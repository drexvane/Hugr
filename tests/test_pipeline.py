"""Tests for the end-to-end pipeline (Phase 1.3).

One rule shapes this module and so shapes these tests: **a snapshot is only
published if validation passed**. Most of what follows is that rule from
different angles - the refusal, what the refusal leaves behind, and the escape
hatch being unmistakable from inside the manifest rather than only in whoever
remembers typing `--force`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from dtp import pipeline as pipe
from dtp import versioning as version_mod

# A rule the mini fixture cannot satisfy: row 4 is CANCELED and carries 99.99.
FAILING_RULES = """
version: 1
tables:
  mini:
    rules:
      - id: no_cancelled_money
        type: expression
        expr: "is_revenue or line_total == 0"
        severity: error
        reason: Deliberately unsatisfiable, to exercise the snapshot gate.
"""


@pytest.fixture
def failing_rules(tmp_path: Path) -> Path:
    path = tmp_path / "failing_rules.yml"
    path.write_text(FAILING_RULES, encoding="utf-8")
    return path


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    return {"clean_dir": tmp_path / "clean", "versions_dir": tmp_path / "versions",
            "reports_dir": tmp_path / "reports", "docs_dir": tmp_path / "docs"}


@pytest.fixture
def run(mini_raw, mini_config, mini_rules, dirs, tmp_path):
    """Run the pipeline against the mini fixture, entirely inside tmp_path."""
    def _run(**kwargs):
        kwargs.setdefault("rules_path", mini_rules)
        thresholds = kwargs.pop("thresholds_path", tmp_path / "no-thresholds.yml")
        return pipe.run(raw_dir=mini_raw, config_path=mini_config,
                        thresholds_path=thresholds, **dirs, **kwargs)
    return _run


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #

def test_one_command_runs_every_stage(run, dirs):
    result = run()
    assert [s.name for s in result.stages] == list(pipe.STAGES)
    assert result.ok, result.verdict()
    assert result.version_id
    assert "PIPELINE OK - published snapshot" in result.verdict()
    # Each stage actually produced what it claims to have produced.
    assert (dirs["clean_dir"] / "mini.parquet").exists()
    assert (dirs["versions_dir"] / result.version_id / "manifest.json").exists()
    assert (dirs["docs_dir"] / "data-dictionary.md").exists()
    assert (dirs["reports_dir"] / "alerts.md").exists()


def test_the_snapshot_holds_what_cleaning_produced(run, dirs):
    result = run()
    written = version_mod.load_version(result.version_id,
                                       versions_dir=dirs["versions_dir"])
    cleaned = {t.table: t.df for t in result.clean_tables}
    assert set(written) == set(cleaned)
    assert (version_mod.content_hash(written["mini"])
            == version_mod.content_hash(cleaned["mini"]))


def test_the_manifest_records_the_validation_verdict(run, dirs):
    result = run()
    raw = json.loads((dirs["versions_dir"] / result.version_id / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert raw["validation"]["status"] == "PASS"
    assert raw["validation"]["failed_rules"] == []
    assert "forced" not in raw["validation"]
    assert raw["notes"] is None


def test_stage_timings_and_summaries_are_reported(run):
    result = run()
    clean = result.stage("clean")
    assert clean.seconds > 0
    assert "7 rows" in clean.summary
    assert "1 rejected cell" in clean.summary
    assert "[ok," in clean.line()


def test_notes_reach_the_manifest(run, dirs):
    result = run(notes="first real load")
    assert result.manifest.notes == "first real load"


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #

def test_a_failing_validation_refuses_the_snapshot(run, dirs, failing_rules):
    result = run(rules_path=failing_rules)
    assert not result.ok
    assert result.version_id is None
    assert result.stage("validate").ok is False
    snapshot = result.stage("snapshot")
    assert snapshot.ok is False
    assert "refused" in snapshot.summary
    # Nothing was published, and the stage that documents the published schema
    # did not run - there is nothing to document.
    assert not dirs["versions_dir"].exists()
    assert result.stage("dictionary") is None
    assert "PIPELINE FAILED at validate, snapshot" in result.verdict()


def test_a_refused_run_still_raises_the_alert(run, dirs, failing_rules):
    """The alerting must not go quiet exactly when something is wrong.

    Skipping the monitor stage on a failure would leave `reports/alerts.md` holding
    the previous run's "nothing to report", so the one file an operator or a cron
    job reads would say the run was fine.
    """
    result = run(rules_path=failing_rules)
    monitor = result.stage("monitor")
    assert monitor is not None and monitor.ok is False
    assert any("no_cancelled_money" in a.title for a in result.alerts.critical)
    written = (dirs["reports_dir"] / "alerts.md").read_text(encoding="utf-8")
    assert "no_cancelled_money" in written
    assert "ALERT - 1 critical" in written
    # No snapshot was written, so there is no drift to report against.
    assert not any(a.source == "drift" for a in result.alerts.alerts)


def test_an_explicit_stop_after_is_not_a_failure_and_stays_quiet(run, dirs):
    """`--stop-after validate` is a deliberate early exit, so it alerts nowhere."""
    result = run(stop_after="validate")
    assert result.stage("monitor") is None
    assert not (dirs["reports_dir"] / "alerts.md").exists()


def test_a_refusal_leaves_the_previous_snapshot_as_the_newest(run, dirs,
                                                             failing_rules):
    """The point of the gate: downstream keeps reading the last good data."""
    good = run()
    assert version_mod.latest(dirs["versions_dir"]).version_id == good.version_id
    bad = run(rules_path=failing_rules)
    assert bad.version_id is None
    assert good.version_id in bad.stage("snapshot").summary
    assert version_mod.latest(dirs["versions_dir"]).version_id == good.version_id
    assert len(version_mod.list_versions(dirs["versions_dir"])) == 1


def test_with_no_baseline_the_refusal_says_absent(run, failing_rules):
    assert "stays absent" in run(rules_path=failing_rules).stage("snapshot").summary


def test_force_publishes_and_stamps_the_manifest(run, dirs, failing_rules):
    """A forced snapshot must be unmistakable from inside the manifest."""
    result = run(rules_path=failing_rules, force_snapshot=True,
                 notes="capturing the break")
    assert result.version_id
    assert result.stage("snapshot").ok
    raw = json.loads((dirs["versions_dir"] / result.version_id / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert raw["validation"]["status"] == "FAIL"
    assert raw["validation"]["forced"] is True
    assert raw["validation"]["failed_rules"] == ["no_cancelled_money"]
    assert raw["notes"].startswith("PUBLISHED WITH --force")
    assert "capturing the break" in raw["notes"]
    # The run still fails: forcing changes what is published, not the verdict.
    assert not result.ok


def test_a_forced_snapshot_is_visible_in_the_version_listing(run, dirs,
                                                            failing_rules):
    run(rules_path=failing_rules, force_snapshot=True)
    listing = version_mod.format_versions(version_mod.list_versions(
        dirs["versions_dir"]))
    assert "validation=FAIL" in listing


# --------------------------------------------------------------------------- #
# stopping early
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stage", list(pipe.STAGES))
def test_stop_after_runs_exactly_that_far(run, stage):
    result = run(stop_after=stage)
    assert [s.name for s in result.stages] == list(
        pipe.STAGES[:pipe.STAGES.index(stage) + 1])


def test_stop_after_validate_writes_no_snapshot(run, dirs):
    """The iterating-on-rules workflow: check the rules, publish nothing."""
    result = run(stop_after="validate")
    assert result.version_id is None
    assert not dirs["versions_dir"].exists()
    assert result.ok                     # stopping early is not a failure


def test_an_unknown_stage_is_rejected_before_any_work_happens(run):
    with pytest.raises(ValueError, match="stop_after must be one of"):
        run(stop_after="wishful")


def test_write_parquet_can_be_skipped(run, dirs):
    result = run(stop_after="clean", write_parquet=False)
    assert result.ok
    assert not (dirs["clean_dir"] / "mini.parquet").exists()
    # The report is still written: it is the point of a dry run.
    assert (dirs["reports_dir"] / "cleaning-report.md").exists()


# --------------------------------------------------------------------------- #
# nothing to do
# --------------------------------------------------------------------------- #

def test_an_empty_source_directory_fails_without_pretending_to_succeed(
        mini_config, mini_rules, dirs, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = pipe.run(raw_dir=empty, config_path=mini_config,
                      rules_path=mini_rules, **dirs)
    assert not result.ok
    assert len(result.stages) == 1
    assert "no configured sources found" in result.stages[0].summary
    assert result.version_id is None


def test_an_unconfigured_file_is_counted_in_the_clean_summary(run, mini_raw):
    (mini_raw / "surprise.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    result = run(stop_after="clean")
    assert "1 unconfigured file(s)" in result.stages[0].summary


def test_a_skipped_file_is_not_counted_as_unconfigured(run):
    """readme.csv is in the skip list; saying otherwise would be a false alarm."""
    assert "unconfigured" not in run(stop_after="clean").stages[0].summary


# --------------------------------------------------------------------------- #
# monitoring, and the second run
# --------------------------------------------------------------------------- #

def test_the_first_run_has_no_baseline_and_the_second_matches_it(run, dirs):
    first = run()
    assert any("no baseline" in a.title for a in first.alerts.alerts)
    second = run()
    assert second.ok
    assert "identical to " + first.version_id in second.stage("snapshot").summary
    assert any("unchanged since" in a.title for a in second.alerts.alerts)


def test_rejected_cells_are_alerted_but_do_not_fail_the_run(run):
    """The mini fixture has one uncoercible cell; a warning must not become a stop."""
    result = run()
    assert any("could not be typed" in a.title for a in result.alerts.alerts)
    assert result.stage("monitor").ok
    assert result.ok


def test_a_row_count_that_disagrees_with_config_fails_the_monitor_stage(
        run, mini_raw):
    """Deleting a row is exactly the "a load went wrong" event monitoring is for."""
    rows = (mini_raw / "mini.csv").read_text(encoding="utf-8").splitlines()
    (mini_raw / "mini.csv").write_text("\n".join(rows[:-1]) + "\n",
                                       encoding="utf-8")
    result = run()
    assert not result.stage("monitor").ok
    assert not result.ok
    assert any("row count disagrees" in a.title
               for a in result.alerts.critical)


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def test_write_report_renders_the_stage_list_and_the_alerts(run, tmp_path):
    result = run()
    paths = pipe.write_report(result, out_dir=tmp_path / "out")
    md = paths["markdown"].read_text(encoding="utf-8")
    assert "# Pipeline run" in md
    assert result.verdict() in md
    for stage in pipe.STAGES:
        assert "- " + stage + " [" in md
    assert "## Alerts" in md
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["version_id"] == result.version_id
    assert [s["name"] for s in payload["stages"]] == list(pipe.STAGES)
    assert all(s["seconds"] >= 0 for s in payload["stages"])


def test_the_report_of_a_refused_run_names_the_failure(run, failing_rules,
                                                      tmp_path):
    result = run(rules_path=failing_rules)
    md = pipe.render_markdown(result)
    assert "PIPELINE FAILED at validate, snapshot" in md
    assert "refused" in md


def test_the_report_names_the_source_it_described(run, synthetic_raw, tmp_path):
    """`--raw` exists, so `reports/` can hold a report about any source.

    A run over `data/_synthetic` and one over the real extract write the same
    filenames and are otherwise identical in shape, so a reader six months on cannot
    tell which they have. Same argument as `--force` stamping the manifest: the thing
    that makes a report readable later is that it says what it was.
    """
    result = run()
    assert result.raw_dir is not None
    md = pipe.render_markdown(result)
    assert "Source: `" + str(result.raw_dir) + "`" in md
    paths = pipe.write_report(result, out_dir=tmp_path / "named")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["source"] == str(result.raw_dir)


def test_the_cli_can_send_the_reports_somewhere_else(mini_raw, mini_config,
                                                     mini_rules, tmp_path, capsys):
    """`dtp pipeline --raw elsewhere` used to overwrite the committed reports.

    `reports/pipeline-report.md` and `reports/cleaning-report.*` are checked in and
    describe the real extract. Running the pipeline over another source replaced them
    with a report that did not say which source it was - so the flag exists, and it
    covers the docs too, because the dictionary lands in `docs/`.
    """
    from dtp import cli

    out, docs = tmp_path / "elsewhere", tmp_path / "elsedocs"
    code = cli.main(["pipeline", "--raw", str(mini_raw),
                     "--config", str(mini_config), "--rules", str(mini_rules),
                     "--clean", str(tmp_path / "c"),
                     "--versions", str(tmp_path / "v"),
                     "--reports", str(out), "--docs", str(docs)])
    assert code == 0, capsys.readouterr().out
    assert (out / "pipeline-report.md").exists()
    assert (out / "cleaning-report.md").exists()
    assert list(docs.glob("*.md"))
    # And the report says which source it was, which is the point of the flag.
    assert str(mini_raw) in (out / "pipeline-report.md").read_text(encoding="utf-8")
