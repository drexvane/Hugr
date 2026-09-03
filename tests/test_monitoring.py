"""Tests for monitoring and alerts (Phase 1.3).

Monitoring's only job is to make a failure impossible to miss, so the tests are
about the classification: does the right event get the right severity, and does
`ok` - which becomes the process exit code - track *critical* rather than
"anything happened at all". Fake objects stand in for the validation report and
the manifests, because what is under test is the ranking, not pandas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dtp import clean as clean_mod
from dtp import monitoring as mon
from dtp import versioning as version_mod


# --------------------------------------------------------------------------- #
# stand-ins. Small enough to read, shaped exactly like the real thing.
# --------------------------------------------------------------------------- #

@dataclass
class FakeRule:
    id: str = "r"
    expect_violations: int | None = None


@dataclass
class FakeResult:
    rule: FakeRule = field(default_factory=FakeRule)
    table: str = "t"
    n_checked: int = 10
    n_violations: int = 1
    severity: str = "error"
    passed: bool = False
    error: str | None = None
    detail: str = ""
    samples: list[str] = field(default_factory=list)


@dataclass
class FakeReport:
    results: list[FakeResult] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)


@dataclass
class FakeSpec:
    row_count_expected: int | None = None


@dataclass
class FakeClean:
    table: str = "t"
    rows_in: int = 100
    n_rejects: int = 0
    spec: Any = None


def manifest(version_id: str = "v1", **tables) -> version_mod.Manifest:
    """A manifest built by hand: `a=(rows, {col: nulls})`."""
    m = version_mod.Manifest(version_id=version_id, created_at="now",
                             dtp_version="test", source_dir="raw")
    for name, (rows, nulls) in tables.items():
        m.tables.append(version_mod.TableVersion(
            table=name, rows=rows, columns=len(nulls),
            content_hash=name + str(rows) + str(sorted(nulls.items())),
            dtypes={c: "Int64" for c in nulls}, null_counts=dict(nulls),
            file=name + ".parquet"))
    return m


# --------------------------------------------------------------------------- #
# thresholds
# --------------------------------------------------------------------------- #

def test_missing_config_falls_back_to_the_defaults(tmp_path):
    assert mon.load_thresholds(tmp_path / "absent.yml") == mon.DEFAULT_THRESHOLDS


def test_config_overrides_only_what_it_names(tmp_path):
    path = tmp_path / "monitoring.yml"
    path.write_text("thresholds:\n  row_growth_pct: 50\n", encoding="utf-8")
    t = mon.load_thresholds(path)
    assert t["row_growth_pct"] == 50
    assert t["max_rejects"] == mon.DEFAULT_THRESHOLDS["max_rejects"]


def test_the_shipped_thresholds_parse():
    """A typo in config/monitoring.yml should fail here, not in a nightly run."""
    t = mon.load_thresholds()
    assert set(t) >= set(mon.DEFAULT_THRESHOLDS)
    assert float(t["row_shrink_pct"]) >= 0
    assert isinstance(t["allow_schema_change"], bool)


# --------------------------------------------------------------------------- #
# validation -> alerts
# --------------------------------------------------------------------------- #

def test_an_error_rule_is_critical_and_a_warn_rule_is_not():
    report = FakeReport(results=[
        FakeResult(rule=FakeRule("bad"), severity="error"),
        FakeResult(rule=FakeRule("meh"), severity="warn"),
    ])
    alerts = mon.from_validation(report)
    assert [a.severity for a in alerts] == [mon.CRITICAL, mon.WARNING]
    assert alerts[0].id == "rule:t.bad"


def test_a_passing_rule_produces_no_alert():
    assert mon.from_validation(FakeReport(results=[FakeResult(passed=True)])) == []


def test_an_unrunnable_rule_says_so_rather_than_reporting_zero_violations():
    report = FakeReport(results=[FakeResult(n_violations=0, n_checked=0,
                                            error="KeyError: nope")])
    detail = mon.from_validation(report)[0].detail
    assert "could not run" in detail
    assert "KeyError: nope" in detail


def test_a_pinned_count_is_shown_alongside_the_actual():
    report = FakeReport(results=[
        FakeResult(rule=FakeRule("pinned", expect_violations=7754),
                   n_violations=7755, severity="warn")])
    assert "expected exactly 7,754" in mon.from_validation(report)[0].detail


def test_samples_are_included_but_capped():
    report = FakeReport(results=[FakeResult(samples=["a", "b", "c", "d", "e"])])
    detail = mon.from_validation(report)[0].detail
    assert "e.g. a, b, c" in detail
    assert "d" not in detail.split("e.g. ")[1]


def test_the_alert_carries_what_the_checker_observed():
    """A rule id and a count alone send the reader off to open the report."""
    report = FakeReport(results=[
        FakeResult(detail="qty >= 1; observed min -3")])
    detail = mon.from_validation(report)[0].detail
    assert "1 of 10 rows" in detail
    assert "qty >= 1; observed min -3" in detail


def test_an_unrunnable_rule_reports_only_the_error():
    """The checker's detail is empty or stale when the rule never ran."""
    report = FakeReport(results=[FakeResult(error="KeyError: nope",
                                           detail="nulls: none")])
    detail = mon.from_validation(report)[0].detail
    assert detail == "rule could not run: KeyError: nope"


