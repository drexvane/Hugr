"""The semantic layer: a metric carries its own correctness gate.

The numbers asserted here are computable by hand from the fixture in `conftest`:
the flat 2017 series is 4 lines a month at $100 sales and $10 profit, one month
holds 12, and the non-baseline rows are dated 2016 and listed in the fixture's
own comment.
"""

from __future__ import annotations

import pandas as pd
import pytest

from dtp import metrics as M


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #

def test_revenue_excludes_lines_that_never_converted(wh):
    """$8,000 of the fixture's sales sit on a cancelled and a fraud line."""
    totals = M.totals(wh, ["revenue", "revenue_ungated"])
    assert totals["revenue"] == pytest.approx(7700.0)
    assert totals["revenue_ungated"] == pytest.approx(15700.0)


def test_the_gate_travels_with_the_metric_not_the_query(wh):
    """Gated and ungated in one result set, which a WHERE clause cannot do.

    This is why the gate is a FILTER on the aggregate: a single query has to be
    able to state the recognised figure and the raw figure side by side, and a
    WHERE would narrow both.
    """
    row = M.totals(wh, ["revenue", "revenue_ungated"])
    assert row["revenue"] < row["revenue_ungated"]
    assert row["n_lines"] == 65          # every row, gate or no gate


def test_delivery_metrics_use_the_shipment_gate(wh):
    """The cancelled lines carry a 4-day delay for shipments that never happened."""
    gated = M.totals(wh, ["shipped_lines", "late_lines", "avg_delay_days"])
    ungated = wh.sql("""
        SELECT count(*) AS shipped_lines,
               count(*) FILTER (WHERE shipping_delay_days > 0) AS late_lines,
               avg(shipping_delay_days) AS avg_delay_days
          FROM order_items
    """).iloc[0]
    assert gated["late_lines"] < ungated["late_lines"]
    assert gated["avg_delay_days"] < ungated["avg_delay_days"]


def test_on_time_and_late_partition_the_shipped_lines(wh):
    row = M.totals(wh, ["shipped_lines", "on_time_lines", "late_lines",
                        "on_time_pct", "late_pct"])
    assert row["on_time_lines"] + row["late_lines"] == row["shipped_lines"]
    assert row["on_time_pct"] + row["late_pct"] == pytest.approx(100.0)


def test_a_gated_composite_expression_is_rejected_at_construction():
    """The bug that shipped once: `FILTER` binds to one aggregate call.

    `100.0 * sum(a) / nullif(sum(b), 0) FILTER (WHERE g)` either fails to parse or -
    the dangerous near-miss - parses and gates only the last aggregate, silently
    computing a fraction with one half filtered. A composite belongs in a `Ratio`,
    and this test fails if anyone reintroduces one as a `Metric`.
    """
    with pytest.raises(ValueError) as err:
        M.Metric("bad", "Bad", "100.0 * sum(order_item_profit) / sum(order_item_sales)",
                 M.REVENUE_GATE, M.PERCENT)
    assert "one aggregate call" in str(err.value)
    assert "Ratio" in str(err.value)


def test_an_ungated_composite_expression_is_allowed():
    """Nothing is wrong with a composite per se - only with gating one."""
    M.Metric("fine", "Fine", "sum(a) / sum(b)", None, M.RATIO)


@pytest.mark.parametrize("expr,single", [
    ("sum(x)", True),
    ("count(*)", True),
    ("count(DISTINCT order_id)", True),
    ("avg(shipping_delay_days)", True),
    ("sum(a) + sum(b)", False),
    ("100.0 * sum(a)", False),
    ("sum(a) / nullif(sum(b), 0)", False),
    ("(sum(a))", False),
    ("x", False),
])
def test_single_call_detection(expr, single):
    assert M._is_single_call(expr) is single


def test_every_gated_metric_in_the_registry_is_a_single_call():
    for key, met in M.METRICS.items():
        if isinstance(met, M.Metric) and met.gate:
            assert M._is_single_call(met.expr), key


