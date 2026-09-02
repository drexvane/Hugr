"""End-to-end pipeline (Phase 1.3).

    raw files -> clean -> validate -> snapshot -> dictionary -> alerts

One command, no manual steps, and one rule that shapes everything else:

    **A snapshot is only published if validation passed.**

That ordering is the point. Cleaning and validation both run before anything is
versioned, so a failed run leaves the previous snapshot as the newest one and
nothing downstream silently picks up bad data. `--force` exists for the case
where you are deliberately capturing a broken state to look at, and it stamps
the manifest so the snapshot cannot later be mistaken for a clean one.

The stage list is data, so `dtp pipeline --stop-after validate` is a real thing
you can do while iterating on rules without waiting for Parquet to be written.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import CLEAN_DIR, RAW_DIR, REPORTS_DIR, VERSIONS_DIR
from . import clean as clean_mod
from . import dictionary as dict_mod
from . import monitoring as monitor_mod
from . import validate as validate_mod
from . import versioning as version_mod

STAGES = ("clean", "validate", "snapshot", "dictionary", "monitor")


@dataclass
class StageResult:
    name: str
    ok: bool
    summary: str
    seconds: float = 0.0
    skipped: bool = False
    outputs: dict[str, str] = field(default_factory=dict)

    def line(self) -> str:
        if self.skipped:
            return "- " + self.name + ": skipped - " + self.summary
        mark = "ok" if self.ok else "FAIL"
        return ("- " + self.name + " [" + mark + ", "
                + format(self.seconds, ".1f") + "s]: " + self.summary)


@dataclass
class PipelineResult:
    stages: list[StageResult] = field(default_factory=list)
    version_id: str | None = None
    clean_tables: list[Any] = field(default_factory=list)
    validation: Any = None
    manifest: Any = None
    dictionary: Any = None
    alerts: Any = None

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.stages)

    def stage(self, name: str) -> StageResult | None:
        return next((s for s in self.stages if s.name == name), None)

    def verdict(self) -> str:
        failed = [s.name for s in self.stages if not s.ok]
        if failed:
            return "PIPELINE FAILED at " + ", ".join(failed)
        if self.version_id:
            return "PIPELINE OK - published snapshot " + self.version_id
        return "PIPELINE OK - no snapshot published"

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "verdict": self.verdict(),
            "version_id": self.version_id,
            "stages": [
                {"name": s.name, "ok": s.ok, "skipped": s.skipped,
                 "seconds": round(s.seconds, 3), "summary": s.summary,
                 "outputs": s.outputs}
                for s in self.stages
            ],
        }


class _Clock:
    """Times a stage without every call site repeating two lines of bookkeeping."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def stop(self) -> float:
        return time.perf_counter() - self.t0


def _paths(mapping: dict[str, Path]) -> dict[str, str]:
    return {k: str(v) for k, v in mapping.items()}


