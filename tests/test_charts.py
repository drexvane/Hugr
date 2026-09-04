"""The chart layer: a shape rule, and figures that state what they exclude.

`choose()` is a function of shape alone, so it is tested exhaustively and by table.
The figures are checked through the Plotly objects rather than by rendering: what
matters is that the axis carries the registry's unit, that a gate is named in the
subtitle, and that nothing distinguishes two groups by colour alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest

from dtp import charts as C
from dtp import insights as I
from dtp import metrics as M

SRC = Path(__file__).resolve().parents[1] / "src" / "dtp" / "charts.py"


@pytest.fixture
def series_2017(wh):
    return M.timeseries(wh, ["revenue"], grain="month",
                        filters=M.Filters(date_from="2017-01-01"))


@pytest.fixture
def grid(wh):
    """On-time rate by market and shipping mode: 4 of 6 cells exist, one is 0%."""
    return M.aggregate(wh, ["on_time_pct"], by=["market", "shipping_mode"])


def _subtitle(fig: go.Figure) -> str:
    """The grey line under the heading, which is where a gate is named."""
    text = fig.layout.title.text
    if "<br>" not in text:
        return ""
    return text.split("<br>", 1)[1].split(">", 1)[1].replace("</span>", "")


# --------------------------------------------------------------------------- #
# the layer boundary
# --------------------------------------------------------------------------- #

def test_the_chart_layer_knows_nothing_about_streamlit():
    """Roadmap 3.2 has the Phase 3 agent reuse these figures rather than rewrite them.

    That only holds while a figure is a value: a DataFrame and registry keys in, a
    `go.Figure` out. One Streamlit call here and the agent would need its own chart
    code, and the two would drift.
    """
    import ast

    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0] if node.level == 0 else "dtp")
    assert imported <= {"__future__", "typing", "pandas", "plotly", "dtp"}, imported


# --------------------------------------------------------------------------- #
# the shape rule
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("dims,metric_keys,n_rows,expected", [
    # No metric is not a chart, whatever the dimensions.
    (["month"], [], 40, "table"),
    ([], ["revenue"], 0, "kpi"),
    # A time dimension wins over every other rule, including the scatter rule -
    # a series plotted as anything else destroys the one thing it is for.
    (["month"], ["revenue"], 40, "line"),
    (["month"], ["revenue", "margin_pct"], 40, "line"),
    (["quarter"], ["revenue"], 8, "line"),
    (["year"], ["revenue"], 3, "line"),
    (["month", "market"], ["revenue"], 40, "line_grouped"),
    (["market", "month"], ["revenue"], 40, "line_grouped"),
    # Two dimensions, one metric: a grid, not twenty bars to pair up by colour.
    (["market", "shipping_mode"], ["revenue"], 40, "heatmap"),
    # Two metrics over one dimension: the question is the relationship.
    (["category"], ["revenue", "margin_pct"], 40, "scatter"),
    (["category"], ["revenue", "margin_pct", "units"], 40, "scatter"),
    # One dimension, one metric: bars, turned sideways once the labels crowd.
    (["market"], ["revenue"], 6, "bar"),
    (["market"], ["revenue"], 7, "hbar"),
    (["product"], ["revenue"], 40, "hbar"),
    # Delay days are ordered integers and must stay in their own order.
    (["delay_days"], ["lines"], 7, "bar"),
    # No honest chart: say so instead of implying a comparison.
    (["market", "category", "month"], ["revenue"], 40, "table"),
    (["month", "market"], ["revenue", "margin_pct"], 40, "table"),
])
def test_choose(dims, metric_keys, n_rows, expected):
    assert C.choose(dims, metric_keys, n_rows) == expected


def test_choose_reads_no_data(wh):
    """Shape alone, so the dashboard and the agent cannot disagree about it."""
    assert C.choose(["market"], ["revenue"], 3) == C.choose(["market"],
                                                            ["revenue"], 3)
    assert C.choose([], []) == "table"


def test_auto_figure_returns_none_where_no_chart_is_honest(series_2017):
    """None rather than an exception: the caller renders the rows instead."""
    assert C.auto_figure(series_2017, [], ["revenue"]) is None
    assert C.auto_figure(series_2017, ["a", "b", "c"], ["revenue"]) is None
    assert C.auto_figure(series_2017, ["month"], []) is None


def test_auto_figure_draws_what_choose_picked(wh, series_2017, grid):
    products = M.aggregate(wh, ["revenue"], by=["product"], order_by="revenue")
    grouped = M.aggregate(wh, ["revenue"], by=["month", "category"])
    cats = M.aggregate(wh, ["revenue", "margin_pct"], by=["category"])
    assert C.auto_figure(series_2017, ["month"], ["revenue"]).data[0].mode \
        == "lines+markers"
    assert len(C.auto_figure(grouped, ["month", "category"], ["revenue"]).data) == 5
    assert isinstance(C.auto_figure(grid, ["market", "shipping_mode"],
                                    ["on_time_pct"]).data[0], go.Heatmap)
    assert C.auto_figure(cats, ["category"], ["revenue", "margin_pct"]).data[0].mode \
        == "markers"
    assert isinstance(C.auto_figure(products, ["product"], ["revenue"]).data[0],
                      go.Bar)


# --------------------------------------------------------------------------- #
# the axis and the subtitle come from the registry, not from the caller
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("unit,key,value", [
    (M.MONEY, "tickprefix", "$"),
    (M.PERCENT, "ticksuffix", "%"),
    (M.COUNT, "tickformat", "~s"),
])
def test_a_unit_formats_its_own_axis(unit, key, value):
    """No caller passes a format string, so no caller can pass the wrong one."""
    assert C._axis(unit, "T")[key] == value


def test_days_and_ratios_take_no_affix():
    assert "tickprefix" not in C._axis(M.DAYS, "T")
    assert "ticksuffix" not in C._axis(M.RATIO, "T")


def test_a_money_series_gets_a_dollar_axis_without_being_asked(series_2017):
    fig = C.line_series(series_2017, "month", ["revenue"])
    assert fig.layout.yaxis.tickprefix == "$"
    assert fig.layout.yaxis.title.text == "Revenue"
    assert "$" in fig.data[0].hovertemplate


def test_the_gate_is_named_in_the_subtitle(series_2017):
    """A total that quietly excludes 8,000 dollars is not wrong, but it is
    unattributed, and the reader cannot reconcile it with a raw figure."""
    assert _subtitle(C.line_series(series_2017, "month", ["revenue"])) == \
        "cancelled and suspected-fraud lines excluded"


def test_an_ungated_series_beside_a_gated_one_is_labelled_as_such():
    assert "one series is ungated" in C._gate_note(["revenue", "revenue_ungated"])
    assert "one series is ungated" not in C._gate_note(["revenue"])


def test_the_shipment_gate_has_its_own_wording():
    assert C._gate_note(["on_time_pct"]) == \
        "shipments that never happened excluded"
    both = C._gate_note(["revenue", "on_time_pct"])
    assert both.count(";") == 1


def test_every_figure_is_titled(wh, series_2017, grid):
    """A chart pasted into a document has to say what it is on its own."""
    products = M.aggregate(wh, ["revenue"], by=["product"], order_by="revenue")
    figures = [
        C.line_series(series_2017, "month", ["revenue"]),
        C.bar(products, "product", "revenue"),
        C.scatter(M.aggregate(wh, ["revenue", "margin_pct"], by=["category"]),
                  "category", "revenue", "margin_pct"),
        C.heatmap(grid, "market", "shipping_mode", "on_time_pct"),
        C.line_grouped(M.aggregate(wh, ["revenue"], by=["month", "category"]),
                       "month", "category", "revenue"),
    ]
    for fig in figures:
        title = fig.layout.title.text
        assert title.startswith("<b>") and len(title) > 8
        assert title.split("<br>")[0].strip("<b>/") [0].isupper()


def test_a_figure_survives_the_json_round_trip_the_agent_will_use(series_2017):
    """Phase 3 hands the figure back as JSON rather than redrawing it."""
    fig = C.line_series(series_2017, "month", ["revenue"],
                        anomalies=I.find_anomalies(series_2017, "revenue",
                                                   label_col="month"))
    payload = json.loads(fig.to_json())
    assert len(payload["data"]) == 2
    assert go.Figure(payload).layout.yaxis.tickprefix == "$"


# --------------------------------------------------------------------------- #
# colour never carries meaning on its own
# --------------------------------------------------------------------------- #

def test_a_flagged_point_is_ringed_labelled_and_legended(series_2017):
    """Three signals, one of which is colour. A reader who sees no colour at all
    still finds the outlier."""
    found = I.find_anomalies(series_2017, "revenue", label_col="month")
    fig = C.line_series(series_2017, "month", ["revenue"], anomalies=found)
    flag = fig.data[-1]
    assert flag.name == "flagged"
    assert flag.mode == "markers+text"
    assert flag.text == ("Jul 2017 $1.2k",)          # the label is the second signal
    assert flag.marker.size == 15                    # the ring is the third
    assert flag.marker.color == "rgba(0,0,0,0)"      # hollow, so the point shows
    assert fig.layout.showlegend is True


def test_an_unflagged_series_draws_no_flag_trace_and_hides_the_legend(series_2017):
    fig = C.line_series(series_2017, "month", ["revenue"])
    assert len(fig.data) == 1
    assert fig.layout.showlegend is False


def test_an_anomaly_from_another_frame_is_not_drawn(series_2017):
    """The caption and the chart must come from the same frame; a label that is
    not on this axis would otherwise be plotted at an invented position."""
    stray = [I.Anomaly("Jan 2019", 99.0, 9.9, 400.0, "above")]
    fig = C.line_series(series_2017, "month", ["revenue"], anomalies=stray)
    assert len(fig.data) == 1


def test_a_bar_that_misses_its_benchmark_is_hatched_as_well_as_recoloured(wh):
    bench = I.benchmark_vs_parent(wh, "margin_pct")
    fig = C.bar(bench, "category", "margin_pct", highlight=bench["beats_parent"])
    marker = fig.data[0].marker
    assert list(marker.color) == [C.GOOD] * 4 + [C.BAD]
    assert list(marker.pattern.shape) == [""] * 4 + ["/"]
    assert fig.data[0].x[-1] == "Sporting Goods"


def test_a_second_metric_is_context_behind_not_a_competing_series(wh):
    """The profitability view's actual question: the largest profit may be third
    by revenue, and the pair read together is the finding."""
    depts = M.aggregate(wh, ["profit", "revenue"], by=["department"],
                        order_by="profit")
    fig = C.bar(depts, "department", "profit", ghost_key="revenue")
    ghost, value = fig.data
    assert (ghost.name, value.name) == ("Revenue", "Profit")
    assert ghost.marker.color == C.GHOST
    assert ghost.width > value.width            # drawn behind, at full width
    assert fig.layout.showlegend is True
    assert fig.layout.barmode == "overlay"


def test_the_palette_is_distinguishable_without_colour_vision():
    """Okabe-Ito, and no two series colours repeat."""
    assert len(set(C.SERIES_COLOURS)) == len(C.SERIES_COLOURS)
    assert C.PRIMARY != C.SECONDARY != C.BAD


# --------------------------------------------------------------------------- #
# what a figure refuses to imply
# --------------------------------------------------------------------------- #

def test_an_empty_heatmap_cell_stays_blank_and_a_zero_stays_zero(grid):
    """Nothing shipped that way there, against everything did and all of it late.

    The fixture holds both: LATAM never shipped First Class, and Pacific Asia
    shipped Standard Class entirely late. If the gap were filled with 0 the two
    would share a colour and a reader could not tell them apart.
    """
    fig = C.heatmap(grid, "market", "shipping_mode", "on_time_pct")
    heat = fig.data[0]
    cells = {(y, x): (z, t) for y, row_z, row_t in zip(heat.y, heat.z, heat.text)
             for x, z, t in zip(heat.x, row_z, row_t)}
    assert pd.isna(cells[("LATAM", "First Class")][0])
    assert cells[("LATAM", "First Class")][1] == ""
    assert cells[("Pacific Asia", "Standard Class")] == (0.0, "0.0%")
    assert heat.hoverongaps is False


def test_a_heatmap_can_drop_cells_too_thin_to_read(grid):
    """A 100% on-time rate over two lines is not a rate worth a colour."""
    thin = C.heatmap(grid, "market", "shipping_mode", "on_time_pct").data[0]
    thick = C.heatmap(grid, "market", "shipping_mode", "on_time_pct",
                      min_lines=4).data[0]
    assert len(thin.y) == 3 and len(thick.y) == 1
    assert thick.z.tolist() == [[100.0]]


def test_a_capped_line_chart_states_what_it_left_out(wh):
    """Beyond six lines the reader is matching colours to a legend, and the ones
    that are not drawn have to be counted rather than implied."""
    frame = M.aggregate(wh, ["revenue"], by=["month", "category"])
    fig = C.line_grouped(frame, "month", "category", "revenue", max_groups=2)
    assert [t.name for t in fig.data] == ["Women's Apparel", "Sporting Goods"]
    assert "top 2 of 5 shown, 3 omitted" in _subtitle(fig)


def test_an_uncapped_line_chart_says_nothing_about_omissions(wh):
    frame = M.aggregate(wh, ["revenue"], by=["month", "category"])
    assert "omitted" not in _subtitle(C.line_grouped(frame, "month", "category",
                                                     "revenue"))


def test_a_truncated_bar_chart_counts_the_bars_it_did_not_draw(wh):
    products = M.aggregate(wh, ["revenue"], by=["product"], order_by="revenue")
    fig = C.bar(products, "product", "revenue", limit=2)
    assert len(fig.data[0].x) == 2
    assert "top 2, 4 more not shown" in _subtitle(fig)


def test_bars_are_ranked_by_the_metrics_own_direction(wh):
    """Best first, whichever way "best" runs for that metric."""
    delays = M.aggregate(wh, ["avg_delay_days"], by=["category"])
    fig = C.bar(delays, "category", "avg_delay_days")
    assert list(fig.data[0].y) == sorted(fig.data[0].y)
    revenue = M.aggregate(wh, ["revenue"], by=["category"])
    fig = C.bar(revenue, "category", "revenue")
    assert list(fig.data[0].y) == sorted(fig.data[0].y, reverse=True)


def test_an_ordered_dimension_keeps_its_own_order(wh):
    """Delay days are a scale, not a ranking: sorting them by count would put
    -2 days between 3 and 4 and the shape of the distribution would be lost."""
    delay = M.aggregate(wh, ["lines"], by=["delay_days"])
    fig = C.bar(delay, "delay_days", "lines")
    assert list(fig.data[0].x) == ["-2", "0", "1", "2", "3", "4"]


def test_a_horizontal_bar_reads_top_down_and_grows_with_its_rows(wh):
    """Plotly's category axis runs bottom-up, which puts the leader at the foot."""
    products = M.aggregate(wh, ["revenue"], by=["product"], order_by="revenue")
    fig = C.bar(products, "product", "revenue", horizontal=True)
    assert fig.data[0].orientation == "h"
    assert fig.layout.yaxis.autorange == "reversed"
    assert fig.layout.height >= 280
    assert fig.layout.margin.l > 200          # room for a product name


