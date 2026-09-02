"""Column- and table-level data profiling (Phase 1.1).

Runs on any tabular source without prior knowledge of its schema. Every value
is inspected as text, so the report describes what is *actually in the file*
rather than what pandas guessed on import.

The findings this is built to surface, in rough order of how often they wreck
a downstream dashboard:

  1. one column, several date formats (and day/month order that the data
     itself cannot disambiguate)
  2. nulls disguised as text - "N/A", "-", "unknown", "TBD", ""
  3. numbers stored with thousands separators, currency symbols or percent signs
  4. the same category spelled several ways - "USA" / "usa" / "U.S.A."
  5. mojibake from a mis-declared encoding
  6. duplicate rows, and columns that are constant or unique (candidate keys)
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

# Values that mean "missing" but are stored as text.
SENTINEL_NULLS = {
    "", "-", "--", "?", "??", ".", "n/a", "n.a.", "na", "nan", "none", "null",
    "nil", "nd", "n/d", "#n/a", "#na", "#null!", "#div/0!", "#ref!", "#value!",
    "unknown", "unspecified", "undefined", "missing", "not available",
    "not applicable", "no data", "blank", "empty", "tbd", "tba", "pending",
    "<na>", "<null>", "\\n", "0000-00-00",
}

BOOLEAN_TRUE = {"true", "t", "yes", "y", "1", "on"}
BOOLEAN_FALSE = {"false", "f", "no", "n", "0", "off"}

# Cap on the distinct-value set kept per column for cross-source join checks.
MAX_DISTINCT_SAMPLE = 200

# Ordered: the first format that parses a value wins, so the stricter
# unambiguous patterns are listed before the ambiguous ones.
DATE_FORMATS: list[tuple[str, str]] = [
    ("%Y-%m-%d", "ISO date"),
    ("%Y-%m-%dT%H:%M:%S", "ISO datetime"),
    ("%Y-%m-%d %H:%M:%S", "ISO datetime (space)"),
    ("%Y-%m-%dT%H:%M:%S.%f", "ISO datetime (micro)"),
    ("%Y-%m-%d %H:%M", "ISO datetime (no seconds)"),
    ("%Y/%m/%d", "Y/M/D"),
    ("%Y%m%d", "YYYYMMDD"),
    ("%d-%b-%Y", "D-Mon-Y"),
    ("%d %b %Y", "D Mon Y"),
    ("%d %B %Y", "D Month Y"),
    ("%b %d, %Y", "Mon D, Y"),
    ("%B %d, %Y", "Month D, Y"),
    ("%d.%m.%Y", "D.M.Y (dotted)"),
    ("%m/%d/%Y", "M/D/Y (US)"),
    ("%d/%m/%Y", "D/M/Y (EU)"),
    ("%m/%d/%Y %H:%M", "M/D/Y HH:MM (US)"),
    ("%d/%m/%Y %H:%M", "D/M/Y HH:MM (EU)"),
    ("%m/%d/%Y %H:%M:%S", "M/D/Y HH:MM:SS (US)"),
    ("%d/%m/%Y %H:%M:%S", "D/M/Y HH:MM:SS (EU)"),
    ("%m/%d/%y", "M/D/YY (US, 2-digit year)"),
    ("%d/%m/%y", "D/M/YY (EU, 2-digit year)"),
    ("%m-%d-%Y", "M-D-Y"),
    ("%d-%m-%Y", "D-M-Y"),
    ("%Y-%m", "year-month"),
]

# Literal separators appearing in DATE_FORMATS. Every entry above contains at
# least one of these, except %Y%m%d which is exactly eight digits - so a value
# holding none of them and not being 8 digits cannot be a date. strptime is
# strict about literals, which is what makes the shortcut safe.
_DATE_HINT_CHARS = frozenset("-/. ")

RE_INT = re.compile(r"^[+-]?\d+$")
RE_INT_GROUPED = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+$")
RE_DECIMAL = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d{1,3}(?:,\d{3})+\.\d+)$")
RE_SCIENTIFIC = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")
RE_PERCENT = re.compile(r"^[+-]?\d+(?:\.\d+)?\s*%$")
RE_CURRENCY = re.compile(
    r"^[+-]?[$£€¥₹₩]\s*\d{1,3}(?:[,\s]?\d{3})*(?:\.\d+)?$"
    r"|^\d{1,3}(?:[,\s]?\d{3})*(?:\.\d+)?\s*"
    r"(?:USD|EUR|GBP|INR|JPY|AUD|CAD|\$|£|€|₹)$"
)
RE_PAREN_NEGATIVE = re.compile(r"^\(\s*[$£€₹]?[\d,]+(?:\.\d+)?\s*\)$")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
RE_URL = re.compile(r"^(?:https?://|www\.)\S+$", re.I)
RE_PHONE = re.compile(r"^[+(]?[\d][\d\s().\-]{6,}\d$")
RE_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# Byte sequences that show up when UTF-8 is decoded as cp1252/latin-1.
MOJIBAKE_MARKERS = ("Ã", "Â", "â", "�", "ï»¿")

# A currency/percent/grouping strip, used before numeric coercion.
_STRIP_NUMERIC = str.maketrans(
    "", "", "$£€¥₹₩,%  "
)


def is_sentinel_null(value: str) -> bool:
    return value.strip().lower() in SENTINEL_NULLS


def looks_numeric(value: str) -> bool:
    v = value.strip()
    if RE_PAREN_NEGATIVE.match(v):
        return True
    stripped = v.translate(_STRIP_NUMERIC)
    if not stripped or stripped in {"+", "-", "."}:
        return False
    return bool(
        RE_INT.match(stripped)
        or RE_DECIMAL.match(stripped.replace(",", ""))
        or RE_SCIENTIFIC.match(stripped)
    )


def to_number(value: str) -> float | None:
    """Best-effort numeric coercion of a messy text value."""
    v = value.strip()
    negative = bool(RE_PAREN_NEGATIVE.match(v))
    if negative:
        v = v.strip("()").strip()
    v = v.translate(_STRIP_NUMERIC)
    v = re.sub(r"(?:USD|EUR|GBP|INR|JPY|AUD|CAD)$", "", v, flags=re.I)
    if not v:
        return None
    try:
        num = float(v)
    except ValueError:
        return None
    return -num if negative else num


# Per-value classification is pure, and real columns repeat their values far
# more often than not (193 distinct prices across 180k rows). Memoising turns
# "once per cell" into "once per distinct value" - on the supply-chain dataset
# that is the difference between ~20 minutes and well under one.
_VALUE_CACHE = 1 << 17


@lru_cache(maxsize=_VALUE_CACHE)
def matching_date_formats(value: str) -> tuple[str, ...]:
    """Every format in DATE_FORMATS that parses this value exactly."""
    v = value.strip()
    if not v or len(v) > 40:
        return ()
    # Every entry in DATE_FORMATS needs a separator from this set, except
    # %Y%m%d which is exactly 8 digits. Anything else cannot be a date, so
    # skip 19 strptime attempts per numeric cell.
    if not (_DATE_HINT_CHARS & set(v)):
        if not (len(v) == 8 and v.isdigit()):
            return ()
    hits = []
    for fmt, _label in DATE_FORMATS:
        try:
            datetime.strptime(v, fmt)
        except (ValueError, TypeError):
            continue
        hits.append(fmt)
    return tuple(hits)


@lru_cache(maxsize=_VALUE_CACHE)
def classify_value(value: str) -> str:
    """Single best type tag for one raw text value."""
    v = value.strip()
    if not v:
        return "blank"
    if RE_UUID.match(v):
        return "uuid"
    if RE_EMAIL.match(v):
        return "email"
    if RE_URL.match(v):
        return "url"
    if RE_PERCENT.match(v):
        return "percent"
    if RE_CURRENCY.match(v) or RE_PAREN_NEGATIVE.match(v):
        return "currency"
    if matching_date_formats(v):
        return "date"
    if RE_SCIENTIFIC.match(v):
        return "decimal"
    if RE_INT.match(v) or RE_INT_GROUPED.match(v):
        return "integer"
    if RE_DECIMAL.match(v):
        return "decimal"
    if RE_PHONE.match(v):
        return "phone"
    return "text"


def normalise_category(value: str) -> str:
    """Fold a category label so spelling variants collapse onto one key."""
    v = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    v = v.casefold().strip()
    v = re.sub(r"[\s_\-.]+", "", v)
    return v


def has_mojibake(value: str) -> bool:
    return any(marker in value for marker in MOJIBAKE_MARKERS)


@dataclass
class ColumnProfile:
    name: str
    n_rows: int = 0
    n_blank: int = 0
    n_sentinel: int = 0
    n_present: int = 0
    missing_pct: float = 0.0
    n_distinct: int = 0
    distinct_pct: float = 0.0
    top_values: list[tuple[str, int]] = field(default_factory=list)
    distinct_sample: list[str] = field(default_factory=list)
    inferred_type: str = "unknown"
    type_confidence: float = 0.0
    type_tally: dict[str, int] = field(default_factory=dict)
    n_type_mismatch: int = 0
    mismatch_examples: list[str] = field(default_factory=list)
    date_formats: dict[str, int] = field(default_factory=dict)
    date_order_ambiguous: bool = False
    currency_notations: dict[str, int] = field(default_factory=dict)
    percent_scale_mixed: bool = False
    n_untidy_whitespace: int = 0
    case_variant_groups: dict[str, list[str]] = field(default_factory=dict)
    mojibake_examples: list[str] = field(default_factory=list)
    sentinel_forms: dict[str, int] = field(default_factory=dict)
    numeric: dict[str, Any] | None = None
    outliers: dict[str, Any] | None = None
    is_constant: bool = False
    is_unique: bool = False
    candidate_key: bool = False
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["top_values"] = [list(t) for t in self.top_values]
        return d


def _as_text(series: pd.Series) -> list[str]:
    """Series -> list of raw strings; any flavour of null becomes ''."""
    out: list[str] = []
    for v in series.tolist():
        if v is None:
            out.append("")
            continue
        if isinstance(v, str):
            out.append(v)
            continue
        try:
            if pd.isna(v):
                out.append("")
                continue
        except (TypeError, ValueError):
            pass
        out.append(str(v))
    return out


def _numeric_summary(nums: list[float]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Descriptive stats plus IQR-based outlier bounds."""
    s = pd.Series(nums, dtype="float64")
    q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    mask = (s < lo) | (s > hi)
    summary = {
        "min": float(s.min()), "max": float(s.max()),
        "mean": round(float(s.mean()), 4), "median": float(s.median()),
        "std": round(float(s.std(ddof=0)), 4),
        "p01": float(s.quantile(0.01)), "p99": float(s.quantile(0.99)),
        "n_zero": int((s == 0).sum()), "n_negative": int((s < 0).sum()),
    }
    outliers = {
        "method": "IQR x1.5",
        "lower_bound": round(lo, 4), "upper_bound": round(hi, 4),
        "n_outliers": int(mask.sum()),
        "examples": [float(x) for x in s[mask].head(5)],
    }
    return summary, outliers