# --------------------------------------------------------------------------- #
# ratios are metrics over metrics
# --------------------------------------------------------------------------- #

def test_margin_is_the_two_tiles_beside_it(wh):
    """A rate on a tile must be arithmetically the tiles it sits next to."""
    row = M.totals(wh, ["revenue", "profit", "margin_pct"])
    assert row["margin_pct"] == pytest.approx(100.0 * row["profit"] / row["revenue"])


def test_aov_uses_gated_orders_not_raw_ones(wh):
    """Gated revenue over an ungated order count is wrong in a way no tile shows."""
    row = M.totals(wh, ["revenue", "orders", "aov"])
    assert row["aov"] == pytest.approx(row["revenue"] / row["orders"])
    raw_orders = wh.scalar("SELECT count(DISTINCT order_id) FROM order_items")
    assert row["orders"] < raw_orders


def test_a_ratio_inherits_its_numerators_gate():
    assert M.metric("margin_pct").gate == M.REVENUE_GATE
    assert M.metric("on_time_pct").gate.startswith(M.SHIPMENT_GATE)


def test_a_ratio_never_divides_by_zero(wh):
    """`nullif` on the denominator, so an empty group is null rather than an error."""
    empty = M.aggregate(wh, ["margin_pct"], by=["market"],
                        filters=M.Filters(where={"market": ["Atlantis"]}))
    assert empty.empty


# --------------------------------------------------------------------------- #
# what cannot be asked for
# --------------------------------------------------------------------------- #

def test_unknown_metric_names_the_alternatives(wh):
    with pytest.raises(KeyError) as err:
        M.totals(wh, ["gross_margin"])
    assert "revenue" in str(err.value)


def test_unknown_dimension_is_refused(wh):
    with pytest.raises(KeyError):
        M.aggregate(wh, ["revenue"], by=["moon_phase"])


def test_personal_columns_are_not_dimensions(wh):
    """The privacy constraint holds by construction, not by reviewer discipline.

    `docs/02` promises no view plots a customer name, street or IP. Because a view
    can only group by a registry dimension, that promise is enforced here.
    """
    for column in ("customer_first_name", "customer_last_name", "customer_street",
                   "client_ip", "product_image_url"):
        assert column not in M.DIMENSIONS
        with pytest.raises(KeyError):
            M.aggregate(wh, ["revenue"], by=[column])
    plotted = {d.expr for d in M.DIMENSIONS.values()}
    assert not plotted & {"customer_first_name", "customer_last_name",
                          "customer_street", "client_ip"}


def test_a_measure_cannot_be_grouped(wh):
    """`views` comes from a purpose-built query, so `aggregate` must refuse it.

    Without this the registry would promise a column `aggregate` cannot produce,
    and the failure would be a SQL error naming a column nobody wrote.
    """
    with pytest.raises(KeyError) as err:
        M.aggregate(wh, ["views"], by=["market"])
    assert "purpose-built" in str(err.value)
    with pytest.raises(TypeError):
        M.metric("views").sql()


def test_order_by_must_be_a_selected_key(wh):
    with pytest.raises(KeyError):
        M.aggregate(wh, ["revenue"], by=["market"], order_by="profit")


def test_timeseries_grain_must_be_a_time_dimension(wh):
    with pytest.raises(KeyError):
        M.timeseries(wh, ["revenue"], grain="market")


def test_funnel_grouping_is_restricted(wh):
    with pytest.raises(KeyError):
        M.funnel(wh, by="market")


def test_filter_values_are_bound(wh):
    hostile = "Europe' OR 1=1 --"
    out = M.aggregate(wh, ["revenue"], by=["market"],
                      filters=M.Filters(where={"market": [hostile]}))
    assert out.empty


# --------------------------------------------------------------------------- #
# shapes
# --------------------------------------------------------------------------- #

