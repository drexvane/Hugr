"""Data-quality risk register (Phase 1.1, "flag data quality risks").

Turns the profiling and schema output into a ranked, stakeholder-readable list.
Severity answers one question: what does this defect do to a number on the
dashboard if nobody fixes it?

  BLOCKER  produces a confidently wrong number - worse than a missing one
  HIGH     distorts totals or breaks a join; visible to users
  MEDIUM   degrades grouping, labels or trust, but totals survive
  LOW      cosmetic or informational
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .profile import ColumnProfile, TableProfile
from .schema_map import SchemaMatrix

SEVERITIES = ["BLOCKER", "HIGH", "MEDIUM", "LOW"]


@dataclass
class Risk:
    severity: str
    where: str
    finding: str
    impact: str
    action: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RiskRegister:
    risks: list[Risk] = field(default_factory=list)
    n_tables: int = 0
    n_columns: int = 0
    n_rows: int = 0

    def add(self, *args: str) -> None:
        self.risks.append(Risk(*args))

    def by_severity(self, sev: str) -> list[Risk]:
        return [r for r in self.risks if r.severity == sev]

    @property
    def counts(self) -> dict[str, int]:
        return {s: len(self.by_severity(s)) for s in SEVERITIES}

    @property
    def go_no_go(self) -> str:
        c = self.counts
        if c["BLOCKER"]:
            return (
                "NOT READY - " + str(c["BLOCKER"]) + " blocker(s) would put wrong "
                "numbers on the dashboard. Resolve these before Phase 2 starts."
            )
        if c["HIGH"] >= 5:
            return (
                "PROCEED WITH CARE - no blockers, but " + str(c["HIGH"])
                + " high-severity defects need fixing during Phase 1.2."
            )
        return "READY - defects found are containable inside the cleaning step."

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scope": {
                "tables": self.n_tables,
                "columns": self.n_columns,
                "rows": self.n_rows,
            },
            "counts": self.counts,
            "verdict": self.go_no_go,
            "risks": [r.to_dict() for r in self.risks],
        }


def _column_risks(reg: RiskRegister, table: str, c: ColumnProfile) -> None:
    at = table + "." + c.name

    if c.date_order_ambiguous:
        reg.add(
            "BLOCKER", at,
            "day/month order cannot be determined from the values themselves",
            "every trend line, month-over-month comparison and date filter is "
            "silently wrong for the affected rows - and looks plausible",
            "confirm the intended order with the source owner; if the rows came "
            "from different systems, parse per source rather than per column",
        )
    if len(c.currency_notations) > 1:
        reg.add(
            "BLOCKER", at,
            "values carry " + str(len(c.currency_notations)) + " currency notations ("
            + ", ".join(c.currency_notations) + ")",
            "any SUM or AVG adds different currencies together, overstating or "
            "understating revenue with no visible error",
            "store a bare numeric amount plus a separate currency column, and "
            "convert with a dated FX rate before aggregating",
        )
    if c.percent_scale_mixed:
        reg.add(
            "BLOCKER", at,
            "percentages mix '10%' notation with bare fractions like 0.1",
            "the two scales differ by 100x, so discounts and rates are wrong by "
            "two orders of magnitude for part of the data",
            "pick one scale (recommend a 0-1 fraction internally, formatted as % "
            "at display time) and convert the other on ingest",
        )

    mismatch_share = c.n_type_mismatch / c.n_present if c.n_present else 0.0
    if mismatch_share > 0.20:
        reg.add(
            "BLOCKER", at,
            str(c.n_type_mismatch) + " of " + str(c.n_present) + " values do not fit "
            "the column's own type (" + c.inferred_type + ")",
            "more than a fifth of the column is unparseable, so any aggregate is "
            "computed over a biased subset",
            "decide per offending pattern whether to convert, quarantine or reject "
            "the row; fail the pipeline loudly rather than coercing to null",
        )
    elif mismatch_share > 0.02:
        reg.add(
            "HIGH", at,
            str(c.n_type_mismatch) + " value(s) do not fit type " + c.inferred_type
            + " (e.g. " + ", ".join(repr(x) for x in c.mismatch_examples[:3]) + ")",
            "these rows drop out of numeric aggregates, quietly shrinking totals",
            "add an explicit conversion rule per pattern and a validation check "
            "that counts rejects",
        )

    if len(c.date_formats) > 1 and not c.date_order_ambiguous:
        reg.add(
            "HIGH", at,
            str(len(c.date_formats)) + " date formats in one column ("
            + ", ".join(c.date_formats) + ")",
            "a single parse format drops or misreads the other formats, leaving "
            "gaps in every time series",
            "parse with an ordered list of explicit formats and assert that zero "
            "values fall through",
        )
    if c.missing_pct >= 50.0:
        reg.add(
            "HIGH", at,
            str(c.missing_pct) + "% of values are missing",
            "any view built on this column represents a minority of the data",
            "confirm with stakeholders whether the column is worth keeping; if it "
            "is, show coverage alongside it on the dashboard",
        )
    elif c.missing_pct >= 10.0:
        reg.add(
            "MEDIUM", at,
            str(c.missing_pct) + "% of values are missing",
            "averages and category breakdowns exclude these rows, so shares do "
            "not add to the whole",
            "document an impute/flag/drop rule for this column and surface the "
            "excluded count in the UI",
        )
    if c.sentinel_forms:
        reg.add(
            "HIGH", at,
            "nulls stored as text in " + str(len(c.sentinel_forms)) + " form(s): "
            + ", ".join(repr(k) for k in sorted(c.sentinel_forms)),
            "COUNT and COUNT DISTINCT treat these as real values, so 'unknown' "
            "becomes a legitimate-looking category on every chart",
            "normalise all of these to a true null on ingest, before any typing",
        )
    if c.case_variant_groups:
        example = next(iter(c.case_variant_groups.values()))
        reg.add(
            "MEDIUM", at,
            str(len(c.case_variant_groups)) + " label(s) spelled inconsistently, e.g. "
            + " / ".join(example),
            "one real category splits into several bars, understating each and "
            "breaking any ranking",
            "standardise to a controlled vocabulary with an explicit mapping table",
        )
    if c.n_untidy_whitespace:
        reg.add(
            "MEDIUM", at,
            str(c.n_untidy_whitespace) + " value(s) carry leading/trailing whitespace",
            "' North' and 'North' group separately and joins miss",
            "strip whitespace on ingest for every text column",
        )
    if c.mojibake_examples:
        reg.add(
            "MEDIUM", at,
            "encoding damage present, e.g. " + repr(c.mojibake_examples[0]),
            "names and labels render as garbage, and the damaged and clean forms "
            "of the same value group separately",
            "re-read the source with the correct encoding rather than repairing "
            "the text after the fact",
        )
    if c.is_constant:
        reg.add(
            "LOW", at,
            "constant column - every row holds " + repr(c.top_values[0][0]),
            "carries no information; occupies space in the schema and the UI",
            "drop from the clean schema unless a stakeholder needs it for lineage",
        )
    if c.outliers and c.outliers["n_outliers"]:
        share = c.outliers["n_outliers"] / max(c.n_present, 1)
        if share <= 0.10:
            reg.add(
                "MEDIUM", at,
                str(c.outliers["n_outliers"]) + " value(s) outside IQR bounds, e.g. "
                + ", ".join(str(x) for x in c.outliers["examples"][:3]),
                "a single extreme value can dominate an average or rescale a chart "
                "axis so the rest of the data is unreadable",
                "decide whether these are errors or genuine extremes; if genuine, "
                "prefer median/percentile summaries on the dashboard",
            )
    if c.numeric and c.numeric.get("n_negative") and c.inferred_type == "currency":
        reg.add(
            "MEDIUM", at,
            str(c.numeric["n_negative"]) + " negative monetary value(s)",
            "refunds netted into revenue without being labelled make growth look "
            "worse than it is, or hide a sign error",
            "confirm whether these are credits; if so, give them their own flag so "
            "gross and net can both be shown",
        )


def _table_risks(reg: RiskRegister, tp: TableProfile) -> None:
    if tp.n_exact_duplicate_rows:
        reg.add(
            "HIGH", tp.name,
            str(tp.n_exact_duplicate_rows) + " exact duplicate row(s)",
            "every count, sum and average is inflated by the repeated rows",
            "de-duplicate on an agreed key and record how many rows were removed",
        )
    extra = tp.n_normalised_duplicate_rows - tp.n_exact_duplicate_rows
    if extra > 0:
        reg.add(
            "HIGH", tp.name,
            str(extra) + " duplicate row(s) that differ only by case or whitespace",
            "these survive a naive drop_duplicates(), so the table looks clean "
            "while still double-counting",
            "normalise text before de-duplicating, not after",
        )
    if tp.n_empty_rows:
        reg.add(
            "MEDIUM", tp.name,
            str(tp.n_empty_rows) + " completely empty row(s)",
            "inflates row counts and adds a blank category to grouped charts",
            "drop rows that are empty across every column, and log the count",
        )
    if not tp.candidate_keys:
        reg.add(
            "HIGH", tp.name,
            "no single column is a unique, complete key",
            "without a key, de-duplication and joins are guesswork and cannot be "
            "verified",
            "agree a composite key with the data owner, or mint a surrogate key "
            "during ingest",
        )
    for w in tp.load_warnings:
        if "not utf-8" in w:
            reg.add(
                "MEDIUM", tp.name,
                "file is not UTF-8 (" + str(tp.encoding) + ")",
                "reading it with the wrong encoding corrupts non-English text "
                "silently",
                "record the encoding in the source inventory and pin it in the "
                "pipeline config rather than re-detecting each run",
            )


def _schema_risks(reg: RiskRegister, mx: SchemaMatrix) -> None:
    for m in mx.matches:
        pair = m.left.ref + " <-> " + m.right.ref
        if m.verdict.startswith("NOT joinable"):
            reg.add(
                "BLOCKER", pair,
                "column names match but the values share no vocabulary ("
                + ", ".join(m.left.distinct_sample[:3]) + " vs "
                + ", ".join(m.right.distinct_sample[:3]) + ")",
                "the join returns almost no rows, or silently drops most of them - "
                "a dashboard built on it shows a fraction of the business",
                "build an explicit crosswalk table between the two vocabularies and "
                "assert full coverage of both sides",
            )
        elif "partial value overlap" in m.verdict:
            reg.add(
                "HIGH", pair,
                "only " + str(round(100 * (m.value_overlap or 0))) + "% of values "
                "overlap between the two sources",
                "rows on either side of the join disappear from the dashboard "
                "without warning",
                "map the unmatched values explicitly and decide whether unmatched "
                "rows are dropped or surfaced as 'unallocated'",
            )
        if not m.type_agreement:
            reg.add(
                "HIGH", pair,
                "type conflict: " + m.left.inferred_type + " vs "
                + m.right.inferred_type,
                "the join either fails or compares a number to a string and "
                "matches nothing",
                "cast both sides to one declared type in the clean schema",
            )
    for f in mx.findings:
        if "disagree on naming convention" in f or "mixes" in f:
            reg.add(
                "LOW", "schema", f,
                "inconsistent naming slows every later query and invites mistakes "
                "in the agent's generated SQL",
                "standardise on snake_case in the clean schema and keep the source "
                "name in the data dictionary",
            )


def build(profiles: list[TableProfile], mx: SchemaMatrix) -> RiskRegister:
    reg = RiskRegister(
        n_tables=len(profiles),
        n_columns=sum(len(tp.columns) for tp in profiles),
        n_rows=sum(tp.n_rows for tp in profiles),
    )
    for tp in profiles:
        _table_risks(reg, tp)
        for c in tp.columns:
            _column_risks(reg, tp.name, c)
    _schema_risks(reg, mx)
    reg.risks.sort(key=lambda r: (SEVERITIES.index(r.severity), r.where))
    return reg


def render_markdown(reg: RiskRegister) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c = reg.counts
    out = [
        "# Data Quality Risk Summary",
        "",
        "Generated " + ts + " by `dtp.risks` (Phase 1.1).",
        "",
        "**Verdict: " + reg.go_no_go + "**",
        "",
        "Scope: " + str(reg.n_tables) + " source(s), " + str(reg.n_columns)
        + " column(s), " + f"{reg.n_rows:,}" + " row(s).",
        "",
        "| Severity | Count | Meaning |",
        "|---|---:|---|",
        "| BLOCKER | " + str(c["BLOCKER"]) + " | produces a confidently wrong number |",
        "| HIGH | " + str(c["HIGH"]) + " | distorts totals or breaks a join |",
        "| MEDIUM | " + str(c["MEDIUM"]) + " | degrades grouping, labels or trust |",
        "| LOW | " + str(c["LOW"]) + " | cosmetic or informational |",
        "",
    ]
    for sev in SEVERITIES:
        items = reg.by_severity(sev)
        if not items:
            continue
        out += ["---", "", "## " + sev + " (" + str(len(items)) + ")", ""]
        for i, r in enumerate(items, 1):
            out += [
                "### " + str(i) + ". `" + r.where + "`",
                "",
                "- **What:** " + r.finding,
                "- **So what:** " + r.impact,
                "- **Do:** " + r.action,
                "",
            ]
    return "\n".join(out)


def run(
    profiles: list[TableProfile], mx: SchemaMatrix, out_dir: Path | None = None
) -> tuple[RiskRegister, dict[str, Path]]:
    from . import REPORTS_DIR

    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    reg = build(profiles, mx)
    md_path = out_dir / "risk-summary.md"
    json_path = out_dir / "risk-summary.json"
    md_path.write_text(render_markdown(reg), encoding="utf-8")
    json_path.write_text(
        json.dumps(reg.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    return reg, {"markdown": md_path, "json": json_path}
