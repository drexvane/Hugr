"""Tests for the cleaning engine (Phase 1.2).

The mini fixture in conftest carries one instance of each defect the engine
claims to handle, so these tests assert on mechanisms rather than on totals.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from dtp import clean as clean_mod


@pytest.fixture
def cleaned(mini_raw: Path, mini_config: Path):
    config = clean_mod.load_config(mini_config)
    pairs, problems = clean_mod.load_sources(mini_raw, config)
    assert len(pairs) == 1, problems
    table, spec = pairs[0]
    return clean_mod.clean_table(table, spec, config.defaults), problems, config


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

def test_load_config_reads_every_section(mini_config: Path):
    config = clean_mod.load_config(mini_config)
    spec = config.sources["mini"]
    assert spec.table == "mini"
    assert spec.key_columns == ["row_id"]
    assert spec.row_count_expected == 7
    assert len(spec.columns) == 11
    assert len(spec.drop) == 1
    assert len(spec.derived) == 2
    assert spec.grain == "one row per order line"


def test_skip_list_is_reported_not_silent(cleaned):
    _, problems, _ = cleaned
    assert len(problems) == 1
    assert "readme.csv" in problems[0]
    assert clean_mod.SKIPPED_MARKER in problems[0]


def test_unconfigured_file_is_a_distinct_problem_kind(mini_raw, mini_config):
    (mini_raw / "surprise.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    config = clean_mod.load_config(mini_config)
    _, problems = clean_mod.load_sources(mini_raw, config)
    unconfigured = [p for p in problems if clean_mod.UNCONFIGURED_MARKER in p]
    assert len(unconfigured) == 1
    assert "surprise.csv" in unconfigured[0]


def test_for_table_matches_key_or_table_name(mini_config: Path):
    config = clean_mod.load_config(mini_config)
    assert config.for_table("mini") is config.for_table("MINI")
    assert config.for_table("nope") is None


# --------------------------------------------------------------------------- #
# text normalisation, row fixes, value maps
# --------------------------------------------------------------------------- #

def test_whitespace_is_trimmed_and_collapsed(cleaned):
    result, _, _ = cleaned
    df = result.df
    assert df.loc[1, "city"] == "Jersey City"      # was "Jersey  City"
    assert df.loc[1, "ordered_on"] == pd.Timestamp("2021-03-04 10:30")
    steps = result.counts_by_step()
    assert steps["trim_whitespace"] > 0
    assert steps["collapse_spaces"] > 0


def test_sentinel_null_becomes_na_not_the_literal_string(cleaned):
    result, _, _ = cleaned
    assert result.counts_by_step()["sentinel_null"] > 0
    # "N/A" lived in Notes, which is dropped; the count is the evidence it was
    # recognised before the column went away.
    assert "notes" not in result.df.columns


def test_row_fix_shifts_only_the_affected_row(cleaned):
    result, _, _ = cleaned
    df = result.df
    assert result.counts_by_step()["row_fix:geo_shift"] == 1
    assert df.loc[2, "zip_code"] == "02120"
    assert df.loc[2, "region"] == "East"
    # City is left null rather than invented.
    assert pd.isna(df.loc[2, "city"])
    # Every other row is untouched.
    assert df.loc[0, "city"] == "Boston"


def test_value_map_applies_to_configured_column_only(cleaned):
    result, _, _ = cleaned
    assert set(result.df["region"].dropna().unique()) == {"East", "West", "Central"}
    assert result.counts_by_step()["value_map"] == 2


def test_row_fix_condition_rejects_unknown_column(mini_raw, mini_config):
    config = clean_mod.load_config(mini_config)
    spec = config.sources["mini"]
    spec.row_fixes = [{"id": "bogus", "where": "Nope is blank",
                       "do": "shift_right", "columns": ["City"]}]
    pairs, _ = clean_mod.load_sources(mini_raw, config)
    with pytest.raises(KeyError, match="unknown column"):
        clean_mod.clean_table(pairs[0][0], spec, config.defaults)


# --------------------------------------------------------------------------- #
# typing, quarantine, transforms
# --------------------------------------------------------------------------- #

def test_declared_types_are_applied(cleaned):
    df = cleaned[0].df
    assert str(df["row_id"].dtype) == "Int64"
    assert str(df["unit_price"].dtype) == "Float64"
    assert str(df["zip_code"].dtype).startswith("string")
    assert str(df["region"].dtype) == "category"
    assert pd.api.types.is_datetime64_any_dtype(df["ordered_on"])
    assert str(df["is_revenue"].dtype) == "boolean"


def test_bad_cell_loses_the_cell_not_the_row(cleaned):
    result, _, _ = cleaned
    assert result.rows_in == result.rows_out == 7
    assert result.n_rejects == 1
    reject = result.rejects.iloc[0]
    assert reject["column"] == "qty"
    assert reject["raw_value"] == "many"
    assert reject["row_key"] == "7"
    # The row survived with a null in the offending cell.
    assert pd.isna(result.df.loc[6, "qty"])
    assert result.df.loc[6, "unit_price"] == 4.0


def test_zero_pad_reconstructs_lost_leading_zeros(cleaned):
    df = cleaned[0].df
    assert df.loc[1, "zip_code"] == "07030"     # source had "7030"
    assert df.loc[0, "zip_code"] == "02110"     # already padded, unchanged
    # Only values that actually needed padding are counted.
    assert cleaned[0].counts_by_step()["transform:zero_pad"] == 1


def test_zero_pad_refuses_to_truncate():
    rule = clean_mod.ColumnRule(source="Z", clean="z", type="text",
                                transform="zero_pad", width=3)
    s = pd.Series(["1234"], dtype="string")
    result = clean_mod.CleanTable(name="t", table="t", df=pd.DataFrame(),
                                  rejects=pd.DataFrame())
    with pytest.raises(ValueError, match="longer than"):
        clean_mod.apply_transform(s, rule, result)


def test_money_is_rounded_to_cents(cleaned):
    df = cleaned[0].df
    assert df["unit_price"].dropna().map(lambda v: v == round(v, 2)).all()


# --------------------------------------------------------------------------- #
# the expression evaluator - the part that must not become eval()
# --------------------------------------------------------------------------- #

@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame({
        "a": pd.array([1, 2, 3], dtype="Int64"),
        "b": pd.array([10.0, 0.0, 5.0], dtype="Float64"),
        "s": pd.array(["x", "y", "x"], dtype="string"),
    })


@pytest.mark.parametrize("expr, expected", [
    ("a + 1", [2, 3, 4]),
    ("a * 2 - 1", [1, 3, 5]),
    ("b / 5", [2.0, 0.0, 1.0]),
    ("a > 1", [False, True, True]),
    ("a >= 2 and b > 0", [False, False, True]),
    ("s in ['x']", [True, False, True]),
    ("s not in ['x']", [False, True, False]),
    ("-a", [-1, -2, -3]),
])
def test_eval_expr_handles_the_whitelisted_grammar(frame, expr, expected):
    got = clean_mod.eval_expr(expr, frame)
    assert list(got) == expected


def test_eval_expr_supplies_row_number(frame):
    assert list(clean_mod.eval_expr(clean_mod.ROW_NUMBER, frame)) == [1, 2, 3]


@pytest.mark.parametrize("expr", [
    "__import__('os').system('echo hi')",
    "open('secret.txt').read()",
    "a.map(print)",
    "[x for x in a]",
    "lambda: 1",
    "a if b else 0",
])
def test_eval_expr_refuses_anything_outside_the_grammar(frame, expr):
    with pytest.raises(ValueError):
        clean_mod.eval_expr(expr, frame)


def test_eval_expr_reports_unparseable_expressions(frame):
    with pytest.raises(ValueError, match="cannot parse"):
        clean_mod.eval_expr("a +", frame)


def test_eval_expr_rejects_unknown_names(frame):
    with pytest.raises((KeyError, ValueError)):
        clean_mod.eval_expr("nope + 1", frame)


# --------------------------------------------------------------------------- #
# derived columns, de-duplication, run()
# --------------------------------------------------------------------------- #

def test_derived_columns_are_computed_from_config(cleaned):
    df = cleaned[0].df
    assert df.loc[0, "discount_rate"] == pytest.approx(2.0 / 18.0, abs=1e-6)
    assert bool(df.loc[3, "is_revenue"]) is False
    assert bool(df.loc[0, "is_revenue"]) is True


def test_null_when_guards_division_by_zero(cleaned):
    df = cleaned[0].df
    # line_total is 0 on these rows, so the rate is null rather than inf.
    assert pd.isna(df.loc[4, "discount_rate"])
    assert pd.isna(df.loc[5, "discount_rate"])
    assert not df["discount_rate"].isin([float("inf"), float("-inf")]).any()


def test_deduplicate_exact_rows_counts_what_it_removed():
    df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    spec = clean_mod.SourceRules(key="t", table="t",
                                 deduplicate={"strategy": "exact_rows"})
    result = clean_mod.CleanTable(name="t", table="t", df=df,
                                 rejects=pd.DataFrame())
    out = clean_mod.deduplicate(df, spec, result)
    assert len(out) == 2
    assert result.counts_by_step()["deduplicate"] == 1


def test_dropped_columns_are_gone_and_recorded(cleaned):
    result, _, _ = cleaned
    assert "notes" not in result.df.columns
    assert result.spec is not None
    assert result.spec.drop_reasons["Notes"].startswith("Free text")


def test_run_writes_parquet_and_reports(mini_raw, mini_config, reports, tmp_path):
    out = tmp_path / "clean"
    results, problems, paths = clean_mod.run(
        raw_dir=mini_raw, out_dir=out, config_path=mini_config,
        reports_dir=reports)
    assert len(results) == 1
    assert (out / "mini.parquet").exists()
    assert (out / "mini__rejects.parquet").exists()
    assert paths["markdown"].exists()
    assert paths["json"].exists()
    # The Parquet round-trips: what validation reads is what cleaning produced.
    back = pd.read_parquet(out / "mini.parquet")
    assert list(back.columns) == list(results[0].df.columns)
    assert len(back) == 7


def test_run_is_idempotent(mini_raw, mini_config, reports, tmp_path):
    first = clean_mod.run(raw_dir=mini_raw, out_dir=tmp_path / "a",
                          config_path=mini_config, reports_dir=reports)[0][0]
    second = clean_mod.run(raw_dir=mini_raw, out_dir=tmp_path / "b",
                           config_path=mini_config, reports_dir=reports)[0][0]
    pd.testing.assert_frame_equal(first.df, second.df)



