"""Phase 1.1 schema-reconciliation tests."""

from __future__ import annotations

import pytest

from dtp import schema_map


@pytest.fixture(scope="module")
def matrix(profile_list):
    return schema_map.build_matrix(profile_list)


def find(matrix, left_col: str, right_col: str):
    for m in matrix.matches:
        if {m.left.column, m.right.column} == {left_col, right_col}:
            return m
    raise AssertionError(
        "no match for " + left_col + "/" + right_col
        + "; got " + str([(m.left.column, m.right.column) for m in matrix.matches])
    )


# --- name handling ----------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Region_Name", ["region", "name"]),
        ("regionName", ["region", "name"]),
        ("REGION NAME", ["region", "name"]),
        ("order-date", ["order", "date"]),
        ("Target_Revenue", ["target", "revenue"]),
    ],
)
def test_tokenise(raw, expected):
    assert schema_map.tokenise(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("order_date", "snake_case"),
        ("Region_Name", "Mixed_Pascal_Snake"),
        ("regionName", "camelCase"),
        ("RegionName", "PascalCase"),
        ("region", "single-word"),
        ("FY", "single-word"),
        ("Owner", "single-word"),
    ],
)
def test_naming_convention(raw, expected):
    assert schema_map.naming_convention(raw) == expected


def test_single_word_names_do_not_count_as_a_convention(matrix):
    """A table with `region` and `order_date` is consistent, not mixed."""
    orders = matrix.conventions["sales_orders"]
    assert orders.get("single-word", 0) > 0
    structural = {
        k: v for k, v in orders.items() if k in schema_map.STRUCTURAL_CONVENTIONS
    }
    assert list(structural) == ["snake_case"]
    assert not any("sales_orders` mixes" in f for f in matrix.findings)


def test_name_similarity_matches_across_conventions():
    assert schema_map.name_similarity("region", "Region_Name") == 1.0
    assert schema_map.name_similarity("country", "Country_Code") == 1.0
    assert schema_map.name_similarity("region", "unit_price") < 0.5


# --- the findings that matter ------------------------------------------------

def test_matching_region_columns_are_joinable(matrix):
    m = find(matrix, "region", "Region_Name")
    assert m.value_overlap == 1.0
    assert m.verdict == "same concept, directly joinable"
    assert m.proposed_name == "region"


def test_country_vs_country_code_needs_a_crosswalk(matrix):
    """Names match perfectly, values do not overlap at all - the trap case."""
    m = find(matrix, "country", "Country_Code")
    assert m.name_score == 1.0
    assert m.value_overlap == 0.0
    assert m.verdict.startswith("NOT joinable")
    assert "crosswalk" in m.proposed_name
    # both original names must survive the proposal
    assert "country_code" in m.proposed_name and "country" in m.proposed_name


def test_convention_disagreement_across_sources_flagged(matrix):
    assert any("sources disagree on naming convention" in f for f in matrix.findings)


def test_unjoinable_pair_is_promoted_to_a_finding(matrix):
    assert any("NOT joinable" in f for f in matrix.findings)


def test_single_source_columns_listed(matrix):
    solo = matrix.single_source_columns
    assert "unit_price" in solo["sales_orders"]
    assert "Owner" in solo["region_targets"]
    # a matched column must not also be listed as single-source
    assert "region" not in solo["sales_orders"]


def test_markdown_renders(matrix):
    md = schema_map.render_markdown(matrix)
    assert "# Schema Inconsistency Matrix" in md
    assert "Country_Code" in md
    assert md.count("|") > 20