def run(raw_dir: Path | None = None, clean_dir: Path | None = None,
        versions_dir: Path | None = None, config_path: Path | None = None,
        rules_path: Path | None = None, thresholds_path: Path | None = None,
        stop_after: str | None = None, force_snapshot: bool = False,
        notes: str | None = None, write_parquet: bool = True,
        reports_dir: Path | None = None,
        docs_dir: Path | None = None) -> PipelineResult:
    raw_dir = raw_dir or RAW_DIR
    clean_dir = clean_dir or CLEAN_DIR
    versions_dir = versions_dir or VERSIONS_DIR
    reports_dir = reports_dir or REPORTS_DIR
    if stop_after is not None and stop_after not in STAGES:
        raise ValueError("stop_after must be one of " + ", ".join(STAGES))

    result = PipelineResult()

    def done(stage: str) -> bool:
        """True when this stage is the last one requested."""
        return stop_after == stage

    # --- clean ------------------------------------------------------------
    clock = _Clock()
    tables, problems, clean_paths = clean_mod.run(
        raw_dir=raw_dir, out_dir=clean_dir, config_path=config_path,
        write_parquet=write_parquet, reports_dir=reports_dir)
    result.clean_tables = tables
    rows = sum(t.rows_out for t in tables)
    rejects = sum(t.n_rejects for t in tables)
    # "unconfigured" and "skipped" are different events; only the first is a
    # gap in the config, and saying so here keeps the one-line summary honest.
    unconfigured = [p for p in problems
                    if clean_mod.SKIPPED_MARKER not in p]
    result.stages.append(StageResult(
        "clean", ok=bool(tables),
        summary=(str(len(tables)) + " table(s), " + format(rows, ",")
                 + " rows, " + format(rejects, ",") + " rejected cell(s)"
                 + (", " + str(len(unconfigured)) + " unconfigured file(s)"
                    if unconfigured else "")),
        seconds=clock.stop(), outputs=_paths(clean_paths)))
    if not tables:
        # Nothing was cleaned, so there is nothing for the later stages to say.
        result.stages[-1].summary = "no configured sources found in " + str(raw_dir)
        return result
    if done("clean"):
        return result

    # --- validate ---------------------------------------------------------
    clock = _Clock()
    frames = {t.table: t.df for t in tables}
    report, validate_paths = validate_mod.run(rules_path=rules_path, tables=frames,
                                              reports_dir=reports_dir)
    result.validation = report
    result.stages.append(StageResult(
        "validate", ok=report.ok, summary=report.verdict(),
        seconds=clock.stop(), outputs=_paths(validate_paths)))
    if done("validate"):
        return result

    # --- snapshot ---------------------------------------------------------
    # The gate. Everything above is diagnosis; this is the first step that
    # publishes something other code will read.
    clock = _Clock()
    previous = version_mod.latest(versions_dir)
    if not report.ok and not force_snapshot:
        result.stages.append(StageResult(
            "snapshot", ok=False,
            summary=("refused - validation failed, so the newest snapshot stays "
                     + (previous.version_id if previous else "absent")
                     + ". Re-run with --force to capture the broken state."),
            seconds=clock.stop()))
        return result

    validation_summary: dict[str, Any] = {
        "status": "PASS" if report.ok else "FAIL",
        "verdict": report.verdict(),
        **report.counts,
        "failed_rules": [r.rule.id for r in report.failures],
        "warned_rules": [r.rule.id for r in report.warnings],
    }
    stamped_notes = notes
    if not report.ok:
        # A forced snapshot must be unmistakable from inside the manifest, not
        # just from whoever remembers typing --force.
        forced = "PUBLISHED WITH --force DESPITE FAILING VALIDATION"
        stamped_notes = forced + ((" | " + notes) if notes else "")
        validation_summary["forced"] = True

    target, manifest = version_mod.write_snapshot(
        frames, source_dir=raw_dir, versions_dir=versions_dir,
        validation=validation_summary, notes=stamped_notes)
    result.manifest = manifest
    result.version_id = manifest.version_id
    unchanged = (previous is not None
                 and not any(d.status != "unchanged"
                             for d in version_mod.diff(previous, manifest)))
    result.stages.append(StageResult(
        "snapshot", ok=True,
        summary=(manifest.version_id + " - " + str(len(manifest.tables))
                 + " table(s), " + format(manifest.total_rows, ",") + " rows"
                 + (" (identical to " + previous.version_id + ")"
                    if unchanged and previous else "")),
        seconds=clock.stop(), outputs={"snapshot": str(target)}))
    if done("snapshot"):
        return result

    # --- dictionary -------------------------------------------------------
    clock = _Clock()
    doc, dict_paths = dict_mod.run(raw_dir=raw_dir, config_path=config_path,
                                   rules_path=rules_path, tables=frames,
                                   out_dir=docs_dir, reports_dir=reports_dir)
    result.dictionary = doc
    result.stages.append(StageResult(
        "dictionary", ok=not doc.gaps,
        summary=(str(len(doc.fields)) + " field(s), "
                 + format(doc.coverage, ".1f") + "% documented"
                 + ("; undocumented: "
                    + ", ".join(g.table + "." + g.name for g in doc.gaps)
                    if doc.gaps else "")),
        seconds=clock.stop(), outputs=_paths(dict_paths)))
    if done("dictionary"):
        return result

    # --- monitor ----------------------------------------------------------
    clock = _Clock()
    alerts, alert_paths = monitor_mod.run(
        validation=report, cleaning=(tables, problems),
        snapshots=(previous, manifest),
        thresholds=monitor_mod.load_thresholds(thresholds_path))
    result.alerts = alerts
    result.stages.append(StageResult(
        "monitor", ok=alerts.ok, summary=alerts.verdict(),
        seconds=clock.stop(), outputs=_paths(alert_paths)))
    return result


def render_markdown(result: PipelineResult) -> str:
    lines = ["# Pipeline run", "", "**" + result.verdict() + "**", ""]
    lines += [s.line() for s in result.stages]
    lines.append("")
    if result.alerts is not None and result.alerts.alerts:
        lines += ["## Alerts", ""]
        lines += ["- " + a.line() for a in result.alerts.sorted_alerts()]
        lines.append("")
    return "\n".join(lines) + "\n"


def write_report(result: PipelineResult,
                 out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "pipeline-report.md"
    js = out_dir / "pipeline-report.json"
    md.write_text(render_markdown(result), encoding="utf-8")
    js.write_text(json.dumps(result.to_payload(), indent=2, default=str),
                  encoding="utf-8")
    return {"markdown": md, "json": js}
