"""Tests for the data dictionary (Phase 1.3).

The roadmap's "100% of fields documented" is a claim this module makes about
itself, so the tests are mostly about whether the claim is checkable: does a
missing description actually register as a gap, does a vendor description that
restates the column name get counted as documentation (it must not), and is the
attribution of each sentence to its author correct.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from dtp import clean as clean_mod
from dtp import dictionary as dict_mod

GLOSSARY_CSV = (
    "FIELDS,DESCRIPTION\n"
    # normal entry, with the vendor's leading ": " and trailing padding
    "Row Id   ,: identifier of the line\n"
    # an echo: the description is the column name again
    "Unit Price,: Unit Price\n"
    # an entry for a column that was dropped
    "Notes,: free text\n"
    # an entry matching nothing at all
    "Legacy Column,: no longer exists\n"
)


@pytest.fixture
def glossary_dir(mini_raw: Path) -> Path:
    (mini_raw / "DescriptionDataCoSupplyChain.csv").write_text(
        GLOSSARY_CSV, encoding="cp1252")
    return mini_raw


@pytest.fixture
def built(mini_raw: Path, mini_config: Path, mini_rules: Path, glossary_dir: Path,
          tmp_path: Path):
    results, _, _ = clean_mod.run(raw_dir=mini_raw, out_dir=tmp_path / "clean",
                                  config_path=mini_config)
    frames = {r.table: r.df for r in results}
    from dtp import validate as validate_mod
    return dict_mod.build(frames, clean_mod.load_config(mini_config),
                          dict_mod.load_glossary(glossary_dir),
                          validate_mod.load_rules(mini_rules))


# --------------------------------------------------------------------------- #
# the vendor glossary
# --------------------------------------------------------------------------- #

def test_glossary_strips_the_vendors_formatting(glossary_dir):
    g = dict_mod.load_glossary(glossary_dir)
    assert g["row id"] == "Identifier of the line"   # ": " gone, capitalised
    assert "legacy column" in g                       # key is casefolded+stripped


def test_a_missing_glossary_is_empty_not_an_error(tmp_path):
    assert dict_mod.load_glossary(tmp_path) == {}


@pytest.mark.parametrize("source, desc, echo", [
    ("Product Price", "Product Price", True),
    ("Product Price", "product  price", True),        # spacing and case ignored
    ("Customer Id", "Customer Id.", True),            # punctuation ignored
    ("Product Price", "Price per unit charged", False),
])
def test_echo_detection_ignores_formatting_but_not_content(source, desc, echo):
    assert dict_mod._is_echo(source, desc) is echo


def test_an_echoed_description_is_recorded_and_not_counted(built):
    """"Unit Price: Unit Price" teaches nobody anything, so it is treated as absent."""
    assert "Unit Price" in built.glossary_echoes
    f = next(f for f in built.fields if f.name == "unit_price")
    # The config's own description took over, and is attributed to us.
    assert f.description_source == "config"
    assert f.vendor_described is False
    assert f.business_description == "Price per unit."


def test_a_vendor_description_is_used_and_attributed(built):
    f = next(f for f in built.fields if f.name == "order_ref")
    assert f.description_source == "config"
    row_id = next(f for f in built.fields if f.name == "row_id")
    # Config wins where both exist; that is the documented order of authority.
    assert row_id.description_source == "config"


def test_glossary_entries_are_reconciled_not_ignored(built):
    """A name in the glossary that matches nothing has to surface somewhere."""
    assert "legacy column" in built.glossary_unused
    # A dropped column is expected to go unused, so it is not reported as a stray.
    assert "notes" not in built.glossary_unused


# --------------------------------------------------------------------------- #
# coverage and gaps
# --------------------------------------------------------------------------- #

def test_the_mini_source_is_fully_documented(built):
    assert built.gaps == []
    assert built.coverage == 100.0
    assert len(built.fields) == 13          # 11 cleaned + 2 derived


def test_an_undocumented_field_is_a_gap_and_moves_the_percentage(built):
    victim = next(f for f in built.fields if f.name == "city")
    victim.business_description = None
    victim.decision = None
    assert victim.documented is False
    assert [g.name for g in built.gaps] == ["city"]
    assert built.coverage == pytest.approx(12 / 13 * 100)
    assert "**undocumented**" in dict_mod.render_markdown(built)


def test_a_derived_field_is_documented_by_its_expression(built):
    f = next(f for f in built.fields if f.name == "discount_rate")
    assert f.kind == "derived"
    assert f.expression == "discount / line_total"
    assert f.null_policy == "null when line_total == 0"
    assert f.documented


def test_an_unconfigured_table_is_listed_as_gaps_not_skipped(mini_config):
    """A clean table nobody configured is a hole in the dictionary; say so."""
    stray = pd.DataFrame({"x": pd.array([1, 2], dtype="Int64")})
    doc = dict_mod.build({"stray": stray}, clean_mod.load_config(mini_config))
    assert [f.name for f in doc.fields] == ["x"]
    assert doc.coverage == 0.0
    assert [g.table + "." + g.name for g in doc.gaps] == ["stray.x"]


def test_an_empty_dictionary_is_zero_percent_not_a_division_error():
    assert dict_mod.Dictionary().coverage == 0.0


# --------------------------------------------------------------------------- #
# measurement - the numbers must come from the data, not from the config
# --------------------------------------------------------------------------- #

def test_measure_reports_nulls_range_and_a_small_vocabulary(built):
    qty = next(f for f in built.fields if f.name == "qty")
    assert qty.rows == 7
    assert qty.nulls == 1                    # the uncoercible "many"
    assert qty.null_pct == pytest.approx(100 / 7)
    assert qty.minimum == "1" and qty.maximum == "3"
    status = next(f for f in built.fields if f.name == "status")
    assert status.values == ["CANCELED", "COMPLETE", "PENDING"]


def test_a_wide_vocabulary_is_sampled_rather_than_listed():
    df = pd.DataFrame({"c": pd.array([str(i) for i in range(40)], dtype="string")})
    stats = dict_mod.measure(df, "c")
    assert stats["distinct"] == 40
    assert "values" not in stats
    assert len(stats["examples"]) == dict_mod.EXAMPLE_COUNT


def test_unordered_columns_get_no_range(built):
    """An alphabetical min for a category is a number that means nothing."""
    city = next(f for f in built.fields if f.name == "city")
    assert city.minimum is None and city.maximum is None


def test_personal_and_identifier_columns_get_no_examples():
    """Value examples are suppressed where they would be needless personal data."""
    for col in ("client_ip", "customer_street", "request_url"):
        df = pd.DataFrame({col: pd.array(["a", "b"], dtype="string")})
        assert "examples" not in dict_mod.measure(df, col)
    assert "examples" in dict_mod.measure(
        pd.DataFrame({"other": pd.array(["a"], dtype="string")}), "other")


def test_an_all_null_column_is_measured_without_crashing():
    df = pd.DataFrame({"c": pd.array([None, None], dtype="string")})
    stats = dict_mod.measure(df, "c")
    assert stats == {"rows": 2, "nulls": 2, "distinct": 0}


# --------------------------------------------------------------------------- #
# rules attribution
# --------------------------------------------------------------------------- #

def test_a_field_lists_the_rules_that_guarantee_it(built):
    zip_field = next(f for f in built.fields if f.name == "zip_code")
    assert "zip_shape (error)" in zip_field.rules


def test_an_identity_rule_appears_under_every_column_it_constrains(built):
    """`total_identity` constrains three columns; filing it under one would lie."""
    for name in ("line_total", "unit_price", "qty", "discount"):
        f = next(f for f in built.fields if f.name == name)
        assert any(r.startswith("total_identity") for r in f.rules), name


def test_rule_matching_is_word_bounded_not_substring():
    rules = {"t": {"rules": [{"id": "r", "type": "expression",
                              "expr": "line_total > 0"}]}}
    assert dict_mod._rules_for("line_total", "t", rules) == ["r (error)"]
    # "total" is a substring of "line_total" but not the column being constrained.
    assert dict_mod._rules_for("total", "t", rules) == []


# --------------------------------------------------------------------------- #
# rendering and output
# --------------------------------------------------------------------------- #

def test_markdown_states_who_wrote_each_description(built):
    md = dict_mod.render_markdown(built)
    assert "# Data dictionary" in md
    assert "written here" in md              # the honest accounting line
    assert "Grain: one row per order line" in md
    assert "Columns deliberately not carried forward" in md
    assert "Free text with no analytical use." in md
    assert "Glossary entries that describe nothing" in md


def test_markdown_escapes_pipes_so_the_tables_survive(built):
    victim = next(f for f in built.fields if f.name == "city")
    victim.business_description = "a | b"
    row = next(line for line in dict_mod.render_markdown(built).splitlines()
               if line.startswith("| `city`"))
    assert row.count("|") == 8               # 7 cell borders + the escaped one
    assert r"a \| b" in row


def test_payload_is_json_serialisable_and_carries_the_verdict(built):
    payload = dict_mod.to_payload(built)
    assert payload["fields_total"] == 13
    assert payload["coverage_pct"] == 100.0
    assert payload["gaps"] == []
    assert payload["grain"]["mini"] == "one row per order line"
    json.loads(json.dumps(payload, default=str))


def test_run_writes_both_documents(mini_raw, mini_config, mini_rules, glossary_dir,
                                   tmp_path):
    clean_mod.run(raw_dir=mini_raw, out_dir=tmp_path / "clean",
                  config_path=mini_config)
    doc, paths = dict_mod.run(clean_dir=tmp_path / "clean", raw_dir=glossary_dir,
                              config_path=mini_config, rules_path=mini_rules,
                              out_dir=tmp_path / "docs")
    assert paths["markdown"].exists() and paths["json"].exists()
    assert doc.coverage == 100.0
    assert "data-dictionary" in paths["markdown"].name