def profile_column(name: str, values: list[str], n_rows: int) -> ColumnProfile:
    p = ColumnProfile(name=name, n_rows=n_rows)
    present: list[str] = []

    for v in values:
        stripped = v.strip()
        if not stripped:
            p.n_blank += 1
        elif is_sentinel_null(v):
            p.n_sentinel += 1
            p.sentinel_forms[stripped] = p.sentinel_forms.get(stripped, 0) + 1
        else:
            present.append(v)
            if v != stripped:
                p.n_untidy_whitespace += 1
            if has_mojibake(v) and len(p.mojibake_examples) < 5:
                p.mojibake_examples.append(v)

    p.n_present = len(present)
    missing = p.n_blank + p.n_sentinel
    p.missing_pct = round(100.0 * missing / n_rows, 2) if n_rows else 0.0

    counts = Counter(s.strip() for s in present)
    p.n_distinct = len(counts)
    p.distinct_pct = round(100.0 * p.n_distinct / p.n_present, 2) if p.n_present else 0.0
    p.top_values = counts.most_common(10)
    # Full distinct set for low-cardinality columns: schema_map needs it to
    # test whether two columns can actually be joined.
    if p.n_distinct <= MAX_DISTINCT_SAMPLE:
        p.distinct_sample = sorted(counts)
    p.is_constant = p.n_distinct == 1
    p.is_unique = p.n_present > 0 and p.n_distinct == p.n_present
    p.candidate_key = p.is_unique and missing == 0 and p.n_rows > 1

    if not present:
        p.inferred_type = "empty"
        p.issues.append("column is entirely missing - no usable values")
        return p

    tally = Counter(classify_value(v) for v in present)
    p.type_tally = dict(tally)
    p.inferred_type, dominant_n = tally.most_common(1)[0]
    p.type_confidence = round(100.0 * dominant_n / p.n_present, 2)
    return _finish_column(p, present, tally)