def test_aggregate_with_no_metrics_is_a_structural_question(wh):
    """"Which markets exist in which months" needs no metric at all."""
    out = M.aggregate(wh, [], by=["market", "month"])
    assert list(out.columns) == ["market", "month", "n_lines"]
    assert len(out) > 0


def test_min_lines_drops_thin_groups_after_aggregation(wh):
    wide = M.aggregate(wh, ["margin_pct"], by=["category"])
    thin = M.aggregate(wh, ["margin_pct"], by=["category"], min_lines=4)
    assert len(thin) < len(wide)
    assert thin["n_lines"].min() >= 4


def test_timeseries_returns_one_row_per_month(wh):
    out = M.timeseries(wh, ["revenue"], grain="month",
                       filters=M.Filters(date_from="2017-01-01"))
    assert len(out) == 12
    assert out["revenue"].tolist() == [400.0] * 6 + [1200.0] + [400.0] * 5


def test_date_filter_includes_the_whole_final_day(wh):
    """The column carries a time, so `<= date_to` would drop that day's rows."""
    one_day = M.totals(wh, ["lines"],
                       filters=M.Filters(date_from="2017-07-15",
                                         date_to="2017-07-15"))
    assert one_day["lines"] == 12


def test_date_bounds_spans_the_fact_table(wh):
    lo, hi = M.date_bounds(wh)
    assert lo == pd.Timestamp("2016-03-15")
    assert hi == pd.Timestamp("2017-12-15")


def test_dimension_values_are_ordered_by_frequency_not_alphabetically(wh):
    """A filter box puts the values a reader will actually pick at the top.

    Women's Apparel carries 58 of the fixture's 65 lines; alphabetical order would
    open the box on a category with one line and bury the one that matters last.
    """
    values = M.dimension_values(wh, "category")
    assert values[0] == "Women's Apparel"
    assert values != sorted(values)
    counts = M.aggregate(wh, [], by=["category"]).set_index("category")["n_lines"]
    assert list(counts[values]) == sorted(counts[values], reverse=True)


def test_equally_common_values_keep_a_stable_order(wh):
    """`ORDER BY n DESC` alone leaves a tie to the scan, so the options reshuffle
    between reruns and a selection remembered by position reopens on a different
    value. The three one-line products come back alphabetically instead."""
    assert M.dimension_values(wh, "product") == [
        "Nike Polo", "Bowling Ball", "Clearance Bat",   # 58, 2, 2
        "Dart Board", "Smart Watch", "Trail Camera",    # 1, 1, 1
    ]
    assert M.dimension_values(wh, "market") == M.dimension_values(wh, "market")


def test_dimension_values_are_capped_and_exclude_nulls(wh):
    assert M.dimension_values(wh, "category", limit=2) == ["Women's Apparel",
                                                           "Sporting Goods"]
    assert None not in M.dimension_values(wh, "order_status")


def test_drill_paths_are_registry_dimensions():
    for path in M.DRILL_PATHS.values():
        for key in path:
            assert key in M.DIMENSIONS


# --------------------------------------------------------------------------- #
# the funnel
# --------------------------------------------------------------------------- #

def test_funnel_totals_are_not_the_columns_summed(wh):
    """Per-group distinct order counts double-count orders that span groups.

    In the real extract that inflates orders by 29%. The headline therefore comes
    from one query over the window, and this test is what stops someone
    "simplifying" it into a `sum()` of the frame.
    """
    totals = M.funnel_totals(wh)
    by_product = M.funnel(wh, by="product")
    assert totals["orders"] == 6
    assert by_product["orders"].sum() >= totals["orders"]


def test_funnel_window_comes_from_the_log(wh):
    totals = M.funnel_totals(wh)
    assert totals["window"] == ("2016-03-01", "2016-06-28")
    # 75 views, 6 gated orders in the window.
    assert totals["views"] == 75
    assert totals["view_to_order_pct"] == pytest.approx(8.0)