# --------------------------------------------------------------------------- #
# two units, and the scatter
# --------------------------------------------------------------------------- #

def test_two_units_get_two_axes_rather_than_one_meaningless_scale(wh):
    """Dollars and percent on one axis is a chart of nothing."""
    frame = M.timeseries(wh, ["revenue", "margin_pct"], grain="month")
    fig = C.line_series(frame, "month", ["revenue", "margin_pct"])
    assert [t.yaxis for t in fig.data] == ["y", "y2"]
    assert fig.layout.yaxis.tickprefix == "$"
    assert fig.layout.yaxis2.ticksuffix == "%"
    assert fig.layout.yaxis2.side == "right"
    assert fig.layout.yaxis2.showgrid is False


def test_two_metrics_in_the_same_unit_share_one_axis(wh):
    frame = M.timeseries(wh, ["revenue", "profit"], grain="month")
    fig = C.line_series(frame, "month", ["revenue", "profit"])
    assert [t.yaxis for t in fig.data] == ["y", "y"]
    assert "yaxis2" not in fig.layout.to_plotly_json()


def test_a_group_with_no_size_is_drawn_smallest_rather_than_dropped():
    """A never-ordered product is the finding, and a point that is not there
    cannot be read. Plotly also rejects a NaN marker size outright."""
    frame = pd.DataFrame({"category": ["A", "B", "C"],
                          "revenue": [1.0, 2.0, 3.0],
                          "margin_pct": [10.0, 20.0, 30.0],
                          "units": [5.0, None, 20.0]})
    fig = C.scatter(frame, "category", "revenue", "margin_pct", size_key="units")
    sizes = list(fig.data[0].marker.size)
    assert len(sizes) == 3
    assert not any(pd.isna(s) for s in sizes)
    assert sizes[1] == min(sizes)
    assert "point size: units" in _subtitle(fig)