FMT_LABEL: dict[str, str] = dict(DATE_FORMATS)

# Formats that differ *only* in whether the day or the month comes first. Any
# value matching one side and not the other proves that side's order; a value
# matching both proves nothing. Checked as sets so the datetime variants
# ("1/31/2018 22:56") are covered, not just the bare dates.
_DMY_PAIRS = [
    ("%m/%d/%Y", "%d/%m/%Y"),
    ("%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"),
    ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"),
    ("%m/%d/%y", "%d/%m/%y"),
    ("%m-%d-%Y", "%d-%m-%Y"),
]
_US_FMTS = frozenset(us for us, _ in _DMY_PAIRS)
_EU_FMTS = frozenset(eu for _, eu in _DMY_PAIRS)


def _analyse_dates(p: ColumnProfile, present: list[str]) -> None:
    """Tally date formats and decide whether day/month order is recoverable."""
    match_sets = {}
    for v in present:
        hits = matching_date_formats(v)
        if hits:
            match_sets[v] = set(hits)
    if not match_sets:
        return

    us_only = eu_only = both = 0
    for hits in match_sets.values():
        has_us, has_eu = bool(hits & _US_FMTS), bool(hits & _EU_FMTS)
        if has_us and has_eu:
            both += 1
        elif has_us:
            us_only += 1
        elif has_eu:
            eu_only += 1

    # A column is resolved when some value can only be read one way: "1/31" is
    # proof the whole column is M/D/Y. The individually-ambiguous values then
    # belong to that same format rather than to a second one.
    resolved: frozenset[str] | None = None
    if us_only and not eu_only:
        resolved = _US_FMTS
    elif eu_only and not us_only:
        resolved = _EU_FMTS

    tally: Counter[str] = Counter()
    for v, hits in match_sets.items():
        if hits & _US_FMTS and hits & _EU_FMTS:
            if resolved is None:
                tally["D/M/Y or M/D/Y (ambiguous)"] += 1
                continue
            hits = hits & resolved
        first = next(f for f, _ in DATE_FORMATS if f in hits)
        tally[FMT_LABEL[first]] += 1
    p.date_formats = dict(tally.most_common())

    if us_only and eu_only:
        p.date_order_ambiguous = True
        p.issues.append(
            "MIXED day/month order in one column: " + str(us_only) + " value(s) can "
            "only be M/D/Y and " + str(eu_only) + " can only be D/M/Y. Rows were "
            "almost certainly written by different systems - needs a per-row rule, "
            "not one parse format."
        )
    elif both and not us_only and not eu_only:
        p.date_order_ambiguous = True
        p.issues.append(
            "day/month order is UNRESOLVABLE from the data (every value has "
            "day <= 12). Confirm the intended order with the data owner."
        )
    if len(tally) > 1:
        p.issues.append(
            str(len(tally)) + " date formats in one column: " + ", ".join(tally)
        )