def test_large_counts_are_readable():
    report = FakeReport(results=[FakeResult(n_violations=155679,
                                            n_checked=180519)])
    assert "155,679 of 180,519 rows" in mon.from_validation(report)[0].detail


def test_a_missing_table_is_critical():
    alerts = mon.from_validation(FakeReport(missing_tables=["order_items"]))
    assert alerts[0].severity == mon.CRITICAL
    assert alerts[0].id == "missing_table:order_items"


# --------------------------------------------------------------------------- #
# cleaning -> alerts. The three problem kinds must not collapse into one.
# --------------------------------------------------------------------------- #

def test_a_skipped_source_is_information_not_a_problem():
    problem = "readme" + clean_mod.SKIPPED_MARKER + "Not a table."
    a = mon._source_alert(problem)
    assert a.severity == mon.INFO
    assert a.detail == "Not a table."


def test_an_unconfigured_source_is_a_warning_because_data_goes_unused():
    a = mon._source_alert("surprise" + clean_mod.UNCONFIGURED_MARKER)
    assert a.severity == mon.WARNING
    assert "nothing consumes" in a.title


def test_an_unreadable_source_is_critical():
    a = mon._source_alert("broken.csv: UnicodeDecodeError at byte 3")
    assert a.severity == mon.CRITICAL
    assert "could not be read" in a.title


def test_the_three_problem_kinds_get_distinct_ids():
    ids = {mon._source_alert(p).id for p in (
        "x" + clean_mod.SKIPPED_MARKER + "why",
        "x" + clean_mod.UNCONFIGURED_MARKER,
        "x: exploded")}
    assert len(ids) == 3


def test_rejected_cells_warn_rather_than_fail():
    """The row survived and the cell is recorded; that is a warning, not a stop."""
    alerts = mon.from_cleaning([FakeClean(n_rejects=3)], [])
    assert alerts[0].severity == mon.WARNING
    assert "3 cells could not be typed" in alerts[0].title
    assert "rows were kept" in alerts[0].detail


def test_the_reject_threshold_is_configurable():
    assert mon.from_cleaning([FakeClean(n_rejects=3)], [],
                             {"max_rejects": 3}) == []


def test_a_row_count_disagreeing_with_config_is_critical():
    """Either the file changed or the config is wrong; both need a person."""
    results = [FakeClean(rows_in=99, spec=FakeSpec(row_count_expected=100))]
    a = mon.from_cleaning(results, [])[0]
    assert a.severity == mon.CRITICAL
    assert "expects 100 rows, the file has 99" in a.detail


