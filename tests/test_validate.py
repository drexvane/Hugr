"""Tests for the validation engine (Phase 1.2).

Each checker gets a frame built to violate it by a known amount, because a
validation layer whose failures are untested is a validation layer nobody has
checked. The severity model gets its own tests: `expect_violations` must fail in
both directions, and a rule that cannot run must never be a mere warning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from dtp import validate as validate_mod


def rule(**kwargs) -> validate_mod.Rule:
    spec = {k: v for k, v in kwargs.items()
            if k not in ("id", "type", "severity", "reason", "expect_violations")}
    return validate_mod.Rule(
        id=kwargs.get("id", "r"),
        type=kwargs["type"],
        severity=kwargs.get("severity", "error"),
        reason=kwargs.get("reason"),
        expect_violations=kwargs.get("expect_violations"),
        spec=spec,
    )


def check(r: validate_mod.Rule, df: pd.DataFrame,
          tables: dict[str, pd.DataFrame] | None = None) -> validate_mod.Result:
    return validate_mod._run_rule(r, "t", df, tables or {"t": df})


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame({
        "id": pd.array([1, 2, 3, 4], dtype="Int64"),
        "dup": pd.array([1, 1, 2, 3], dtype="Int64"),
        "qty": pd.array([1, 2, 0, None], dtype="Int64"),
        "money": pd.array([10.0, 20.0, 30.0, 40.0], dtype="Float64"),
        "rate": pd.array([0.5, 0.5, 0.5, 0.5], dtype="Float64"),
        "name": pd.array(["a", "b", None, "d"], dtype="string"),
        "code": pd.array(["01234", "5678", "abcde", "99999"], dtype="string"),
        "status": pd.array(["OK", "OK", "BAD", "OK"], dtype="string"),
        "grp": pd.array(["x", "x", "y", "y"], dtype="string"),
        "label": pd.array(["p", "q", "r", "r"], dtype="string"),
    })


# --------------------------------------------------------------------------- #
# individual checkers
# --------------------------------------------------------------------------- #

def test_not_null_counts_null_cells(df):
    r = check(rule(type="not_null", columns=["name", "qty"]), df)
    assert r.n_violations == 2          # one null in each
    assert not r.passed


def test_not_null_passes_on_complete_columns(df):
    assert check(rule(type="not_null", columns=["id"]), df).passed


def test_unique_finds_duplicates(df):
    r = check(rule(type="unique", columns=["dup"]), df)
    assert r.n_violations == 2          # the two rows sharing value 1
    assert check(rule(type="unique", columns=["id"]), df).passed


def test_range_exempts_nulls(df):
    r = check(rule(type="range", column="qty", min=1), df)
    assert r.n_violations == 1          # the 0; the null is not a violation
    assert "qty" in r.detail


def test_range_checks_both_bounds(df):
    assert check(rule(type="range", column="money", min=0, max=40), df).passed
    assert check(rule(type="range", column="money", max=30), df).n_violations == 1


def test_allowed_values_reports_the_offenders(df):
    r = check(rule(type="allowed_values", column="status", values=["OK"]), df)
    assert r.n_violations == 1
    assert "BAD" in " ".join(r.samples)


def test_regex_exempts_nulls_but_not_bad_shapes(df):
    r = check(rule(type="regex", column="code", pattern=r"^[0-9]{5}$"), df)
    assert r.n_violations == 2          # "5678" and "abcde"


def test_identity_tolerance_is_respected(df):
    ok = check(rule(type="identity", lhs="money", rhs="money * rate * 2",
                    tol_abs=0.005), df)
    assert ok.passed
    tight = check(rule(type="identity", lhs="money", rhs="money * rate",
                       tol_abs=0.005), df)
    assert tight.n_violations == 4


def test_expression_counts_rows_where_it_is_false(df):
    r = check(rule(type="expression", expr="qty >= 1"), df)
    assert r.n_violations == 1          # the 0
    assert check(rule(type="expression", expr="money > 0"), df).passed


def test_expression_exempts_unevaluable_rows_but_says_so(df):
    """A null operand means "not checked", not "failed" - and it must show."""
    r = check(rule(type="expression", expr="qty >= 1"), df)
    assert r.n_checked == 3            # the null row was not evaluable
    assert "not evaluable" in r.detail
    # The same column under `range` must agree, which is the point of the rule.
    assert (check(rule(type="range", column="qty", min=1), df).n_violations
            == r.n_violations)


def test_consistency_detects_a_broken_functional_dependency(df):
    assert check(rule(type="consistency", determinant="grp",
                      dependent="grp"), df).passed
    r = check(rule(type="consistency", determinant="grp", dependent="label"), df)
    assert r.n_violations == 1          # grp 'x' maps to both 'p' and 'q'


def test_referential_counts_distinct_values_not_rows(df):
    other = pd.DataFrame({"key": pd.array(["x"], dtype="string")})
    r = check(rule(type="referential", column="grp", references="o.key"), df,
              {"t": df, "o": other})
    # Two rows hold 'y', but that is one unmatched value.
    assert r.n_violations == 1


def test_referential_can_fold_case(df):
    other = pd.DataFrame({"key": pd.array(["X", "Y"], dtype="string")})
    tables = {"t": df, "o": other}
    assert check(rule(type="referential", column="grp", references="o.key",
                      casefold=True), df, tables).passed
    assert not check(rule(type="referential", column="grp",
                          references="o.key"), df, tables).passed


def test_min_rows_and_no_duplicate_rows(df):
    assert check(rule(type="min_rows", min_rows=4), df).passed
    assert not check(rule(type="min_rows", min_rows=5), df).passed
    assert check(rule(type="no_duplicate_rows"), df).passed
    doubled = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    assert not check(rule(type="no_duplicate_rows"), doubled).passed


# --------------------------------------------------------------------------- #
# the severity model - what decides whether a run may publish
# --------------------------------------------------------------------------- #

def test_expect_violations_fails_in_both_directions(df):
    """The whole point of pinning a count: drifting *down* is news too."""
    exact = check(rule(type="not_null", columns=["name"], severity="warn",
                       expect_violations=1), df)
    assert exact.passed and exact.n_violations == 1
    too_many = check(rule(type="not_null", columns=["name"],
                          expect_violations=0), df)
    assert not too_many.passed
    too_few = check(rule(type="not_null", columns=["name"],
                         expect_violations=2), df)
    assert not too_few.passed


def test_a_warn_rule_does_not_fail_the_run(df):
    report = validate_mod.validate_tables(
        {"t": df},
        {"t": {"rules": [
            {"id": "w", "type": "not_null", "columns": ["name"], "severity": "warn"},
            {"id": "e", "type": "not_null", "columns": ["id"], "severity": "error"},
        ]}})
    assert report.ok
    assert report.counts == {"rules": 2, "passed": 1, "failed": 0, "warned": 1}
    assert "PASS with 1 warning" in report.verdict()


def test_an_error_rule_fails_the_run_and_says_no_snapshot(df):
    report = validate_mod.validate_tables(
        {"t": df},
        {"t": {"rules": [{"id": "e", "type": "not_null",
                          "columns": ["name"], "severity": "error"}]}})
    assert not report.ok
    assert "no snapshot should be published" in report.verdict()


def test_an_unrunnable_rule_is_an_error_even_when_declared_warn(df):
    """An uncheckable check is worse than a failing one, so warn cannot hide it."""
    r = check(rule(type="regex", column="nope", pattern="^x$", severity="warn"), df)
    assert r.error and not r.passed
    assert r.severity == "error"        # overridden, not respected
    assert r.status == "FAIL"


def test_one_broken_rule_still_lets_the_others_report(df):
    report = validate_mod.validate_tables(
        {"t": df},
        {"t": {"rules": [
            {"id": "broken", "type": "referential", "column": "grp",
             "references": "absent.key"},
            {"id": "fine", "type": "unique", "columns": ["id"]},
        ]}})
    assert report.counts["rules"] == 2
    assert report.counts["passed"] == 1
    assert not report.ok
    assert "referenced table not available" in report.results[0].error


def test_a_missing_table_fails_the_run_rather_than_being_skipped(df):
    report = validate_mod.validate_tables({"t": df}, {"absent": {"min_rows": 1}})
    assert report.missing_tables == ["absent"]
    assert not report.ok
    assert "expected table(s) absent" in report.verdict()


def test_table_level_properties_become_real_rules(df):
    report = validate_mod.validate_tables(
        {"t": df}, {"t": {"min_rows": 1, "no_duplicate_rows": True}})
    assert [r.rule.id for r in report.results] == ["min_rows", "no_duplicate_rows"]
    assert report.ok


@pytest.mark.parametrize("raw, message", [
    ({"id": "x", "type": "not_null", "severity": "loud"}, "unknown severity"),
    ({"id": "x", "type": "vibes"}, "unknown type"),
])
def test_a_malformed_rule_is_rejected_at_parse_time(raw, message):
    """Config errors surface as config errors, not as a rule that quietly no-ops."""
    with pytest.raises(ValueError, match=message):
        validate_mod._parse_rule(raw)


# --------------------------------------------------------------------------- #
# reporting and the real config
# --------------------------------------------------------------------------- #

def test_report_records_the_pinned_count_and_the_rationale(df, tmp_path):
    report = validate_mod.validate_tables(
        {"t": df},
        {"t": {"rules": [{"id": "known_gap", "type": "not_null",
                          "columns": ["name"], "severity": "warn",
                          "expect_violations": 0,
                          "reason": "Pinned so it cannot grow."}]}})
    md = validate_mod.render_markdown(report)
    assert "expected exactly 0" in md
    assert "Pinned so it cannot grow." in md
    payload = validate_mod.to_payload(report)
    assert payload["results"][0]["expect_violations"] == 0
    # The payload has to survive json.dumps; a numpy bool or int64 in there would
    # only be discovered by whoever next reads the report.
    json.loads(json.dumps(payload, default=str))


def test_run_writes_both_reports(mini_raw, mini_config, mini_rules, tmp_path):
    from dtp import clean as clean_mod

    results, _, _ = clean_mod.run(raw_dir=mini_raw, out_dir=tmp_path / "clean",
                                  config_path=mini_config)
    frames = {r.table: r.df for r in results}
    report, paths = validate_mod.run(rules_path=mini_rules, tables=frames)
    assert paths["markdown"].exists() and paths["json"].exists()
    assert report.counts["rules"] == 10
    assert report.ok, report.verdict()


def test_the_shipped_rules_parse(df):
    """Every rule in config/validation_rules.yml is well-formed, checked without
    needing the real data - a typo there should fail the suite, not a nightly run."""
    cfg = validate_mod.load_rules()
    assert set(cfg) == {"order_items", "access_logs"}
    parsed = [validate_mod._parse_rule(r)
              for table in cfg.values() for r in (table.get("rules") or [])]
    assert len(parsed) > 30
    assert all(p.type in validate_mod.CHECKERS for p in parsed)
    ids = [p.id for p in parsed]
    assert len(ids) == len(set(ids)), "rule ids must be unique to be citable"
    # Every pinned count is a real number, and every warn rule explains itself.
    for p in parsed:
        if p.expect_violations is not None:
            assert isinstance(p.expect_violations, int)
            assert p.reason, p.id + " pins a count without saying why"