NUMERIC_TYPES = {"integer", "decimal", "currency", "percent"}

# Below this many values, IQR outlier bounds are noise rather than signal.
MIN_ROWS_FOR_OUTLIERS = 20

# Types where near-uniqueness suggests an identifier. A high-cardinality
# numeric column is just a measurement, not a key.
KEYLIKE_TYPES = {"text", "categorical", "uuid"}
RE_KEYLIKE_NAME = re.compile(r"(^|_)(id|ids|key|code|no|num|number|ref|sku)$", re.I)

# A value tagged X does not count as a mismatch in a column inferred as Y
# when X is in COMPATIBLE[Y] - "1200" in a currency column is fine.
COMPATIBLE: dict[str, set[str]] = {
    "integer": {"integer", "decimal"},
    "decimal": {"integer", "decimal"},
    "currency": {"currency", "integer", "decimal"},
    "percent": {"percent", "integer", "decimal"},
    "boolean": {"boolean", "integer", "text"},
    "categorical": {"text", "categorical", "integer"},
}


RE_CURRENCY_MARKER = re.compile(r"[$£€¥₹₩]|\b(?:USD|EUR|GBP|INR|JPY|AUD|CAD)\b")


def _currency_notations(present: list[str]) -> Counter[str]:
    syms: Counter[str] = Counter()
    for v in present:
        m = RE_CURRENCY_MARKER.search(v)
        if m:
            syms[m.group(0)] += 1
    return syms