def test_funnel_counts_are_zero_filled_and_the_ratio_is_not(wh):
    """A never-ordered product earned $0; a product with no views has no rate."""
    out = M.funnel(wh, by="product")
    dead = out[out["product_key"] == "never sold hat"].iloc[0]
    assert dead["orders"] == 0 and dead["lines"] == 0 and dead["revenue"] == 0
    assert dead["view_to_order_pct"] == 0.0
    unviewed = out[out["views"] == 0]
    assert len(unviewed) > 0
    assert unviewed["view_to_order_pct"].isna().all()


def test_funnel_prefers_the_fact_tables_label_for_display(wh):
    out = M.funnel(wh, by="category")
    row = out[out["category_key"] == "indoor outdoor games"].iloc[0]
    assert row["category"] == "Indoor/Outdoor Games"
    # A group the fact table has never seen keeps the log's spelling, which is
    # itself the finding.
    never = out[out["category_key"] == "featured shops"].iloc[0]
    assert never["category"] == "featured shops"


def test_funnel_joins_the_punctuation_pair(wh):
    """Without the fold this row would be two rows, each missing half its data."""
    out = M.funnel(wh, by="category")
    row = out[out["category_key"] == "indoor outdoor games"].iloc[0]
    assert row["views"] == 20 and row["orders"] == 1


# --------------------------------------------------------------------------- #
# formatting - one place, so a number reads the same everywhere
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,unit,expected", [
    (1234.5, M.MONEY, "$1,234.50"),
    (-40.0, M.MONEY, "-$40.00"),
    (10.75, M.PERCENT, "10.8%"),
    (0.5714, M.DAYS, "0.57 days"),
    (2.9977, M.RATIO, "3.00"),
    (62897.4, M.COUNT, "62,897"),
    (None, M.MONEY, "n/a"),
])
def test_fmt(value, unit, expected):
    assert M.fmt(value, unit) == expected


@pytest.mark.parametrize("value,expected", [
    (35_214_428.98, "$35.21M"),
    (-1_570_305.33, "-$1.57M"),
    (3806.42, "$3.8k"),
    (559.87, "$559.87"),
])
def test_fmt_money_compact_keeps_the_sign_outside(value, expected):
    assert M.fmt(value, M.MONEY, compact=True) == expected


def test_fmt_change_states_percentages_in_points():
    """"Margin is up 0.4%" is ambiguous; "0.4 pp" is not."""
    assert M.fmt_change(0.4, M.PERCENT) == "0.4 pp"
    assert M.fmt_change(800.0, M.MONEY) == "$800.00"


def test_fmt_delta_always_shows_a_direction():
    assert M.fmt_delta(0.0, M.PERCENT) == "+0.0%"
    assert M.fmt_delta(-3.2, M.PERCENT) == "-3.2%"


def test_fmt_dim_names_a_month_not_a_timestamp():
    assert M.fmt_dim("month", pd.Timestamp("2018-01-01")) == "Jan 2018"
    assert M.fmt_dim("quarter", pd.Timestamp("2018-01-01")) == "Q1 2018"
    assert M.fmt_dim("year", pd.Timestamp("2018-01-01")) == "2018"
    assert M.fmt_dim("market", "Europe") == "Europe"


def test_every_registry_entry_has_a_label_and_a_known_unit():
    units = {M.MONEY, M.PERCENT, M.DAYS, M.COUNT, M.RATIO}
    for key, met in M.METRICS.items():
        assert met.label and met.label[0].isupper(), key
        assert met.unit in units, key
        assert met.key == key


def test_money_metrics_are_gated_or_named_ungated():
    """A money metric with no gate must say so in its own key.

    The registry is what Phase 3 hands to a model. If an ungated money metric
    could be called `revenue`, the agent would pick it by name.
    """
    for key, met in M.METRICS.items():
        if isinstance(met, M.Metric) and met.unit == M.MONEY and met.gate is None:
            assert key.endswith("_ungated"), key