def test_a_point_with_no_position_is_dropped():
    """A null on either axis has no honest place on the plane."""
    frame = pd.DataFrame({"category": ["A", "B"], "revenue": [1.0, None],
                          "margin_pct": [10.0, 20.0]})
    assert len(C.scatter(frame, "category", "revenue", "margin_pct").data[0].x) == 1


def test_the_median_split_names_a_quadrant_without_inventing_a_threshold(wh):
    frame = M.aggregate(wh, ["revenue", "margin_pct"], by=["category"])
    fig = C.scatter(frame, "category", "revenue", "margin_pct", quadrants=True)
    lines = [s for s in fig.layout.shapes if s.line.dash == "dot"]
    assert len(lines) == 2
    assert any(s.x0 == s.x1 == float(frame["revenue"].median()) for s in lines)


def test_the_parity_line_is_drawn_from_the_origin_to_the_larger_extent(wh):
    """The funnel's reading: above the line converts more per view than below it."""
    frame = M.funnel(wh, by="product")
    fig = C.scatter(frame, "product", "views", "orders", parity=True)
    parity = next(t for t in fig.data if t.name == "parity")
    assert parity.x == (0, 30.0)             # 30 views is the fixture's largest
    assert parity.y == (0, 30.0)
    assert parity.hoverinfo == "skip"


def test_a_scatter_labels_its_points_with_the_dimension_not_the_index(wh):
    frame = M.aggregate(wh, ["revenue", "margin_pct"], by=["category"])
    fig = C.scatter(frame, "category", "revenue", "margin_pct")
    assert "Women's Apparel" in list(fig.data[0].text)
    assert "%{text}" in fig.data[0].hovertemplate


def test_a_scatter_is_capped_so_a_cloud_stays_a_chart(wh):
    frame = M.aggregate(wh, ["revenue", "margin_pct"], by=["product"])
    fig = C.scatter(frame, "product", "revenue", "margin_pct", limit=2)
    assert len(fig.data[0].x) == 2