def _finish_column(
    p: ColumnProfile, present: list[str], tally: Counter[str]
) -> ColumnProfile:
    lowered = {v.strip().casefold() for v in present}
    if lowered <= (BOOLEAN_TRUE | BOOLEAN_FALSE):
        p.inferred_type = "boolean"
        p.type_confidence = 100.0
        if p.n_distinct > 2:
            p.issues.append(
                "boolean stored in " + str(p.n_distinct) + " different spellings ("
                + ", ".join(repr(v) for v, _ in p.top_values[:8])
                + ") - collapse to one representation"
            )
        elif lowered <= {"0", "1"}:
            p.issues.append(
                "only 0/1 present - confirm this is a flag and not a small count"
            )

    # Currency is the semantically richer reading of a bare number: any
    # currency-marked value means this is a money column written inconsistently.
    if tally.get("currency") and p.inferred_type in {"decimal", "integer"}:
        numeric_n = sum(tally.get(t, 0) for t in ("currency", "decimal", "integer"))
        p.inferred_type = "currency"
        p.type_confidence = round(100.0 * numeric_n / p.n_present, 2)
    if p.inferred_type == "currency":
        notations = _currency_notations(present)
        p.currency_notations = dict(notations.most_common())
        if len(notations) > 1:
            p.issues.append(
                str(len(notations)) + " currency notations in one column ("
                + ", ".join(repr(k) + " x" + str(v) for k, v in notations.most_common())
                + ") - values are not comparable until a single currency is agreed"
            )
        elif notations and notations.total() < p.n_present:
            p.issues.append(
                str(notations.total()) + " of " + str(p.n_present) + " values carry a "
                "currency symbol and the rest do not - strip to a bare number and "
                "record the currency in its own column"
            )

    if p.inferred_type == "percent":
        bare = tally.get("decimal", 0) + tally.get("integer", 0)
        if bare:
            p.percent_scale_mixed = True
            p.issues.append(
                str(bare) + " value(s) are bare numbers while " + str(tally["percent"])
                + " carry a '%' sign - the two scales differ by a factor of 100. "
                "Pick one before any aggregation."
            )

    if "date" in tally:
        _analyse_dates(p, present)

    if p.inferred_type in NUMERIC_TYPES:
        nums = [n for n in (to_number(v) for v in present) if n is not None]
        if nums:
            summary, outliers = _numeric_summary(nums)
            p.numeric = summary
            # IQR on a handful of rows says nothing; don't manufacture a finding.
            p.outliers = outliers if len(nums) >= MIN_ROWS_FOR_OUTLIERS else None

    allowed = COMPATIBLE.get(p.inferred_type, {p.inferred_type})
    bad = [v for v in present if classify_value(v) not in allowed]
    p.n_type_mismatch = len(bad)
    p.mismatch_examples = list(dict.fromkeys(v.strip() for v in bad))[:8]

    if (
        p.inferred_type == "text"
        and p.n_distinct <= max(50, int(0.05 * p.n_present))
        and p.distinct_pct < 50.0
    ):
        p.inferred_type = "categorical"

    if p.inferred_type in {"categorical", "text"} and p.n_distinct <= 500:
        groups: dict[str, list[str]] = {}
        for raw in {v.strip() for v in present}:
            groups.setdefault(normalise_category(raw), []).append(raw)
        p.case_variant_groups = {
            k: sorted(v) for k, v in groups.items() if len(v) > 1
        }

    return _collect_issues(p)