def test_a_matching_row_count_is_silent():
    results = [FakeClean(rows_in=100, spec=FakeSpec(row_count_expected=100))]
    assert mon.from_cleaning(results, []) == []


def test_no_declared_row_count_is_not_a_finding():
    assert mon.from_cleaning([FakeClean(spec=FakeSpec(None))], []) == []


# --------------------------------------------------------------------------- #
# drift -> alerts
# --------------------------------------------------------------------------- #

def test_the_first_run_has_no_baseline_and_says_so():
    alerts = mon.from_drift(None, manifest("v1", a=(10, {"c": 0})))
    assert [a.severity for a in alerts] == [mon.INFO]
    assert "no baseline" in alerts[0].title


def test_an_unchanged_table_is_reported_as_unchanged():
    old = manifest("v1", a=(10, {"c": 0}))
    new = manifest("v2", a=(10, {"c": 0}))
    alerts = mon.from_drift(old, new)
    assert alerts[0].severity == mon.INFO
    assert "unchanged since v1" in alerts[0].title


def test_losing_rows_is_critical_and_gaining_them_is_not():
    """Growth is expected of an append-only source; shrinkage means a bad load."""
    base = manifest("v1", a=(100, {"c": 0}))
    shrunk = mon.from_drift(base, manifest("v2", a=(99, {"c": 0})))
    assert shrunk[0].severity == mon.CRITICAL
    assert "lost rows" in shrunk[0].title
    assert "-1.00%" in shrunk[0].detail
    grown = mon.from_drift(base, manifest("v2", a=(110, {"c": 0})))
    assert grown == [] or all(a.severity != mon.CRITICAL for a in grown)


def test_growth_beyond_tolerance_warns():
    old, new = manifest("v1", a=(100, {"c": 0})), manifest("v2", a=(200, {"c": 0}))
    a = mon.from_drift(old, new, {"row_growth_pct": 25.0})[0]
    assert a.severity == mon.WARNING
    assert "+100.00%" in a.detail


def test_a_vanished_table_is_critical():
    old = manifest("v1", a=(10, {"c": 0}), b=(5, {"c": 0}))
    new = manifest("v2", a=(10, {"c": 0}))
    by_id = {a.id: a for a in mon.from_drift(old, new)}
    assert by_id["drift:b"].severity == mon.CRITICAL
    assert "disappeared" in by_id["drift:b"].title


def test_a_new_table_is_information():
    old = manifest("v1", a=(10, {"c": 0}))
    new = manifest("v2", a=(10, {"c": 0}), b=(5, {"c": 0}))
    by_id = {a.id: a for a in mon.from_drift(old, new)}
    assert by_id["drift:b"].severity == mon.INFO


def test_a_dropped_column_is_critical_unless_schema_change_is_allowed():
    old = manifest("v1", a=(10, {"keep": 0, "gone": 0}))
    new = manifest("v2", a=(10, {"keep": 0}))
    strict = {a.id: a for a in mon.from_drift(old, new)}
    assert strict["drift:cols_removed:a"].severity == mon.CRITICAL
    relaxed = {a.id: a for a in mon.from_drift(old, new,
                                               {"allow_schema_change": True})}
    assert relaxed["drift:cols_removed:a"].severity == mon.INFO


def test_a_new_column_warns_rather_than_failing():
    old = manifest("v1", a=(10, {"c": 0}))
    new = manifest("v2", a=(10, {"c": 0, "extra": 0}))
    by_id = {a.id: a for a in mon.from_drift(old, new)}
    assert by_id["drift:cols_added:a"].severity == mon.WARNING


def test_a_dtype_change_is_critical():
    old = manifest("v1", a=(10, {"c": 0}))
    new = manifest("v2", a=(10, {"c": 0}))
    new.table("a").dtypes["c"] = "string"
    new.table("a").content_hash = "different"
    by_id = {a.id: a for a in mon.from_drift(old, new)}
    assert by_id["drift:dtypes:a"].severity == mon.CRITICAL
    assert "Int64 -> string" in by_id["drift:dtypes:a"].detail


