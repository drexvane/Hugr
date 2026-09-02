"""Cross-source schema reconciliation (Phase 1.1).

Answers three questions the roadmap's "schema inconsistency matrix" asks:

  1. which columns in different sources mean the same thing but are named
     differently  (`region` vs `Region_Name`)
  2. where two such columns disagree on type or format, and what the
     canonical form should be
  3. whether a pair can actually be joined - matching names prove nothing if
     one side holds "USA" and the other holds "US"

That third check is the one that quietly breaks dashboards, so it runs on
value overlap rather than on names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .profile import TableProfile, normalise_category

# Decorative suffixes that carry no meaning for matching purposes.
NOISE_TOKENS = {
    "name", "names", "desc", "description", "txt", "text", "value", "val",
    "code", "cd", "field", "col", "column", "the", "of", "type",
}

RE_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenise(name: str) -> list[str]:
    """'Region_Name' / 'regionName' / 'REGION NAME' -> ['region', 'name']."""
    s = RE_CAMEL_BOUNDARY.sub(" ", str(name))
    parts = re.split(r"[\s_\-.]+", s)
    out: list[str] = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        if len(p) > 3 and p.endswith("s") and not p.endswith("ss"):
            p = p[:-1]          # crude singularisation
        out.append(p)
    return out


def canonical_name(name: str) -> str:
    return "_".join(tokenise(name))


def meaning_tokens(name: str) -> set[str]:
    toks = set(tokenise(name))
    stripped = toks - NOISE_TOKENS
    return stripped or toks       # never return empty


def naming_convention(name: str) -> str:
    """Which convention a column name follows.

    A single-word name follows no multi-word convention, so it is reported as
    'single-word' and excluded from mixed-convention findings - otherwise every
    table with both `region` and `order_date` looks inconsistent when it is not.
    """
    s = str(name).strip()
    if not s:
        return "empty"
    if " " in s:
        return "spaced"
    if "-" in s:
        return "kebab-case"
    if len(tokenise(s)) < 2:
        return "single-word"
    if s.isupper():
        return "SCREAMING_CASE"
    if "_" in s and s.islower():
        return "snake_case"
    if "_" in s and s[:1].isupper():
        return "Mixed_Pascal_Snake"
    if s[:1].isupper():
        return "PascalCase"
    if any(ch.isupper() for ch in s):
        return "camelCase"
    return "other"


STRUCTURAL_CONVENTIONS = {
    "spaced", "kebab-case", "SCREAMING_CASE", "snake_case",
    "Mixed_Pascal_Snake", "PascalCase", "camelCase",
}


def name_similarity(a: str, b: str) -> float:
    """Jaccard over meaning tokens, with a boost when one side contains the other."""
    ta, tb = meaning_tokens(a), meaning_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    score = inter / union
    if ta <= tb or tb <= ta:
        score = max(score, 0.75)
    if canonical_name(a) == canonical_name(b):
        score = 1.0
    return round(score, 3)


NAME_MATCH_THRESHOLD = 0.5

# Types that describe the same shape of data closely enough to join.
TYPE_FAMILY = {
    "integer": "number", "decimal": "number", "currency": "number",
    "percent": "number", "date": "date", "boolean": "boolean",
    "text": "label", "categorical": "label", "uuid": "label",
    "email": "label", "url": "label", "phone": "label",
    "empty": "unknown", "unknown": "unknown",
}


@dataclass
class ColumnRef:
    table: str
    column: str
    inferred_type: str
    n_distinct: int
    missing_pct: float
    distinct_sample: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return self.table + "." + self.column


@dataclass
class Match:
    left: ColumnRef
    right: ColumnRef
    name_score: float
    type_agreement: bool
    value_overlap: float | None = None
    unmatched_left: list[str] = field(default_factory=list)
    unmatched_right: list[str] = field(default_factory=list)
    verdict: str = ""
    proposed_name: str = ""
    proposed_type: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["left"] = self.left.ref
        d["right"] = self.right.ref
        return d


def _overlap(a: ColumnRef, b: ColumnRef) -> tuple[float | None, list[str], list[str]]:
    """Fraction of the smaller distinct set present in the larger, after folding."""
    if not a.distinct_sample or not b.distinct_sample:
        return None, [], []
    fa = {normalise_category(v): v for v in a.distinct_sample}
    fb = {normalise_category(v): v for v in b.distinct_sample}
    if not fa or not fb:
        return None, [], []
    shared = set(fa) & set(fb)
    frac = len(shared) / min(len(fa), len(fb))
    only_a = sorted(fa[k] for k in set(fa) - shared)[:8]
    only_b = sorted(fb[k] for k in set(fb) - shared)[:8]
    return round(frac, 3), only_a, only_b


SPECIFICITY = ["unknown", "empty", "text", "categorical", "boolean", "integer",
               "decimal", "percent", "currency", "date", "email", "url", "uuid"]


def _concept_name(name: str) -> str:
    """snake_case name with decorative tokens dropped: 'Region_Name' -> 'region'."""
    toks = tokenise(name)
    kept = [t for t in toks if t not in NOISE_TOKENS]
    return "_".join(kept or toks)


def _preferred_name(a: str, b: str) -> str:
    """The more descriptive of two names, reduced to its concept and snake_cased."""
    ta, tb = meaning_tokens(a), meaning_tokens(b)
    if len(ta) != len(tb):
        winner = a if len(ta) > len(tb) else b
    elif naming_convention(a) == "snake_case":
        winner = a
    elif naming_convention(b) == "snake_case":
        winner = b
    else:
        winner = min(a, b)
    return _concept_name(winner)


def _classify(m: Match) -> None:
    if m.value_overlap is None:
        m.verdict = "name match; join compatibility untested (cardinality too high)"
    elif m.value_overlap >= 0.8:
        m.verdict = "same concept, directly joinable"
    elif m.value_overlap >= 0.2:
        m.verdict = "same concept, partial value overlap - needs a mapping table"
        m.notes.append(
            "values only in " + m.left.ref + ": " + ", ".join(m.unmatched_left)
            + " | only in " + m.right.ref + ": " + ", ".join(m.unmatched_right)
        )
    else:
        m.verdict = "NOT joinable as-is - different vocabularies"
        m.notes.append(
            "e.g. " + m.left.ref + " holds " + ", ".join(m.left.distinct_sample[:3])
            + " while " + m.right.ref + " holds "
            + ", ".join(m.right.distinct_sample[:3])
            + " - a lookup/crosswalk is required, not a rename"
        )
        # Same concept, different vocabulary: these are two columns, not one.
        # Keep the full canonical names so the distinguishing token survives.
        m.proposed_name = (
            "keep both: " + canonical_name(m.left.column) + " + "
            + canonical_name(m.right.column) + " (join via crosswalk)"
        )
    if not m.type_agreement:
        m.notes.append(
            "type conflict: " + m.left.ref + " is " + m.left.inferred_type + ", "
            + m.right.ref + " is " + m.right.inferred_type
            + " - cast both to one type in the clean schema"
        )


@dataclass
class SchemaMatrix:
    tables: list[str] = field(default_factory=list)
    conventions: dict[str, dict[str, int]] = field(default_factory=dict)
    matches: list[Match] = field(default_factory=list)
    single_source_columns: dict[str, list[str]] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": self.tables,
            "naming_conventions": self.conventions,
            "matches": [m.to_dict() for m in self.matches],
            "single_source_columns": self.single_source_columns,
            "findings": self.findings,
        }


def build_matrix(profiles: list[TableProfile]) -> SchemaMatrix:
    mx = SchemaMatrix(tables=[tp.name for tp in profiles])

    refs: dict[str, list[ColumnRef]] = {}
    for tp in profiles:
        refs[tp.name] = [
            ColumnRef(
                table=tp.name,
                column=c.name,
                inferred_type=c.inferred_type,
                n_distinct=c.n_distinct,
                missing_pct=c.missing_pct,
                distinct_sample=list(c.distinct_sample),
            )
            for c in tp.columns
        ]
        conv: dict[str, int] = {}
        for c in tp.columns:
            k = naming_convention(c.name)
            conv[k] = conv.get(k, 0) + 1
        mx.conventions[tp.name] = conv
        structural = {k: v for k, v in conv.items() if k in STRUCTURAL_CONVENTIONS}
        if len(structural) > 1:
            mx.findings.append(
                "`" + tp.name + "` mixes " + str(len(structural))
                + " naming conventions ("
                + ", ".join(k + " x" + str(v) for k, v in structural.items())
                + ") - standardise before building the clean schema"
            )

    dominant = {}
    for t, conv in mx.conventions.items():
        structural = {k: v for k, v in conv.items() if k in STRUCTURAL_CONVENTIONS}
        if structural:
            dominant[t] = max(structural, key=structural.get)
    if len(set(dominant.values())) > 1:
        mx.findings.append(
            "sources disagree on naming convention ("
            + ", ".join(t + ": " + c for t, c in sorted(dominant.items()))
            + ") - the clean schema should standardise on snake_case"
        )

    matched: set[str] = set()
    names = list(refs)
    for i, ta in enumerate(names):
        for tb in names[i + 1:]:
            for a in refs[ta]:
                for b in refs[tb]:
                    score = name_similarity(a.column, b.column)
                    if score < NAME_MATCH_THRESHOLD:
                        continue
                    overlap, only_a, only_b = _overlap(a, b)
                    m = Match(
                        left=a, right=b, name_score=score,
                        type_agreement=(
                            TYPE_FAMILY.get(a.inferred_type, "unknown")
                            == TYPE_FAMILY.get(b.inferred_type, "unknown")
                        ),
                        value_overlap=overlap,
                        unmatched_left=only_a, unmatched_right=only_b,
                        proposed_name=_preferred_name(a.column, b.column),
                    )
                    m.proposed_type = max(
                        (a.inferred_type, b.inferred_type),
                        key=lambda t: SPECIFICITY.index(t) if t in SPECIFICITY else 0,
                    )
                    _classify(m)
                    mx.matches.append(m)
                    matched.add(a.ref)
                    matched.add(b.ref)

    mx.matches.sort(key=lambda m: (-m.name_score, m.left.ref))
    for t, cols in refs.items():
        solo = [c.column for c in cols if c.ref not in matched]
        if solo:
            mx.single_source_columns[t] = solo
    for m in mx.matches:
        if m.verdict.startswith("NOT joinable"):
            mx.findings.append(
                m.left.ref + " <-> " + m.right.ref + ": " + m.verdict
            )
    return mx


def render_markdown(mx: SchemaMatrix) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = [
        "# Schema Inconsistency Matrix",
        "",
        "Generated " + ts + " by `dtp.schema_map` (Phase 1.1).",
        "",
    ]
    if len(mx.tables) < 2:
        out += [
            "Only " + str(len(mx.tables)) + " source present, so there is nothing to "
            "reconcile across sources yet. Naming conventions are still reported below.",
            "",
        ]

    if mx.findings:
        out += ["## Findings", ""] + ["- " + f for f in mx.findings] + [""]

    out += ["## Naming conventions by source", "",
            "| Source | Conventions used |", "|---|---|"]
    for t, conv in mx.conventions.items():
        out.append(
            "| `" + t + "` | "
            + ", ".join(k + " x" + str(v) for k, v in conv.items()) + " |"
        )
    out.append("")

    if mx.matches:
        out += [
            "## Cross-source column matches",
            "",
            "`Value overlap` is the share of the smaller distinct-value set that "
            "also appears on the other side, compared case- and punctuation-"
            "insensitively. A high name score with a low overlap means the columns "
            "mean the same thing but speak different vocabularies.",
            "",
            "| Left | Right | Name score | Types | Value overlap | Proposed name | Proposed type | Verdict |",
            "|---|---|---:|---|---:|---|---|---|",
        ]
        for m in mx.matches:
            ov = "n/a" if m.value_overlap is None else str(round(100 * m.value_overlap)) + "%"
            types = m.left.inferred_type + " / " + m.right.inferred_type
            if not m.type_agreement:
                types = "**" + types + "**"
            out.append(
                "| `" + m.left.ref + "` | `" + m.right.ref + "` | "
                + str(m.name_score) + " | " + types + " | " + ov + " | `"
                + m.proposed_name + "` | " + m.proposed_type + " | " + m.verdict + " |"
            )
        out.append("")
        detailed = [m for m in mx.matches if m.notes]
        if detailed:
            out += ["### Notes", ""]
            for m in detailed:
                out.append("**`" + m.left.ref + "` <-> `" + m.right.ref + "`**")
                out.append("")
                out += ["- " + n for n in m.notes] + [""]
    else:
        out += ["## Cross-source column matches", "",
                "No column pairs cleared the name-similarity threshold of "
                + str(NAME_MATCH_THRESHOLD) + ".", ""]

    if mx.single_source_columns:
        out += ["## Columns present in only one source", ""]
        for t, cols in mx.single_source_columns.items():
            out.append("- `" + t + "`: " + ", ".join("`" + c + "`" for c in cols))
        out.append("")
    return "\n".join(out)


def run(
    profiles: list[TableProfile], out_dir: Path | None = None
) -> tuple[SchemaMatrix, dict[str, Path]]:
    from . import REPORTS_DIR

    out_dir = out_dir or REPORTS_DIR / "schema"
    out_dir.mkdir(parents=True, exist_ok=True)
    mx = build_matrix(profiles)
    md_path = out_dir / "schema_matrix.md"
    json_path = out_dir / "schema_matrix.json"
    md_path.write_text(render_markdown(mx), encoding="utf-8")
    json_path.write_text(
        json.dumps(mx.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    return mx, {"markdown": md_path, "json": json_path}
