"""Phase 1.1 regression tests.

Each test names one defect deliberately injected by
scripts/make_synthetic_messy.py. If the profiler stops catching it, the
matching test fails - that is the "done when" gate from project_roadmap.md
made executable.
"""

from __future__ import annotations

import pytest

from dtp import profile


def col(tp: profile.TableProfile, name: str) -> profile.ColumnProfile:
    match = [c for c in tp.columns if c.name == name]
    assert match, name + " not profiled; got " + str([c.name for c in tp.columns])
    return match[0]


def issues_text(c: profile.ColumnProfile) -> str:
    return " || ".join(c.issues).lower()


# --- loading -----------------------------------------------------------------

def test_both_sources_discovered(profiled):
    assert set(profiled) == {"sales_orders", "region_targets"}


def test_delimiter_and_encoding_detected(profiled):
    tsv = profiled["region_targets"]
    assert tsv.delimiter == "\t"
    assert tsv.encoding == "cp1252"
    assert any("not utf-8" in w for w in tsv.load_warnings)


# --- dates: the headline defect ---------------------------------------------

def test_mixed_day_month_order_is_flagged(profiled):
    c = col(profiled["sales_orders"], "order_date")
    assert c.date_order_ambiguous
    assert "mixed day/month order" in issues_text(c)


def test_multiple_date_formats_counted(profiled):
    c = col(profiled["sales_orders"], "order_date")
    assert len(c.date_formats) >= 4, c.date_formats
    assert "date formats in one column" in issues_text(c)


# --- nulls, whitespace, spelling --------------------------------------------

def test_textual_nulls_detected(profiled):
    c = col(profiled["sales_orders"], "region")
    assert {"N/A", "-", "unknown"} <= set(c.sentinel_forms)
    assert c.missing_pct > 10
    assert "nulls stored as text" in issues_text(c)


def test_untidy_whitespace_detected(profiled):
    c = col(profiled["sales_orders"], "customer_name")
    assert c.n_untidy_whitespace > 0
    assert "whitespace" in issues_text(c)


def test_case_and_punctuation_variants_grouped(profiled):
    c = col(profiled["sales_orders"], "country")
    groups = [set(g) for g in c.case_variant_groups.values()]
    assert {"USA", "usa", "U.S.A."} in groups, c.case_variant_groups


def test_mojibake_detected(profiled):
    c = col(profiled["sales_orders"], "customer_name")
    assert c.mojibake_examples
    assert "mojibake" in issues_text(c)


# --- types -------------------------------------------------------------------

def test_boolean_spelled_many_ways(profiled):
    c = col(profiled["sales_orders"], "status")
    assert c.inferred_type == "boolean"
    assert "different spellings" in issues_text(c)


def test_currency_mix_detected(profiled):
    c = col(profiled["sales_orders"], "unit_price")
    assert c.inferred_type == "currency"
    assert "currency notations in one column" in issues_text(c)
    assert c.numeric is not None and c.numeric["n_negative"] > 0


def test_percent_scale_mix_detected(profiled):
    c = col(profiled["sales_orders"], "discount_pct")
    assert c.inferred_type == "percent"
    assert "factor of 100" in issues_text(c)


def test_non_numeric_value_in_int_column(profiled):
    c = col(profiled["sales_orders"], "quantity")
    assert c.inferred_type == "integer"
    assert "three" in issues_text(c)


def test_malformed_emails_flagged(profiled):
    c = col(profiled["sales_orders"], "email")
    assert c.inferred_type == "email"
    assert c.n_type_mismatch > 0


def test_constant_column_flagged(profiled):
    c = col(profiled["sales_orders"], "legacy_flag")
    assert c.is_constant
    assert "constant column" in issues_text(c)


def test_mostly_missing_column_flagged(profiled):
    c = col(profiled["sales_orders"], "internal_note")
    assert c.missing_pct > 50
    assert "over half the values are missing" in issues_text(c)


# --- table level -------------------------------------------------------------

def test_exact_and_normalised_duplicates(profiled):
    tp = profiled["sales_orders"]
    assert tp.n_exact_duplicate_rows == 3
    # the case/whitespace-only variant is caught on top of the exact three
    assert tp.n_normalised_duplicate_rows == 4
    assert tp.n_empty_rows == 1


def test_near_unique_id_flagged(profiled):
    c = col(profiled["sales_orders"], "order_id")
    assert not c.candidate_key
    assert "intended identifier" in issues_text(c)


# --- precision: things that must NOT be flagged ------------------------------

def test_no_outlier_finding_on_tiny_sample(profiled):
    """IQR bounds on 5 values are noise; the profiler must stay quiet."""
    c = col(profiled["region_targets"], "Target_Revenue")
    assert c.outliers is None
    assert "iqr" not in issues_text(c)


def test_numeric_column_not_called_an_identifier(profiled):
    c = col(profiled["sales_orders"], "unit_price")
    assert "intended identifier" not in issues_text(c)


def test_clean_column_has_no_findings(profiled):
    c = col(profiled["region_targets"], "Owner")
    assert c.issues == []


# --- coercion units ---------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234.50", 1234.5),
        ("$1,200", 1200.0),
        ("(500)", -500.0),
        ("(1,234.56)", -1234.56),
        ("45%", 45.0),
        ("1.2e3", 1200.0),
        ("210.14 USD", 210.14),
        ("abc", None),
        ("", None),
    ],
)
def test_to_number(raw, expected):
    assert profile.to_number(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-03-12", "date"),
        ("12/03/2024", "date"),
        ("$99.00", "currency"),
        ("10%", "percent"),
        ("1,200", "integer"),
        ("a@b.com", "email"),
        ("hello world", "text"),
    ],
)
def test_classify_value(raw, expected):
    assert profile.classify_value(raw) == expected


def test_plain_number_is_not_currency():
    """Regression: an unescaped '$' in the currency regex matched any number."""
    assert not profile.RE_CURRENCY.match("1234")
    assert profile.classify_value("1234") == "integer"
