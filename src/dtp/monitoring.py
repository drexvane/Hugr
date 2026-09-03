"""Monitoring and alerts (Phase 1.3).

The pipeline already refuses to publish a snapshot when validation fails. This
module is the other half: it turns everything the run learned into a ranked list
of alerts, so a failure is *reported* rather than merely *returned*.

Four sources feed it:

  validation  A failed `error` rule is critical; a failed `warn` rule is a
              warning. A rule that could not run at all is critical, because a
              check nobody is doing is worse than one that fails.
  cleaning    Rejected cells, a row count that disagrees with the configured
              expectation, and any source file the config does not describe.
  drift       The current snapshot against the previous one: row counts moving
              beyond tolerance, columns appearing or vanishing, dtypes changing,
              and nulls showing up where there were none.
  thresholds  `config/monitoring.yml`, so "how much movement is normal" is a
              reviewable number rather than a constant in this file.

There is deliberately no network sink. Alerts are written to `reports/alerts.md`
and `.json`, printed, and reflected in the process exit code, which is what CI
and cron actually read. Sending the contents of a dataset to a third party is a
decision for whoever operates this, not a default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import CONFIG_DIR, REPORTS_DIR

CRITICAL, WARNING, INFO = "critical", "warning", "info"
_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}

DEFAULT_THRESHOLDS: dict[str, Any] = {
    # A snapshot that loses rows is a different kind of event from one that gains
    # them: growth is expected, shrinkage usually means a load went wrong.
    "row_growth_pct": 25.0,
    "row_shrink_pct": 0.0,
    "new_null_columns": 0,
    "allow_schema_change": False,
    "max_rejects": 0,
}


@dataclass
class Alert:
    id: str
    severity: str
    title: str
    detail: str = ""
    source: str = "validation"

    def line(self) -> str:
        text = "[" + self.severity.upper() + "] " + self.title
        return text + (" - " + self.detail if self.detail else "")


@dataclass
class MonitorReport:
    alerts: list[Alert] = field(default_factory=list)
    checked_at: str = ""
    thresholds: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.checked_at:
            self.checked_at = datetime.now().isoformat(timespec="seconds")

    @property
    def critical(self) -> list[Alert]:
        return [a for a in self.alerts if a.severity == CRITICAL]

    @property
    def warnings(self) -> list[Alert]:
        return [a for a in self.alerts if a.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.critical

    def sorted_alerts(self) -> list[Alert]:
        return sorted(self.alerts, key=lambda a: (_ORDER.get(a.severity, 9), a.id))

    def verdict(self) -> str:
        if self.critical:
            return ("ALERT - " + str(len(self.critical))
                    + " critical, " + str(len(self.warnings)) + " warning(s)")
        if self.warnings:
            return "OK with " + str(len(self.warnings)) + " warning(s)"
        return "OK - nothing to report"


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    path = path or CONFIG_DIR / "monitoring.yml"
    if not path.exists():
        return dict(DEFAULT_THRESHOLDS)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {**DEFAULT_THRESHOLDS, **(raw.get("thresholds") or {})}


# --------------------------------------------------------------------------- #
# sources of alerts
# --------------------------------------------------------------------------- #

def from_validation(report: Any) -> list[Alert]:
    out: list[Alert] = []
    for table in report.missing_tables:
        out.append(Alert(
            id="missing_table:" + table, severity=CRITICAL,
            title="Expected table absent: " + table,
            detail="validation_rules.yml describes it; the clean output does not "
                   "contain it.",
            source="validation",
        ))
    for result in report.results:
        if result.passed:
            continue
        severity = CRITICAL if result.severity == "error" else WARNING
        detail = (format(result.n_violations, ",") + " of "
                  + format(result.n_checked, ",") + " rows")
        if result.error:
            detail = "rule could not run: " + str(result.error)
        else:
            if result.rule.expect_violations is not None:
                detail += " (expected exactly "
                detail += format(result.rule.expect_violations, ",") + ")"
            # The checker's own one-line explanation - which bound was breached,
            # which column held the nulls. Without it the alert names a rule id
            # and a count and leaves the reader to open the report to find out
            # what was actually wrong.
            if result.detail:
                detail += "; " + str(result.detail)
        if result.samples:
            detail += " e.g. " + ", ".join(str(s) for s in result.samples[:3])
        out.append(Alert(
            id="rule:" + result.table + "." + result.rule.id,
            severity=severity,
            title=result.table + "." + result.rule.id + " violated",
            detail=detail, source="validation",
        ))
    return out


def _source_alert(problem: str) -> Alert:
    """Classify one of `clean.load_sources`'s problem strings.

    A file we deliberately skipped is information; one nobody configured is a
    warning, because it is data going unused; one that failed to parse is
    critical, because the run silently produced less than it was asked for.
    """
    from .clean import SKIPPED_MARKER, UNCONFIGURED_MARKER

    name = problem.split(":", 1)[0]
    if SKIPPED_MARKER in problem:
        return Alert(id="source:skipped:" + name, severity=INFO,
                     title="Source skipped by config: " + name,
                     detail=problem.split(SKIPPED_MARKER, 1)[1],
                     source="cleaning")
    if UNCONFIGURED_MARKER in problem:
        return Alert(id="source:unconfigured:" + name, severity=WARNING,
                     title="Source file nothing consumes: " + name,
                     detail="No entry in cleaning_rules.yml, so it was not "
                            "cleaned, validated or documented.",
                     source="cleaning")
    return Alert(id="source:error:" + name, severity=CRITICAL,
                 title="Source file could not be read: " + name,
                 detail=problem, source="cleaning")


def from_cleaning(results: list[Any], problems: list[str],
                  thresholds: dict[str, Any] | None = None) -> list[Alert]:
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    out: list[Alert] = []
    for problem in problems:
        out.append(_source_alert(problem))
    for r in results:
        if r.n_rejects > int(t["max_rejects"]):
            out.append(Alert(
                id="rejects:" + r.table, severity=WARNING,
                title=r.table + ": " + format(r.n_rejects, ",")
                      + " cells could not be typed",
                detail="Quarantined in " + r.table
                       + "__rejects.parquet; the rows were kept.",
                source="cleaning",
            ))
        expected = getattr(r.spec, "row_count_expected", None) if r.spec else None
        # Compared against rows *read*, not rows written: de-duplication is
        # supposed to change the row count, and the deduplicate rule already
        # records how many it removed.
        if expected is not None and int(expected) != int(r.rows_in):
            out.append(Alert(
                id="rowcount:" + r.table, severity=CRITICAL,
                title=r.table + ": source row count disagrees with config",
                detail="cleaning_rules.yml expects "
                       + format(int(expected), ",") + " rows, the file has "
                       + format(int(r.rows_in), ","),
                source="cleaning",
            ))
    return out


def from_drift(old: Any, new: Any,
               thresholds: dict[str, Any] | None = None) -> list[Alert]:
    """Compare two snapshot manifests. `old` may be None on a first run."""
    from .versioning import diff

    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if old is None:
        return [Alert(id="drift:first", severity=INFO,
                      title="First snapshot - no baseline to compare against",
                      source="drift")]

    out: list[Alert] = []
    for d in diff(old, new):
        if d.status == "unchanged":
            out.append(Alert(id="drift:" + d.table, severity=INFO,
                             title=d.table + ": unchanged since "
                                   + old.version_id,
                             detail="content hash identical", source="drift"))
            continue
        if d.status == "removed":
            out.append(Alert(id="drift:" + d.table, severity=CRITICAL,
                             title=d.table + ": table disappeared",
                             detail="present in " + old.version_id
                                    + ", absent now", source="drift"))
            continue
        if d.status == "added":
            out.append(Alert(id="drift:" + d.table, severity=INFO,
                             title=d.table + ": new table",
                             detail=format(d.rows_after or 0, ",") + " rows",
                             source="drift"))
            continue

        before, after = d.rows_before or 0, d.rows_after or 0
        if before:
            pct = (after - before) / before * 100
            if pct < -float(t["row_shrink_pct"]):
                out.append(Alert(
                    id="drift:rows:" + d.table, severity=CRITICAL,
                    title=d.table + ": lost rows",
                    detail=format(before, ",") + " -> " + format(after, ",")
                           + " (" + format(pct, "+.2f") + "%, tolerance "
                           + format(-float(t["row_shrink_pct"]), "+.2f") + "%)",
                    source="drift"))
            elif pct > float(t["row_growth_pct"]):
                out.append(Alert(
                    id="drift:rows:" + d.table, severity=WARNING,
                    title=d.table + ": unusual row growth",
                    detail=format(before, ",") + " -> " + format(after, ",")
                           + " (" + format(pct, "+.2f") + "%, tolerance "
                           + format(float(t["row_growth_pct"]), "+.2f") + "%)",
                    source="drift"))

        schema_severity = INFO if t["allow_schema_change"] else CRITICAL
        if d.columns_removed:
            out.append(Alert(
                id="drift:cols_removed:" + d.table, severity=schema_severity,
                title=d.table + ": columns disappeared",
                detail=", ".join(d.columns_removed), source="drift"))
        if d.columns_added:
            out.append(Alert(
                id="drift:cols_added:" + d.table, severity=WARNING,
                title=d.table + ": new columns",
                detail=", ".join(d.columns_added), source="drift"))
        if d.dtype_changes:
            out.append(Alert(
                id="drift:dtypes:" + d.table, severity=schema_severity,
                title=d.table + ": dtypes changed",
                detail="; ".join(c + ": " + v
                                 for c, v in sorted(d.dtype_changes.items())),
                source="drift"))
        # New nulls are the quiet one: same schema, same row count, a metric moves.
        grew = {c: v for c, v in d.null_changes.items()
                if _null_grew(v)}
        if len(grew) > int(t["new_null_columns"]):
            out.append(Alert(
                id="drift:nulls:" + d.table, severity=WARNING,
                title=d.table + ": nulls increased in "
                      + str(len(grew)) + " column(s)",
                detail="; ".join(c + ": " + v for c, v in sorted(grew.items())),
                source="drift"))
    return out


def _null_grew(change: str) -> bool:
    try:
        before, after = (int(x.strip()) for x in change.split("->"))
    except ValueError:
        return True
    return after > before


# --------------------------------------------------------------------------- #
# orchestration and output
# --------------------------------------------------------------------------- #

def check(validation: Any = None,
          cleaning: tuple[list[Any], list[str]] | None = None,
          snapshots: tuple[Any, Any] | None = None,
          thresholds: dict[str, Any] | None = None) -> MonitorReport:
    """Assemble every alert. Any source may be omitted."""
    t = thresholds if thresholds is not None else load_thresholds()
    alerts: list[Alert] = []
    if validation is not None:
        alerts += from_validation(validation)
    if cleaning is not None:
        alerts += from_cleaning(cleaning[0], cleaning[1], t)
    if snapshots is not None:
        alerts += from_drift(snapshots[0], snapshots[1], t)
    return MonitorReport(alerts=alerts, thresholds=t)


def render_markdown(report: MonitorReport) -> str:
    lines = ["# Alerts", "",
             "Checked at " + report.checked_at, "",
             "**" + report.verdict() + "**", ""]
    if not report.alerts:
        lines += ["Nothing to report: validation held, cleaning quarantined "
                  "nothing, and the snapshot matches its baseline.", ""]
    else:
        lines += ["| severity | source | alert | detail |",
                  "| --- | --- | --- | --- |"]
        for a in report.sorted_alerts():
            lines.append("| " + a.severity + " | " + a.source + " | "
                         + a.title.replace("|", r"\|") + " | "
                         + a.detail.replace("|", r"\|") + " |")
        lines.append("")
    # Printed even on a clean run: "nothing to report" only means something if the
    # reader can see what would have been reported.
    lines += ["## Thresholds in force", ""]
    lines += ["- `" + k + "`: " + str(v)
              for k, v in sorted(report.thresholds.items())]
    lines.append("")
    return "\n".join(lines) + "\n"


def to_payload(report: MonitorReport) -> dict[str, Any]:
    return {
        "checked_at": report.checked_at,
        "verdict": report.verdict(),
        "ok": report.ok,
        "counts": {
            "critical": len(report.critical),
            "warning": len(report.warnings),
            "info": sum(1 for a in report.alerts if a.severity == INFO),
        },
        "thresholds": report.thresholds,
        "alerts": [
            {"id": a.id, "severity": a.severity, "source": a.source,
             "title": a.title, "detail": a.detail}
            for a in report.sorted_alerts()
        ],
    }


def run(validation: Any = None,
        cleaning: tuple[list[Any], list[str]] | None = None,
        snapshots: tuple[Any, Any] | None = None,
        thresholds: dict[str, Any] | None = None,
        out_dir: Path | None = None) -> tuple[MonitorReport, dict[str, Path]]:
    report = check(validation, cleaning, snapshots, thresholds)
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "alerts.md"
    js = out_dir / "alerts.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(json.dumps(to_payload(report), indent=2, default=str),
                  encoding="utf-8")
    return report, {"markdown": md, "json": js}