def test_new_nulls_are_flagged_even_though_nothing_else_moved():
    """Same schema, same row count - the failure mode that hides."""
    old = manifest("v1", a=(10, {"c": 0}))
    new = manifest("v2", a=(10, {"c": 4}))
    by_id = {a.id: a for a in mon.from_drift(old, new)}
    assert by_id["drift:nulls:a"].severity == mon.WARNING
    assert "0 -> 4" in by_id["drift:nulls:a"].detail


def test_nulls_going_down_is_not_an_alert():
    old = manifest("v1", a=(10, {"c": 4}))
    new = manifest("v2", a=(10, {"c": 0}))
    assert "drift:nulls:a" not in {a.id for a in mon.from_drift(old, new)}


@pytest.mark.parametrize("change, grew", [
    ("0 -> 4", True), ("4 -> 0", False), ("4 -> 4", False),
    ("nonsense", True),          # unparseable means "assume the worst"
])
def test_null_growth_parsing(change, grew):
    assert mon._null_grew(change) is grew


# --------------------------------------------------------------------------- #
# assembly, verdict and output
# --------------------------------------------------------------------------- #

def test_ok_tracks_critical_not_merely_activity():
    """Warnings and info must not turn a good run into a failing exit code."""
    report = mon.MonitorReport(alerts=[
        mon.Alert("i", mon.INFO, "fyi"), mon.Alert("w", mon.WARNING, "hmm")])
    assert report.ok
    assert "OK with 1 warning" in report.verdict()
    report.alerts.append(mon.Alert("c", mon.CRITICAL, "bad"))
    assert not report.ok
    assert "ALERT - 1 critical, 1 warning(s)" in report.verdict()


def test_an_empty_report_is_ok_and_says_nothing_to_report():
    report = mon.MonitorReport()
    assert report.ok and report.verdict() == "OK - nothing to report"
    assert "Nothing to report" in mon.render_markdown(report)


def test_alerts_are_sorted_by_severity_then_id():
    report = mon.MonitorReport(alerts=[
        mon.Alert("z-info", mon.INFO, "z"), mon.Alert("b-crit", mon.CRITICAL, "b"),
        mon.Alert("a-warn", mon.WARNING, "a"), mon.Alert("a-crit", mon.CRITICAL, "a")])
    assert [a.id for a in report.sorted_alerts()] == ["a-crit", "b-crit",
                                                      "a-warn", "z-info"]


def test_check_can_omit_any_source():
    assert mon.check(thresholds={}).alerts == []
    only_cleaning = mon.check(cleaning=([FakeClean(n_rejects=1)], []),
                              thresholds={"max_rejects": 0})
    assert len(only_cleaning.alerts) == 1


def test_check_records_the_thresholds_it_used():
    report = mon.check(thresholds={"max_rejects": 9})
    assert report.thresholds["max_rejects"] == 9
    assert "`max_rejects`: 9" in mon.render_markdown(report)


def test_markdown_escapes_pipes_so_the_table_survives():
    report = mon.MonitorReport(alerts=[
        mon.Alert("x", mon.WARNING, "a | b", detail="c | d")])
    row = next(line for line in mon.render_markdown(report).splitlines()
               if line.startswith("| warning"))
    assert r"a \| b" in row and r"c \| d" in row


def test_run_writes_both_files_and_is_json_serialisable(tmp_path):
    report, paths = mon.run(validation=FakeReport(results=[FakeResult()]),
                            out_dir=tmp_path, thresholds={})
    assert paths["markdown"].exists() and paths["json"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["counts"]["critical"] == 1
    assert payload["alerts"][0]["source"] == "validation"


def test_run_writes_nowhere_near_the_network(tmp_path):
    """The deliberate absence of a sink. If this changes it is a policy decision."""
    source = Path(mon.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib", "http.client", "smtplib", "socket"):
        assert forbidden not in source, forbidden + " appeared in monitoring.py"