def _collect_issues(p: ColumnProfile) -> ColumnProfile:
    """Turn the measurements into findings a human can act on."""
    if p.missing_pct >= 50.0:
        p.issues.append(
            "over half the values are missing (" + str(p.missing_pct) + "%) - "
            "decide whether this column is usable at all"
        )
    elif p.missing_pct > 0:
        p.issues.append(str(p.missing_pct) + "% missing")

    if p.sentinel_forms:
        forms = ", ".join(repr(k) for k in sorted(p.sentinel_forms))
        p.issues.append(
            "nulls stored as text in " + str(len(p.sentinel_forms))
            + " form(s): " + forms
        )
    if p.n_type_mismatch:
        p.issues.append(
            str(p.n_type_mismatch) + " value(s) do not fit the inferred type '"
            + p.inferred_type + "': " + ", ".join(repr(x) for x in p.mismatch_examples)
        )
    if p.n_untidy_whitespace:
        p.issues.append(
            str(p.n_untidy_whitespace) + " value(s) carry leading/trailing whitespace"
        )
    if p.case_variant_groups:
        shown = list(p.case_variant_groups.values())[:3]
        p.issues.append(
            str(len(p.case_variant_groups)) + " label(s) spelled inconsistently, e.g. "
            + "; ".join(" / ".join(g) for g in shown)
        )
    if p.mojibake_examples:
        p.issues.append(
            "encoding damage (mojibake) present, e.g. "
            + ", ".join(repr(x) for x in p.mojibake_examples[:3])
        )
    if p.is_constant:
        p.issues.append(
            "constant column - single value " + repr(p.top_values[0][0])
            + "; carries no information for analysis"
        )
    if p.outliers and p.outliers["n_outliers"]:
        share = p.outliers["n_outliers"] / max(p.n_present, 1)
        if share <= 0.10:
            p.issues.append(
                str(p.outliers["n_outliers"]) + " value(s) outside IQR bounds ["
                + str(p.outliers["lower_bound"]) + ", "
                + str(p.outliers["upper_bound"]) + "], e.g. "
                + ", ".join(str(x) for x in p.outliers["examples"][:3])
            )
        else:
            p.issues.append(
                str(round(100 * share, 1)) + "% of values fall outside IQR bounds - "
                "the distribution is heavily skewed or multi-modal, so IQR is the "
                "wrong outlier test here; inspect the distribution before trusting "
                "any average"
            )
    if (
        p.n_present
        and p.distinct_pct >= 90.0
        and not p.is_unique
        and (p.inferred_type in KEYLIKE_TYPES or RE_KEYLIKE_NAME.search(p.name))
    ):
        dups = p.n_present - p.n_distinct
        p.issues.append(
            str(dups) + " duplicate value(s) in an otherwise-unique column ("
            + str(p.distinct_pct) + "% distinct) - this looks like an intended "
            "identifier, so the duplicates must be resolved before it can be a key"
        )
    if p.numeric and p.numeric["n_negative"] and p.inferred_type == "currency":
        p.issues.append(
            str(p.numeric["n_negative"]) + " negative monetary value(s) - "
            "refunds/credits, or a sign error?"
        )
    return p


