"""Refusals and verification: the two things that hold when the model is wrong.

Roadmap 3.1 asks for a defined fallback per out-of-scope question type, and 3.3 for
*zero* mismatches between a stated number and the query result. Both live here, and
neither depends on the model behaving:

* `Refusal` is a value with a machine-readable `code`. `REFUSALS` names every code
  there is, and a test fails if `agent/` raises one this table does not describe -
  so a new class of unanswerable question cannot be added without its fallback.
* `verify_summary` checks every numeric literal in the model's sentence against the
  frame the answer was computed from. A literal that matches nothing drops the whole
  sentence, not just the figure: prose edited to remove one number still reads as if
  it had been checked.

`docs/03-agent-design.md` (6-12, 19-21) argues the design. This module imports no
model client and makes no network call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from .. import metrics as M

# Every refusal code, with the sentence that opens its fallback. The agent is
# allowed to raise exactly these, and `test_every_refusal_code_is_documented`
# checks this table against the codes the package actually uses and against the
# table in `docs/03-agent-design.md`.
REFUSALS: dict[str, str] = {
    "unknown_metric":
        "That is not a figure this dataset defines.",
    "unknown_dimension":
        "There is no such column in this dataset.",
    "unknown_value":
        "That value does not appear in the data.",
    "empty_result":
        "Nothing in this snapshot matches that.",
    "personal_data":
        "Customer names, street addresses and client IPs are in this data and are "
        "deliberately not available to query.",
    "out_of_window":
        "That period is outside the data.",
    "funnel_window":
        "Page views only exist for part of the order history.",
    "causal":
        "This platform reports what happened, not why.",
    "forecast":
        "Nothing here is forecast: no model is fitted to this data.",
    "write":
        "Nothing can change the data through this interface.",
    "raw_sql":
        "SQL is not accepted here, by design.",
    "unparseable":
        "I could not turn that into a question about this dataset.",
}


@dataclass(frozen=True)
class Refusal:
    """Why a question was not answered, and what to ask instead.

    A value rather than a bare string so the CLI, the dashboard and the tests all
    read the same fields, and so `code` can be asserted on without matching prose.
    """

    code: str
    detail: str = ""
    suggestions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in REFUSALS:
            raise ValueError(
                "undocumented refusal code " + repr(self.code) + ". Add it to "
                "REFUSALS and to the table in docs/03-agent-design.md, or reuse "
                "one of: " + ", ".join(sorted(REFUSALS)))

    @property
    def message(self) -> str:
        parts = [REFUSALS[self.code]]
        if self.detail:
            parts.append(self.detail)
        if self.suggestions:
            parts.append("Try: " + "; ".join(self.suggestions) + ".")
        return " ".join(parts)


class PlanError(Exception):
    """A refusal raised where an exception is the natural control flow.

    `ask()` catches it and returns the refusal as part of the answer, because a
    question this platform cannot answer is a normal outcome, not a failure.
    """

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


def refuse(code: str, detail: str = "",
           suggestions: tuple[str, ...] | list[str] = ()) -> PlanError:
    return PlanError(Refusal(code=code, detail=detail,
                             suggestions=tuple(suggestions)))


# --------------------------------------------------------------------------- #
# the scope screen
#
# These patterns are **precision-first**, not a classifier. The guarantee that
# matters is structural and lives elsewhere: a personal column is not a registry
# dimension so no plan can name one, the warehouse is views over Parquet so
# nothing can write, and every number rendered comes from the executed frame. The
# screen exists only so that a question of these kinds gets the explanation it
# deserves instead of an empty result or a confusing aggregate. Anything it misses
# falls through to `unknown_metric` / `unknown_dimension`, which is a worse message
# and an equally safe outcome - so a missed pattern costs clarity, never safety.
# --------------------------------------------------------------------------- #

SCOPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("personal_data", r"customer'?s? (name|names|email|address|street|password)"),
    ("personal_data", r"\b(first|last|full) names?\b"),
    ("personal_data", r"\bwhich customers?\b|\btop \d+ customers?\b"),
    ("personal_data", r"\bwho (are|is) (our|the) (top|best|biggest|largest)"),
    # `ips?` on its own, because "which IP hit us most" is the question this row
    # exists for and it says neither "address" nor "client". `\b` keeps "zip" out.
    ("personal_data", r"\b(ips?|ip address|client ip|email address|password)\b"),
    ("forecast", r"\b(forecast|predict|prediction|projected|extrapolat)"),
    ("forecast", r"\bnext (month|quarter|year|week)\b"),
    ("causal", r"\bwhy\b|\broot cause\b|\bwhat caused\b|\bwhat drove\b"),
    # A verb, then up to two words, then the thing being written to. The words in
    # between are what "delete the cancelled rows" needs and what an earlier
    # `\w*(row|...)` could not span, since `\w` does not cross a space.
    # `lines` is deliberately *not* a noun here: "drop groups under 100 lines" is a
    # thin-group control, and a false positive costs an answerable question.
    ("write", r"\b(delete|remove|drop|truncate|wipe|purge)\s+"
              r"(?:(?:the|all|these|those|every|any)\s+)?(?:\w+\s+){0,2}"
              r"(rows?|tables?|records?|orders?|snapshots?|data)\b"),
    ("write", r"\b(update|modify|overwrite|insert into|set)\s+"
              r"(?:(?:the|all|these|those|our|my)\s+)?(?:\w+\s+){0,2}"
              r"(targets?|budgets?|status|prices?|values?|rows?|records?|orders?|"
              r"tables?|columns?|order_items|data)\b"),
    ("raw_sql", r"\bselect\b.{0,80}\bfrom\b|\bgroup by\b|\bsql\b"),
)


def scope_screen(question: str) -> Refusal | None:
    """The first thing `ask()` does, before a token is sent anywhere.

    Returns the refusal for the first pattern that matches, or None. Order in
    `SCOPE_PATTERNS` is the tie-break, and personal data comes first on purpose:
    "who are our top customers by name" is both a naming question and a ranking,
    and the naming half is the one that must not be answered.
    """
    text = question.lower()
    for code, pattern in SCOPE_PATTERNS:
        if re.search(pattern, text):
            return Refusal(code=code, detail=_scope_detail(code),
                           suggestions=_scope_suggestions(code))
    return None


# What each screened refusal offers instead. These are the "defined fallback"
# roadmap 3.1 asks for: a refusal that names nothing askable is a dead end, and a
# dead end is what makes people stop asking.
_SCOPE_DETAIL: dict[str, str] = {
    "personal_data":
        "Summarising a segment and naming a person are different acts, and this "
        "platform only does the first. An operator who needs the second has the "
        "snapshot and SQL.",
    "forecast":
        "What is here is the trend that happened, which you can have at year, "
        "quarter or month grain.",
    "causal":
        "It can show what moved and by how much, and it will say when a "
        "comparison is not safe to make - but the reason is yours to supply.",
    "write":
        "The warehouse is read-only views over Parquet files; a snapshot is "
        "published by the pipeline, never edited.",
    "raw_sql":
        "Every answer here comes from the metric registry, so a figure carries "
        "its gate with it. For ad-hoc SQL, open the snapshot Parquet in DuckDB.",
}

# What each screened refusal offers instead. Roadmap 3.1 asks for a defined fallback
# per out-of-scope type, and the only fallback worth having is one the platform can
# actually answer: whatever follows "by" here is a registry metric or dimension, and a
# test checks that. `city` was in this table once, and a refusal that suggests a
# grouping no plan can express is worse than one that suggests nothing.
_SCOPE_SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "personal_data": ("revenue by customer segment",
                      "orders by country",
                      "revenue by product"),
    "forecast": ("monthly revenue for the last year",
                 "revenue by quarter",
                 "revenue this month against last month"),
    "causal": ("monthly revenue by department",
               "the biggest movers month over month",
               "margin by category"),
    "write": ("revenue by order status",
              "the lines the revenue gate excludes"),
    "raw_sql": ("revenue and margin by market",
                "on-time rate by shipping mode"),
}


def _scope_detail(code: str) -> str:
    return _SCOPE_DETAIL.get(code, "")


def _scope_suggestions(code: str) -> tuple[str, ...]:
    return _SCOPE_SUGGESTIONS.get(code, ())


# --------------------------------------------------------------------------- #
# the numeric verifier
#
# Layer 2 of the hallucination guardrail (`docs/03-agent-design.md` 10-12). Layer
# 1 is that no figure the model emits is ever *rendered*: tiles, axes and tables
# are built from the frame in code. This layer is about the one sentence the model
# is asked for, and its rule is blunt on purpose - a literal that matches nothing
# in the frame drops the whole sentence, because prose edited to remove one number
# still reads as if it had been checked.
# --------------------------------------------------------------------------- #

# A numeric literal as a model writes one. The scale words come before the single
# letters so "million" is not read as "m" plus "illion", and the letter forms are
# followed by (?![a-z]) so the "m" of "markets" is not a scale.
_NUMBER = re.compile(r"""
    (?P<neg>-|minus\s)?
    (?P<currency>\$\s?)?
    (?P<sign>-)?
    (?P<digits>\d{1,3}(?:,\d{3})+|\d+)
    (?:\.(?P<frac>\d+))?
    \s?
    (?:(?P<scale>billion|million|thousand|bn|[kmb])(?![a-z]))?
    \s?
    (?P<unit>%|pp\b|percentage\spoints?|points?|pts?|days?)?