@dataclass
class TableProfile:
    name: str
    source_path: str
    source_format: str
    sheet: str | None = None
    encoding: str | None = None
    delimiter: str | None = None
    n_rows: int = 0
    n_cols: int = 0
    n_empty_rows: int = 0
    n_exact_duplicate_rows: int = 0
    n_normalised_duplicate_rows: int = 0
    duplicate_examples: list[dict[str, Any]] = field(default_factory=list)
    candidate_keys: list[str] = field(default_factory=list)
    columns: list[ColumnProfile] = field(default_factory=list)
    load_warnings: list[str] = field(default_factory=list)
    table_issues: list[str] = field(default_factory=list)

    @property
    def n_issues(self) -> int:
        return len(self.table_issues) + sum(len(c.issues) for c in self.columns)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "columns"}
        d["columns"] = [c.to_dict() for c in self.columns]
        d["n_issues"] = self.n_issues
        return d


def _normalised_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Whitespace- and case-insensitive view, for near-duplicate detection."""
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        out[col] = [s.strip().casefold() for s in _as_text(df[col])]
    return out


def profile_table(table: Any) -> TableProfile:
    """Profile one LoadedTable (io_utils.LoadedTable)."""
    df = table.df
    tp = TableProfile(
        name=table.name,
        source_path=str(table.source_path),
        source_format=table.source_format,
        sheet=table.sheet,
        encoding=table.encoding,
        delimiter=table.delimiter,
        n_rows=int(df.shape[0]),
        n_cols=int(df.shape[1]),
        load_warnings=list(table.warnings),
    )
    if tp.n_rows == 0:
        tp.table_issues.append("table has no data rows")
        return tp

    text_cols = {col: _as_text(df[col]) for col in df.columns}
    tp.columns = [
        profile_column(str(col), vals, tp.n_rows) for col, vals in text_cols.items()
    ]
    tp.candidate_keys = [c.name for c in tp.columns if c.candidate_key]

    norm = _normalised_frame(df)
    tp.n_empty_rows = int((norm == "").all(axis=1).sum())
    tp.n_exact_duplicate_rows = int(df.duplicated().sum())
    dup_mask = norm.duplicated(keep=False)
    tp.n_normalised_duplicate_rows = int(norm.duplicated().sum())
    if tp.n_normalised_duplicate_rows:
        sample = df[dup_mask].head(4)
        tp.duplicate_examples = [
            {str(k): str(v) for k, v in row.items()}
            for row in sample.to_dict(orient="records")
        ]

    if tp.n_exact_duplicate_rows:
        tp.table_issues.append(
            str(tp.n_exact_duplicate_rows) + " exact duplicate row(s)"
        )
    extra = tp.n_normalised_duplicate_rows - tp.n_exact_duplicate_rows
    if extra > 0:
        tp.table_issues.append(
            str(extra) + " further duplicate row(s) that differ only by case or "
            "whitespace - these survive a naive drop_duplicates()"
        )
    if tp.n_empty_rows:
        tp.table_issues.append(str(tp.n_empty_rows) + " completely empty row(s)")
    if not tp.candidate_keys:
        tp.table_issues.append(
            "no single column is a unique, complete key - a composite key or a "
            "surrogate key will be needed to join or de-duplicate reliably"
        )
    return tp


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(profiles: list[TableProfile], errors: list[str]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: list[str] = [
        "# Data Profiling Report",
        "",
        "Generated " + ts + " by `dtp.profile` (Phase 1.1).",
        "",
    ]
    if errors:
        out += ["## Files that could not be read", ""]
        out += ["- " + _md_escape(e) for e in errors] + [""]

    if not profiles:
        out += [
            "## No sources found",
            "",
            "`data/raw/` contains no readable tabular file. Drop the source data "
            "there and re-run `python -m dtp.cli profile`.",
            "",
        ]
        return "\n".join(out)

    out += [
        "## Sources",
        "",
        "| Table | Rows | Cols | Findings | Duplicate rows | Format |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for tp in profiles:
        out.append(
            "| `" + _md_escape(tp.name) + "` | " + f"{tp.n_rows:,}" + " | "
            + str(tp.n_cols) + " | " + str(tp.n_issues) + " | "
            + str(tp.n_normalised_duplicate_rows) + " | " + tp.source_format + " |"
        )
    out.append("")
    for tp in profiles:
        out += _render_table_section(tp)
    return "\n".join(out)


def _render_table_section(tp: TableProfile) -> list[str]:
    out = ["---", "", "## Table: `" + tp.name + "`", ""]
    meta = ["source: `" + tp.source_path + "`", "format: " + tp.source_format]
    if tp.sheet:
        meta.append("sheet: " + tp.sheet)
    if tp.encoding:
        meta.append("encoding: " + tp.encoding)
    if tp.delimiter:
        meta.append("delimiter: " + repr(tp.delimiter))
    out += ["- " + m for m in meta]
    out += ["- shape: " + f"{tp.n_rows:,}" + " rows x " + str(tp.n_cols) + " cols", ""]

    if tp.load_warnings:
        out += ["**Load warnings**", ""]
        out += ["- " + _md_escape(w) for w in tp.load_warnings] + [""]
    if tp.table_issues:
        out += ["**Table-level findings**", ""]
        out += ["- " + _md_escape(i) for i in tp.table_issues] + [""]
    if tp.candidate_keys:
        out += ["Candidate key(s): " + ", ".join("`" + k + "`" for k in tp.candidate_keys), ""]

    out += [
        "### Columns",
        "",
        "| Column | Inferred type | Conf. | Missing | Distinct | Findings |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for c in tp.columns:
        out.append(
            "| `" + _md_escape(c.name) + "` | " + c.inferred_type + " | "
            + str(c.type_confidence) + "% | " + str(c.missing_pct) + "% | "
            + f"{c.n_distinct:,}" + " | " + str(len(c.issues)) + " |"
        )
    out.append("")

    flagged = [c for c in tp.columns if c.issues]
    if flagged:
        out += ["### Column findings", ""]
        for c in flagged:
            out.append("**`" + c.name + "`** (" + c.inferred_type + ")")
            out.append("")
            out += ["- " + _md_escape(i) for i in c.issues]
            if c.date_formats:
                out.append(
                    "- date formats seen: "
                    + ", ".join(k + " (" + str(v) + ")" for k, v in c.date_formats.items())
                )
            out.append("")
    return out


def run(
    raw_dir: Path | None = None, out_dir: Path | None = None
) -> tuple[list[TableProfile], list[str], dict[str, Path]]:
    """Profile every source under raw_dir and write the report pair."""
    from . import RAW_DIR, REPORTS_DIR
    from .io_utils import load_all

    raw_dir = raw_dir or RAW_DIR
    out_dir = out_dir or REPORTS_DIR / "profiling"

    tables, errors = load_all(raw_dir)
    profiles = [profile_table(t) for t in tables]

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "profiling_report.md"
    json_path = out_dir / "profiling_report.json"
    md_path.write_text(render_markdown(profiles, errors), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "raw_dir": str(raw_dir),
                "unreadable_files": errors,
                "tables": [tp.to_dict() for tp in profiles],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return profiles, errors, {"markdown": md_path, "json": json_path}