""", re.VERBOSE | re.IGNORECASE)

# Period references, blanked before the numbers are read. A date is not a claim
# about a value, and the window a plan ran over is set in code and stated by
# `Plan.describe()`, so nothing is lost by not checking its digits here. The cost
# is a false negative on a count that happens to equal a year, which is why the
# order of these two is: correctness of the *window* is structural, prose about it
# is not checked.
_PERIOD = re.compile(r"""
    \d{4}-\d{2}(-\d{2})?
  | \bq[1-4]\b(\s\d{4})?
  | \bh[12]\b\s\d{4}
  | \b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s?\d{0,4}
  | \b(19|20)\d{2}\b
""", re.VERBOSE | re.IGNORECASE)

# "the top 5 categories" is a claim about how many rows were shown, not about a
# value, so an integer in this position is checked against the frame's length.
_TOP_N = re.compile(r"\b(top|first|last|bottom|best|worst|highest|lowest)"
                    r"\s(?:\w+\s)?$", re.IGNORECASE)

_SCALES: dict[str, float] = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}

# Which candidate kinds a decorated literal is allowed to match. A bare number is
# ambiguous ("35.2 million" is money here, "5" is a row count) so it matches
# anything; a decorated one is not, and holding it to its own kind is what stops
# a fabricated dollar figure from being waved through by a coincidental ratio.
_KINDS: dict[str, frozenset[str]] = {
    "money": frozenset({"money"}),
    "percent": frozenset({"percent"}),
    "days": frozenset({"days", "count"}),
    # A bare integer under 100 is a count or a claim about shape - "3 markets",
    # "12 late days". Holding it to those two pools is the difference between
    # checking it and waving it through: with a tolerance of half a unit, a pool
    # that also held percentage-point differences would accept almost any of them.
    "integer": frozenset({"count", "shape"}),
    "": frozenset({"money", "percent", "days", "count", "shape"}),
}

# A metric's registry unit, mapped to the candidate kind a literal is matched
# against. `ratio` joins `count` because both print as a bare number.
_UNIT_KIND: dict[str, str] = {
    M.MONEY: "money", M.PERCENT: "percent", M.DAYS: "days",
    M.COUNT: "count", M.RATIO: "count",
}

_PAIR_ROWS = 30          # rows entering the pairwise derivations
_SMALL = 100             # below this, an undecorated integer is a count, not a rate


def _kind_of(column: str) -> str:
    """The candidate kind for a frame column, by its registry unit."""
    try:
        met = M.metric(column)
        return _UNIT_KIND.get(met.unit, "count")
    except KeyError:
        return "count"       # n_lines, and anything the executor adds


def _candidates(frame: pd.DataFrame) -> dict[str, set[float]]:
    """Every number a truthful sentence about this frame could contain."""
    out: dict[str, set[float]] = {
        "money": set(), "percent": set(), "days": set(),
        "count": set(), "shape": set()}
    numeric = [c for c in frame.columns
               if pd.api.types.is_numeric_dtype(frame[c])]
    for column in numeric:
        kind = _kind_of(column)
        series = pd.to_numeric(frame[column], errors="coerce").dropna()
        values = [float(v) for v in series]
        if not values:
            continue
        out[kind].update(values)
        out[kind].add(sum(values))
        out[kind].add(sum(values) / len(values))
        out[kind].add(max(values) - min(values))
        head = values[:_PAIR_ROWS]
        total = sum(head)
        running = 0.0
        for i, a in enumerate(head):
            running += a
            out[kind].add(running)
            if total:
                out["percent"].add(100.0 * a / total)
            for b in head[i + 1:]:
                out[kind].add(abs(a - b))
                if b:
                    out["percent"].add(100.0 * a / b)
                    out["percent"].add(100.0 * (a / b - 1.0))
                if a:
                    out["percent"].add(100.0 * b / a)
                    out["percent"].add(100.0 * (b / a - 1.0))
    rows = len(frame)
    out["shape"].add(float(rows))
    return out


_MIN_LABEL = 3           # shorter than this is not distinctive enough to match on


def _labels(frame: pd.DataFrame) -> list[str]:
    """Every group name this frame legitimately mentions, longest first."""
    cat = M.get_active_catalog()
    dims = cat.dimensions if cat else M.DIMENSIONS
    out: set[str] = set()
    for column in frame.columns:
        if column not in dims:
            continue
        for value in frame[column].dropna().unique():
            out.add(M.fmt_dim(column, value))
            out.add(str(value))
    return sorted((s for s in out if len(s) >= _MIN_LABEL),
                  key=len, reverse=True)


def _blank_labels(text: str, labels: list[str]) -> str:
    """Remove group names before the numbers are read.

    A label is verified by *membership*, not by value, so the digits inside one
    must not be re-checked as a figure: "Q3 2017" holds 3 and 2017, and neither is
    a claim about revenue. Longest first, so blanking `Jan 2018` does not leave a
    stray `2018` behind.
    """
    for label in labels:
        text = re.sub(re.escape(label), " ", text, flags=re.IGNORECASE)
    return text


def _label_spans(text: str, labels: list[str]) -> list[tuple[int, int]]:
    """Where the frame's own group names sit in the sentence.

    Read by the fabrication check, which asks one question of every hit: is it
    *inside* a name this frame does carry? Overlapping spans are all kept, because
    the drill paths overlap by design - `Apparel` is a department and
    `Women's Apparel` a category, and one contains the other as text.
    """
    spans: list[tuple[int, int]] = []
    for label in labels:
        spans += [m.span() for m in
                  re.finditer(re.escape(label), text, flags=re.IGNORECASE)]
    return spans


@dataclass(frozen=True)
class Verification:
    """The outcome of checking one sentence against one frame.

    `checked` is recorded because zero is a meaningful answer: a sentence with no
    figures in it passes without having been tested, and a caller that wants to
    know the difference between "verified" and "nothing to verify" can.
    """

    ok: bool
    problems: tuple[str, ...] = ()
    checked: int = 0

    @property
    def note(self) -> str:
        if self.ok:
            return ""
        return "model summary withheld (unverifiable: " + \
               ", ".join(self.problems) + ")"


def _literal(match: re.Match[str]) -> tuple[float, float, str, bool]:
    """One regex hit as (value, tolerance, kind, signed)."""
    digits = match.group("digits").replace(",", "")
    frac = match.group("frac") or ""
    value = float(digits + ("." + frac if frac else ""))
    scale = _SCALES.get((match.group("scale") or "").lower(), 1.0)
    value *= scale
    signed = bool(match.group("neg") or match.group("sign"))
    if signed:
        value = -value
    # The tolerance is the formatter's, not a guess: half a unit in the last place
    # the model chose to print. "$35.21M" is therefore satisfied by anything within
    # $5,000 of it, and "10.8%" by 10.7999.
    tolerance = 0.5 * (10.0 ** -len(frac)) * scale + 1e-9
    unit = (match.group("unit") or "").lower()
    if match.group("currency"):
        kind = "money"
    elif unit.startswith(("%", "pp", "percentage", "point", "pt")):
        kind = "percent"
    elif unit.startswith("day"):
        kind = "days"
    elif not frac and not match.group("scale") and abs(value) < _SMALL:
        kind = "integer"
    else:
        kind = ""
    return value, tolerance, kind, signed


def verify_summary(text: str, frame: pd.DataFrame,
                  known_labels: tuple[str, ...] | list[str] = ()
                  ) -> Verification:
    """Check every figure and every group name in a model's sentence.

    Returns a `Verification`; the caller substitutes the computed `insights` line
    when `ok` is False rather than editing the prose (design reason 11).

    Two checks, and the asymmetry between them is deliberate:

    * **numbers** are matched against `_candidates(frame)`, which is permissive by
      construction - it holds derived readings as well as raw values, so a true
      sentence is not dropped for saying "38% of revenue" about a money column.
      Permissive is the right error here because layer 1 already guarantees no
      model figure is *rendered*; this layer is about the prose beside it.
    * **labels** are matched the other way round, strictly: a name that belongs to
      one of this plan's dimensions but is absent from this frame is a mismatch,
      because "Africa leads" about a frame with no Africa row is fabrication even
      though every number in the sentence may be real. The one allowance is
      containment - a name that appears only *inside* a name the frame does carry is
      not a claim of its own, since the drill paths overlap as text.
    """
    labels = _labels(frame)
    problems: list[str] = []
    lower = text.lower()
    spans = _label_spans(text, labels)
    for label in known_labels:
        if len(label) < _MIN_LABEL or label in labels:
            continue
        for hit in re.finditer(r"\b" + re.escape(label.lower()) + r"\b", lower):
            # "Women's Apparel takes 74% of revenue" quotes the category the frame
            # holds; the `Apparel` department inside it is not a group the sentence
            # invented. The reverse still fails, which is the case worth keeping: a
            # frame grouped by department, described in categories, is a sentence
            # about rows nobody was shown.
            if not any(a <= hit.start() and hit.end() <= b for a, b in spans):
                problems.append(label)
                break

    pool = _candidates(frame)
    checked = 0
    scanned = _PERIOD.sub(" ", _blank_labels(text, labels))
    for match in _NUMBER.finditer(scanned):
        value, tolerance, kind, signed = _literal(match)
        checked += 1
        if (_TOP_N.search(scanned[max(0, match.start() - 24):match.start()])
                and value == int(value) and 0 < value <= len(frame)):
            continue
        # An unsigned literal is matched on magnitude: prose carries direction in
        # words ("a loss of $40.00", "6.7 points below"), which this does not parse.
        targets = (value,) if signed else (value, -value)
        allowed = _KINDS[kind]
        if not any(abs(t - c) <= tolerance
                   for t in targets for k in allowed for c in pool[k]):
            problems.append(match.group(0).strip())
    return Verification(ok=not problems, problems=tuple(problems),
                        checked=checked)


