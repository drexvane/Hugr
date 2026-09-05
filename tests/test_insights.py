"""The analysis layer: a figure and its caption come from the same frame.

Every number asserted here is derivable by hand from the fixture in `conftest`.
The 2017 monthly series is 4 lines a month at $100 with one month at 3x - which is
the design doc's stated acceptance criterion for anomaly flagging - and everything
that would disturb that series is dated 2016.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from dtp import insights as I
from dtp import metrics as M

SRC = Path(__file__).resolve().parents[1] / "src" / "dtp" / "insights.py"


@pytest.fixture
def series_2017(wh):
    """Monthly revenue from 2017: $400 eleven times and $1,200 once."""
    return M.timeseries(wh, ["revenue"], grain="month",
                        filters=M.Filters(date_from="2017-01-01"))


# --------------------------------------------------------------------------- #
# the module boundary
# --------------------------------------------------------------------------- #

def test_insights_never_queries_the_warehouse_itself():
    """The module docstring's central claim, enforced rather than asserted in prose.

    Every number must arrive through `metrics`, so a gate travels with it. A query
    written here would be a second path to the data with no gate on it, and the
    figure it produced would look exactly like the ones that went through the
    registry. `wh` is passed straight through and never touched.
    """
    text = SRC.read_text(encoding="utf-8")
    assert "wh." not in text
    lowered = text.lower()
    for keyword in ("select ", "duckdb", "from order_items", "from access_logs",
                    "group by ", "filter (where"):
        assert keyword not in lowered, keyword


# --------------------------------------------------------------------------- #
# robust z - why median/MAD rather than mean/sigma
# --------------------------------------------------------------------------- #

def test_mean_and_sigma_would_miss_the_spike_this_fixture_exists_for(series_2017):
    """The whole argument for the robust score, on the acceptance-criterion series.

    One extreme month inflates sigma enough to keep its own z-score under the
    threshold: mean/sigma peaks at 3.32 against a 3.5 cut, so it flags nothing at
    all. The median/MAD score puts the same month at 15.0. The classical version is
    at its least sensitive exactly when it is most needed.
    """
    values = pd.to_numeric(series_2017["revenue"])
    classic = ((values - values.mean()) / values.std(ddof=0)).abs().max()
    robust = I.robust_z(values).abs().max()
    assert classic < I.Z_THRESHOLD
    assert robust > 4 * classic
    assert robust == pytest.approx(15.0396, abs=0.001)


def test_robust_z_is_hand_computable():
    """Median 11.5, MAD 1.5, scale 1.5/0.6745 - so 10 sits at -0.674."""
    z = I.robust_z(pd.Series([10.0, 12.0, 11.0, 13.0, 9.0, 40.0]))
    assert z.iloc[0] == pytest.approx(-0.6745, abs=0.001)
    assert z.iloc[5] == pytest.approx(12.815, abs=0.001)


def test_mad_of_zero_falls_back_to_the_mean_deviation(series_2017):
    """Eleven identical months make MAD exactly 0, which would divide by zero.

    This is the fixture's own series, so the fallback is not a rare branch here -
    it is the path the acceptance criterion runs through.
    """
    values = pd.to_numeric(series_2017["revenue"])
    assert (values - values.median()).abs().median() == 0.0
    assert I.robust_z(values).notna().all()


def test_an_entirely_flat_series_has_no_outlier():
    """Both scales are 0. Returning all-NaN beats returning an infinity."""
    assert I.robust_z(pd.Series([5.0] * 6)).isna().all()


def test_a_series_too_short_to_have_a_shape_is_not_scored():
    assert I.robust_z(pd.Series([1.0, 2.0, 3.0])).isna().all()


def test_nulls_do_not_shift_the_scores_of_the_points_around_them():
    z = I.robust_z(pd.Series([10.0, None, 11.0, 12.0, 9.0, 40.0]))
    assert pd.isna(z.iloc[1])
    assert z.drop(index=1).notna().all()


# --------------------------------------------------------------------------- #
# anomalies
# --------------------------------------------------------------------------- #

def test_the_injected_spike_is_flagged_and_named_as_a_month(series_2017):
    """Design doc acceptance criterion 1: the 3x month is flagged."""
    found = I.find_anomalies(series_2017, "revenue", label_col="month")
    assert len(found) == 1
    worst = found[0]
    assert worst.label == "Jul 2017"          # not a timestamp
    assert worst.value == 1200.0
    assert worst.median == 400.0
    assert worst.direction == "above"
    assert worst.pct_from_median == pytest.approx(200.0)
    assert worst.z == pytest.approx(15.0396, abs=0.001)


def test_a_flat_window_flags_nothing(wh):
    """Design doc acceptance criterion 2: no false positives on a calm series."""
    flat = M.timeseries(wh, ["revenue"], grain="month",
                        filters=M.Filters(date_from="2017-01-01",
                                          date_to="2017-05-31"))
    assert flat["revenue"].tolist() == [400.0] * 5
    assert I.find_anomalies(flat, "revenue", label_col="month") == []


def test_no_anomaly_says_so_rather_than_saying_nothing():
    said = I.say_anomalies([], "revenue")
    assert "nothing is flagged" in said
    assert "3.5" in said


def test_anomalies_are_reported_worst_first():
    """A caption names the worst point, so the order is part of the contract."""
    frame = pd.DataFrame({"month": pd.date_range("2018-01-01", periods=8,
                                                 freq="MS"),
                          "revenue": [100.0, 102.0, 98.0, 101.0, 99.0, 400.0,
                                      -300.0, 250.0]})
    found = I.find_anomalies(frame, "revenue", label_col="month")
    assert len(found) > 1
    assert [abs(a.z) for a in found] == sorted((abs(a.z) for a in found),
                                              reverse=True)
    assert found[0].direction == "below"          # -300 is furthest from 100.5
    assert {a.direction for a in found} == {"above", "below"}


def test_a_zero_median_has_no_percentage_from_it():
    """A percentage of nothing is not 0%, it is unanswerable."""
    assert I.Anomaly("X", 5.0, 4.0, 0.0, "above").pct_from_median is None


def test_flag_anomalies_leaves_the_callers_frame_alone(series_2017):
    """Other panels on the page are still reading that frame."""
    before = list(series_2017.columns)
    flagged = I.flag_anomalies(series_2017, "revenue")
    assert list(series_2017.columns) == before
    assert {"z", "is_anomaly"} <= set(flagged.columns)
    assert int(flagged["is_anomaly"].sum()) == 1


def test_flagging_a_column_that_is_not_there_flags_nothing(series_2017):
    out = I.flag_anomalies(series_2017, "not_a_column")
    assert out["z"].isna().all()
    assert not out["is_anomaly"].any()


def test_say_anomalies_counts_the_others_in_the_right_number():
    made = [I.Anomaly("Jan 2018", 9.0, 9.0, 1.0, "above"),
            I.Anomaly("Feb 2018", 8.0, 8.0, 1.0, "above"),
            I.Anomaly("Mar 2018", 7.0, 7.0, 1.0, "above")]
    assert "1 other month also flagged" in I.say_anomalies(made[:2], "revenue")
    assert "2 other months also flagged" in I.say_anomalies(made, "revenue")


# --------------------------------------------------------------------------- #
# period over period
# --------------------------------------------------------------------------- #

def test_the_prior_window_is_the_same_length_ending_the_day_before():
    """Not "last month": a 31-day January against a 28-day February moves the
    number for a reason that has nothing to do with the business."""
    assert I.prior_window("2017-07-01", "2017-07-31") == ("2017-05-31",
                                                          "2017-06-30")
    assert I.prior_window("2017-07-31", "2017-07-01") == ("2017-05-31",
                                                          "2017-06-30")


def test_the_spike_month_against_the_month_before_it(wh):
    comp = I.period_over_period(wh, ["revenue"],
                                filters=M.Filters(date_from="2017-07-01",
                                                  date_to="2017-07-31"))[0]
    assert comp.current == 1200.0
    assert comp.previous == 400.0
    assert comp.delta == 800.0
    assert comp.delta_pct == pytest.approx(200.0)
    assert comp.improved is True
    assert "$800.00" in I.say_comparison(comp)


def test_a_fall_in_a_higher_is_better_metric_is_named_as_wrong(wh):
    comp = I.period_over_period(wh, ["revenue"],
                                filters=M.Filters(date_from="2017-08-01",
                                                  date_to="2017-08-31"))[0]
    assert comp.delta == -800.0
    assert comp.improved is False
    assert "wrong direction" in I.say_comparison(comp)


def test_an_undated_window_has_no_baseline_and_says_so(wh):
    """Zero would read as "no change", which is the opposite of the truth."""
    comp = I.period_over_period(wh, ["revenue"])[0]
    assert comp.current == 7700.0
    assert comp.previous is None
    assert comp.delta is None and comp.delta_pct is None
    assert comp.improved is None
    assert "no change is shown" in I.say_comparison(comp)


def test_a_zero_baseline_gives_no_percentage(wh):
    """Growth from nothing is not a percentage."""
    comp = I.Comparison("revenue", 100.0, 0.0, "w", "p")
    assert comp.delta == 100.0
    assert comp.delta_pct is None


def test_a_percentage_change_is_stated_in_points(wh):
    """"Margin is up 0.4%" is ambiguous about which quantity moved."""
    comp = I.period_over_period(wh, ["margin_pct"],
                                filters=M.Filters(date_from="2017-07-01",
                                                  date_to="2017-07-31"))[0]
    assert "pp" in I.say_comparison(comp)


def test_the_filters_carry_into_the_prior_window(wh):
    """A comparison that widened its own baseline would flatter every filter."""
    filters = M.Filters(date_from="2017-07-01", date_to="2017-07-31",
                        where={"market": ["LATAM"]})
    comp = I.period_over_period(wh, ["revenue"], filters=filters)[0]
    assert comp.current is None          # LATAM has no 2017 rows at all
    assert comp.previous is None


# --------------------------------------------------------------------------- #
# a child against the parent it sits inside
# --------------------------------------------------------------------------- #

def test_the_parent_figure_is_recomputed_not_averaged(wh):
    """The mistake this function exists to avoid, pinned with a number.

    Fan Shop holds a 20% category and a -4% one. Their mean is 8%; the department's
    actual margin is 2.86%, because the loss-maker carries four lines and the
    winner one. An average of children is not the parent.
    """
    out = I.benchmark_vs_parent(wh, "margin_pct")
    fan_shop = out[out["department"] == "Fan Shop"]
    assert fan_shop["parent_value"].tolist() == pytest.approx([2.857143] * 2)
    children = sorted(fan_shop["margin_pct"].tolist())
    assert children == pytest.approx([-4.0, 20.0])
    assert fan_shop["parent_value"].iloc[0] != pytest.approx(sum(children) / 2)


def test_the_gap_is_the_child_minus_its_own_parent(wh):
    out = I.benchmark_vs_parent(wh, "margin_pct")
    best = out.iloc[0]
    assert best["category"] == "Indoor/Outdoor Games"
    assert best["gap"] == pytest.approx(17.142857)
    assert bool(best["beats_parent"]) is True
    worst = out.iloc[-1]
    assert worst["category"] == "Sporting Goods"
    assert worst["gap"] == pytest.approx(-6.857143)
    assert bool(worst["beats_parent"]) is False


def test_a_lower_is_better_metric_sorts_and_judges_the_other_way(wh):
    """A 2-day delay does not "beat" a 0.25-day department."""
    assert M.metric("avg_delay_days").higher_is_better is False
    out = I.benchmark_vs_parent(wh, "avg_delay_days")
    assert out["gap"].is_monotonic_increasing
    assert out.iloc[0]["category"] == "Sporting Goods"
    assert bool(out.iloc[0]["beats_parent"]) is True
    assert bool(out.iloc[-1]["beats_parent"]) is False


def test_a_benchmark_over_nothing_still_has_its_columns(wh):
    """A view reads `gap` unconditionally; an empty frame without it raises."""
    out = I.benchmark_vs_parent(wh, "margin_pct",
                                filters=M.Filters(where={"market": ["Atlantis"]}))
    assert out.empty
    assert {"parent_value", "gap"} <= set(out.columns)


def test_a_one_row_benchmark_does_not_say_it_beats_and_trails_itself(wh):
    """With one row, best and worst are the same row."""
    out = I.benchmark_vs_parent(wh, "margin_pct",
                                filters=M.Filters(where={"market":
                                                         ["Pacific Asia"]}))
    said = I.say_benchmark(out.head(1), "margin_pct")
    assert "trails its own" not in said
    assert said.count(";") == 0


def test_the_benchmark_sentence_counts_the_categories_in_the_right_number(wh):
    out = I.benchmark_vs_parent(wh, "margin_pct")
    said = I.say_benchmark(out, "margin_pct")
    assert "17.1 pp" in said                      # points, not percent
    assert "4 of 5 categories" in said
    assert "1 of 1 categories" not in said


# --------------------------------------------------------------------------- #
# losses and movers
# --------------------------------------------------------------------------- #

def test_loss_makers_lists_only_realised_losses(wh):
    out = I.loss_makers(wh)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["product"] == "Clearance Bat"
    assert row["profit"] == -100.0        # two lines at -$50
    assert row["revenue"] == 400.0
    assert row["margin_pct"] == pytest.approx(-25.0)


def test_loss_makers_is_capped_and_deepest_first(wh):
    assert I.loss_makers(wh, limit=0).empty
    out = I.loss_makers(wh, by="category")
    assert out["profit"].is_monotonic_increasing


def test_the_loss_sentence_names_the_deepest_not_the_shallowest():
    """`say_ranking` sorts by the metric's own direction, so ranking a loss list by
    profit puts the least bad group first and captions the chart with it."""
    losses = pd.DataFrame({"product": ["Alpha", "Bravo", "Charlie", "Delta"],
                           "profit": [-100.0, -80.0, -60.0, -10.0],
                           "revenue": [400.0, 200.0, 90.0, 5.0]})
    said = I.say_losses(losses)
    assert said.startswith("4 products lose money, $250.00 in all.")
    assert "Alpha is the deepest at $100.00 on $400.00 of sales" in said
    assert "-$" not in said              # "lost" already carries the direction
    assert I.say_losses(losses.sort_values("profit", ascending=False)) == said
    assert "Delta" not in said


def test_the_loss_sentence_reads_for_one_group_and_for_none(wh):
    only = I.say_losses(I.loss_makers(wh))
    assert only == ("Clearance Bat is the only product losing money: "
                    "$100.00 on $400.00 of sales.")
    assert I.say_losses(pd.DataFrame()) == ("No product loses money under these "
                                            "filters.")


def test_movers_keeps_arrivals_and_departures(wh):
    """An outer merge, because vanishing is the largest move there is."""
    out = I.movers(wh, "revenue", by="category",
                   filters=M.Filters(date_from="2016-05-01",
                                     date_to="2016-06-30"))
    rows = out.set_index("category")
    gone = rows.loc["Sporting Goods"]
    assert gone["revenue"] == 0.0 and gone["revenue_prior"] == 1000.0
    assert gone["delta"] == -1000.0 and gone["delta_pct"] == pytest.approx(-100.0)
    arrived = rows.loc["Electronics (Outdoors)"]
    assert arrived["revenue_prior"] == 0.0 and arrived["delta"] == 350.0
    # Arriving from nothing has no percentage, and 0% would read as "no change".
    assert pd.isna(arrived["delta_pct"])


def test_movers_orders_by_absolute_move(wh):
    out = I.movers(wh, "revenue", by="category",
                   filters=M.Filters(date_from="2016-05-01",
                                     date_to="2016-06-30"))
    assert out["delta"].abs().is_monotonic_decreasing
    assert out.iloc[0]["category"] == "Sporting Goods"


def test_movers_without_a_window_refuses_rather_than_inventing_one(wh):
    with pytest.raises(ValueError) as err:
        I.movers(wh, "revenue", by="category")
    assert "dated window" in str(err.value)


# --------------------------------------------------------------------------- #
# composition and confounding - is the series measuring the same thing?
# --------------------------------------------------------------------------- #

def test_a_stable_composition_raises_no_warning(wh):
    """The failure mode is a warning on every chart, which trains it to be ignored.

    The fixture's orders are one line each but for a single two-line order, so
    `lines_per_order` never steps and the answer must be None - not a Shift with a
    tiny ratio.
    """
    assert I.composition_shift(wh) is None
    assert I.say_shift(None) == ""


def test_the_split_is_searched_for_rather_than_hardcoded(wh):
    """No date is written into the check, so a new extract cannot inherit this one's.

    Monthly revenue in the fixture steps from a $750 median over its first three
    months to $400 over the thirteen after them, and the search finds that split
    without being told where it is.
    """
    shift = I.composition_shift(wh, metric_key="revenue", threshold=0.4)
    assert shift is not None
    assert shift.at == "Jun 2016"
    assert (shift.before, shift.after) == (750.0, 400.0)
    assert (shift.n_before, shift.n_after) == (3, 13)
    assert shift.ratio == pytest.approx(400.0 / 750.0)


def test_a_step_below_the_threshold_is_not_reported(wh):
    """The same series, a threshold above its largest step: silence."""
    assert I.composition_shift(wh, metric_key="revenue", threshold=0.5) is None


def test_a_series_too_short_to_split_is_not_split(wh):
    """Three months each side is the minimum; five months cannot supply it."""
    assert I.composition_shift(wh, metric_key="revenue",
                               filters=M.Filters(date_from="2017-01-01",
                                                 date_to="2017-05-31")) is None


def test_the_shift_sentence_says_which_way_to_read_across_it():
    """The real extract falls; a rise needs the opposite advice, not the same."""
    down = I.say_shift(I.Shift(at="Oct 2017", before=3.0, after=1.0,
                               n_before=33, n_after=4))
    assert "Totals fall across that boundary" in down
    assert "per-order or per-line figures" in down
    up = I.say_shift(I.Shift(at="Oct 2017", before=1.0, after=3.0,
                             n_before=33, n_after=4))
    assert "Totals rise across that boundary" in up


def test_market_is_a_time_partition_in_this_data(wh):
    """The fixture reproduces the extract's own defect: one market per month.

    "Revenue by market" over the whole window is then largely a chart of which
    months each market's block covers.
    """
    conf = I.confounding(wh, "market")
    assert (conf.n_values, conf.periods) == (3, 16)
    assert conf.multi_value_periods == 1
    assert conf.single_value_periods == 15
    assert conf.median_top_share == pytest.approx(100.0)
    assert conf.is_time_partitioned is True
    said = I.say_confound(conf)
    assert "15 of 16 months" in said
    assert "exactly one market" in said
    assert "compares two different windows" in said


def test_an_ordinary_imbalance_is_not_a_partition_and_says_nothing():
    """41% in the largest value is imbalance, not separation."""
    calm = I.Confound("market", "month", 5, 37, 37, 41.2)
    assert calm.is_time_partitioned is False
    assert I.say_confound(calm) == ""
    assert I.Confound("market", "month", 5, 37, 20, 90.0).is_time_partitioned


def test_confounding_over_an_empty_window_is_zero_not_an_error(wh):
    conf = I.confounding(wh, "market",
                         filters=M.Filters(where={"market": ["Atlantis"]}))
    assert (conf.periods, conf.median_top_share) == (0, 0.0)
    assert conf.is_time_partitioned is False


def test_confounding_refuses_a_column_that_is_not_a_dimension(wh):
    with pytest.raises(KeyError):
        I.confounding(wh, "client_ip")


# --------------------------------------------------------------------------- #
# the sentences - a caption that contradicts its chart is worse than none
# --------------------------------------------------------------------------- #

def test_a_ranking_states_a_share_only_when_something_is_left_out(wh):
    """"3 of 3 categories, 100% of the revenue" tells the reader nothing."""
    top = M.aggregate(wh, ["revenue"], by=["category"], order_by="revenue")
    partial = I.say_ranking(top, "revenue", "category", top=3)
    assert "92.2% of the revenue on this chart" in partial
    whole = I.say_ranking(top, "revenue", "category", top=len(top))
    assert "% of the revenue" not in whole


def test_a_ranking_of_losses_states_an_amount_not_a_share():
    """A share of a negative total reads as a share of a positive one."""
    losses = pd.DataFrame({"product": ["A", "B", "C", "D"],
                           "profit": [-100.0, -80.0, -60.0, -10.0]})
    said = I.say_ranking(losses, "profit", "product")
    assert "$150.00 of the $250.00 lost on this chart" in said
    assert "100.0% of the profit" not in said


def test_a_ranking_with_mixed_signs_claims_no_share_at_all():
    """A total that partly cancels itself out is not a share of anything."""
    mixed = pd.DataFrame({"product": ["A", "B", "C", "D"],
                          "profit": [500.0, 100.0, -60.0, -480.0]})
    said = I.say_ranking(mixed, "profit", "product")
    assert "of the" not in said
    assert said.endswith("at the top.")


def test_a_ranking_of_a_rate_claims_no_share(wh):
    """Margins do not add up, so no share of them is meaningful."""
    pct = pd.DataFrame({"category": ["A", "B", "C", "D"],
                        "margin_pct": [30.0, 20.0, 10.0, 5.0]})
    assert "of the" not in I.say_ranking(pct, "margin_pct", "category")


def test_one_leader_leads_and_several_lead():
    frame = pd.DataFrame({"category": ["A", "B"], "revenue": [2.0, 1.0]})
    assert " leads with " in I.say_ranking(frame, "revenue", "category", top=1)
    assert " lead with " in I.say_ranking(frame, "revenue", "category", top=2)


def test_a_caption_shortens_a_product_name_that_would_not_fit():
    long = pd.DataFrame({"product": ["Nike Men's Dri-FIT Victory Golf Polo in "
                                     "Extended Sizes"], "revenue": [10.0]})
    said = I.say_ranking(long, "revenue", "product")
    assert "…" in said
    assert "Extended Sizes" not in said


def test_a_ranking_of_nothing_says_so(wh):
    assert I.say_ranking(pd.DataFrame(), "revenue", "market") == \
        "No rows match these filters."


def test_a_ranking_ordered_against_the_metric_names_the_end_it_shows():
    """The defect the agent surfaced: a caller can order a frame either way.

    `say_ranking` re-sorts by the metric's own direction, which is right for every
    dashboard panel and wrong for "the ten worst products by profit" - a frame whose
    first row is the deepest loss, captioned with the shallowest one and the word
    "lead". `ascending` makes the direction travel with the frame.
    """
    frame = pd.DataFrame({"product": ["Sinking", "Middling", "Best"],
                          "profit": [-900.0, 100.0, 500.0]})
    # The default is unchanged: the metric's good end, described as leading.
    assert "Best leads with $500.00 at the top" in \
        I.say_ranking(frame, "profit", "product", top=1)
    # Told the frame is worst-first, it names the worst and does not call it the top.
    worst = I.say_ranking(frame, "profit", "product", top=2, ascending=True)
    assert "Sinking, Middling are the lowest at -$900.00" in worst
    assert "lead" not in worst and "at the top" not in worst


def test_the_end_named_depends_on_the_metric_not_on_the_sort_alone():
    # Lower is better for a delay, so ascending *is* the good end and "lead" is
    # right; descending is the bad end and gets named as the highest.
    frame = pd.DataFrame({"market": ["Quick", "Slow"],
                          "avg_delay_days": [0.5, 4.0]})
    assert " lead with " in I.say_ranking(frame, "avg_delay_days", "market",
                                          ascending=True)
    said = I.say_ranking(frame, "avg_delay_days", "market", ascending=False)
    assert "are the highest at " in said and "Slow, Quick" in said


def test_one_row_is_the_lowest_rather_than_are_the_lowest():
    frame = pd.DataFrame({"product": ["Sinking"], "profit": [-900.0]})
    assert " is the lowest at " in I.say_ranking(frame, "profit", "product",
                                                top=1, ascending=True)


# --------------------------------------------------------------------------- #
# the four sentences the dashboard needed and a ranking could not give
# --------------------------------------------------------------------------- #

def test_a_grid_names_its_worst_cell_so_no_one_reads_it_off_a_shade(wh):
    """A heatmap's question is which combination is worst, not which is dark."""
    grid = M.aggregate(wh, ["on_time_pct"], by=["market", "shipping_mode"])
    said = I.say_grid(grid, "market", "shipping_mode", "on_time_pct")
    assert "best for Standard Class in Europe at 100.0%" in said
    assert "worst for Standard Class in Pacific Asia at 0.0%" in said


def test_a_grid_counts_the_combinations_that_have_no_rows(wh):
    """A blank cell is not a zero, and past a few rows the eye cannot tell.

    Three markets and two shipping modes are six combinations; the fixture holds
    four, so two were never shipped that way at all.
    """
    grid = M.aggregate(wh, ["on_time_pct"], by=["market", "shipping_mode"])
    assert len(grid) == 4
    assert "2 of 6 combinations have no rows at all" in \
        I.say_grid(grid, "market", "shipping_mode", "on_time_pct")


def test_a_full_grid_claims_no_empty_cells(wh):
    full = pd.DataFrame({"market": ["A", "A", "B", "B"],
                         "shipping_mode": ["X", "Y", "X", "Y"],
                         "on_time_pct": [90.0, 80.0, 70.0, 60.0]})
    assert "combinations" not in I.say_grid(full, "market", "shipping_mode",
                                           "on_time_pct")


def test_a_grid_of_one_cell_is_not_its_own_worst_case():
    one = pd.DataFrame({"market": ["A"], "shipping_mode": ["X"],
                        "on_time_pct": [90.0]})
    said = I.say_grid(one, "market", "shipping_mode", "on_time_pct")
    assert "best for X in A at 90.0%." == said.split("is ", 1)[1]
    assert "worst" not in said


def test_a_lower_is_better_grid_calls_the_high_cell_the_worst_one():
    """`best` is the registry's judgement, not the top of the sort."""
    delays = pd.DataFrame({"market": ["A", "B"], "shipping_mode": ["X", "X"],
                           "avg_delay_days": [0.5, 4.0]})
    said = I.say_grid(delays, "market", "shipping_mode", "avg_delay_days")
    assert "best for X in A at 0.50 days" in said
    assert "worst for X in B at 4.00 days" in said


def test_the_spread_sentence_counts_shipments_not_lines(wh):
    """The 4-day row is two cancelled lines, and the gate leaves it empty.

    `shipped_lines` rather than `lines` is what makes this chart honest: those two
    rows carry a real `days_shipping_real` for a delivery that never happened, and
    a delay distribution that counted them would show two late shipments that do
    not exist.
    """
    spread = M.aggregate(wh, ["shipped_lines"], by=["delay_days"])
    late = spread[spread["delay_days"] == 4].iloc[0]
    assert late["n_lines"] == 2 and late["shipped_lines"] == 0
    said = I.say_spread(spread, "delay_days", "shipped_lines")
    assert "Most shipped lines sit at 0 delay (57 of 63)" in said


def test_the_spread_sentence_states_the_share_on_the_good_side_of_the_cut(wh):
    """59 of 63 shipped lines are early or on time - 93.7%, and 0 is the default."""
    spread = M.aggregate(wh, ["shipped_lines"], by=["delay_days"])
    assert "93.7% are at 0 or below." in \
        I.say_spread(spread, "delay_days", "shipped_lines")
    assert "98.4% are at 2 or below." in \
        I.say_spread(spread, "delay_days", "shipped_lines", cut=2)


def test_a_spread_with_no_shipments_says_so(wh):
    """Every row gated away is not a distribution with a tallest bar."""
    none = pd.DataFrame({"delay_days": [0, 1], "shipped_lines": [0, 0]})
    assert I.say_spread(none, "delay_days", "shipped_lines") == \
        "No rows match these filters."
    assert I.say_spread(pd.DataFrame(), "delay_days", "shipped_lines") == \
        "No rows match these filters."


def test_the_movers_sentence_makes_the_change_the_subject(wh):
    """A ranking would name the biggest category; this names the biggest move."""
    moved = I.movers(wh, "revenue", by="category",
                     filters=M.Filters(date_from="2016-04-01",
                                       date_to="2016-06-30"), limit=8)
    said = I.say_movers(moved, "revenue", "category")
    assert said.startswith("Indoor/Outdoor Games moved most: revenue down "
                           "$400.00 (-100.0%)")
    assert "out of 5 categories compared." in said


def test_an_arrival_is_named_because_it_has_no_percentage_to_state(wh):
    """A group absent from the prior window divides by zero, so `delta_pct` is null.

    A departure needs no prose - "-100.0%" says it went to nothing. An arrival has
    no percentage at all, so "up $350.00" would read as an ordinary move rather
    than a group that did not exist last window.
    """
    arrival = pd.DataFrame({"category": ["Electronics (Outdoors)"],
                            "revenue": [350.0], "revenue_prior": [0.0],
                            "delta": [350.0], "delta_pct": [float("nan")]})
    said = I.say_movers(arrival, "revenue", "category")
    assert "up $350.00, from nothing in the prior window" in said
    assert "%" not in said


def test_movers_over_nothing_names_the_dimension_it_found_nothing_in(wh):
    assert I.say_movers(pd.DataFrame(), "revenue", "category") == \
        "No category moved between these two windows."


def test_the_trend_sentence_traces_a_line_rather_than_ranking_it(wh):
    """Under a multi-line chart a ranking repeats the bar chart above it.

    What a series adds is shape, so the sentence gives the three values a reader
    would otherwise follow with a finger: both endpoints and the peak.
    """
    trend = M.aggregate(wh, ["revenue"], by=["month", "market"])
    said = I.say_trend(trend, "month", "market", "revenue")
    assert said.startswith("Europe is the largest: revenue of $1.0k in Mar 2016 "
                           "against $400.00 in Dec 2017")
    assert "peaking at $1.2k in Jul 2017" in said
    assert "3 markets over 16 months." in said


def test_the_trend_names_the_group_the_chart_actually_drew(wh):
    """`line_grouped` keeps the largest groups by summed total; so does this.

    If the sentence picked by final value or by row count it could name a line the
    chart dropped, and the caption would describe something that is not there.
    """
    trend = M.aggregate(wh, ["revenue"], by=["month", "market"])
    largest = trend.groupby("market")["revenue"].sum().idxmax()
    assert I.say_trend(trend, "month", "market", "revenue").startswith(largest)


def test_a_peak_at_an_endpoint_is_not_stated_twice(wh):
    """The peak clause exists to add a third value, not to repeat the first."""
    rising = pd.DataFrame({"month": pd.to_datetime(["2017-01-01", "2017-02-01",
                                                    "2017-03-01"]),
                           "market": ["A"] * 3, "revenue": [1.0, 2.0, 3.0]})
    assert "peaking" not in I.say_trend(rising, "month", "market", "revenue")
    humped = rising.assign(revenue=[1.0, 9.0, 3.0])
    assert "peaking at $9.00 in Feb 2017" in \
        I.say_trend(humped, "month", "market", "revenue")


def test_a_one_month_trend_does_not_compare_a_point_to_itself():
    one = pd.DataFrame({"month": pd.to_datetime(["2017-01-01"]),
                        "market": ["A"], "revenue": [5.0]})
    said = I.say_trend(one, "month", "market", "revenue")
    assert said == ("A is the largest: revenue of $5.00 in Jan 2017. "
                    "1 market over 1 month.")


def test_the_funnel_sentence_carries_the_window_its_rate_is_true_in(wh):
    """A conversion rate without its window is a number no one can check."""
    said = I.say_funnel(M.funnel(wh, by="product"), "product",
                        M.funnel_totals(wh))
    assert "75 page views and 6 gated orders" in said
    assert "over 2016-03-01 to 2016-06-28" in said
    assert "a view-to-order rate of 8.00%" in said


def test_the_funnel_sentence_will_not_sum_the_column_it_was_not_given(wh):
    """Without totals it describes the frame and claims no rate.

    Summing per-group distinct order counts overstates orders by 29% in the real
    extract, and the sentence that did it read as a headline figure.
    """
    said = I.say_funnel(M.funnel(wh, by="product"), "product")
    assert said.startswith("75 page views across 7 ranked products.")
    assert "rate" not in said
    assert "gated orders" not in said


def test_the_funnel_sentence_agrees_with_itself_about_one_product(wh):
    """"1 products were viewed" is the defect this pins."""
    said = I.say_funnel(M.funnel(wh, by="product"), "product",
                        M.funnel_totals(wh))
    assert "1 product was viewed and never ordered" in said
    assert "never sold hat most of all (15 views)" in said


def test_an_empty_funnel_says_so(wh):
    assert I.say_funnel(pd.DataFrame()) == \
        "No page views join to orders in this window."


def test_the_gate_is_quantified_once_and_as_a_delta(wh):
    said = I.say_gate(M.totals(wh, ["revenue", "revenue_ungated"]))
    assert "$8,000.00 of sales that never converted" in said
    assert "50.96% of the $15.7k raw total" in said


def test_there_is_nothing_to_say_about_a_gate_with_no_raw_total(wh):
    assert I.say_gate({"revenue": 1.0}) == ""
    assert I.say_gate({"revenue": 1.0, "revenue_ungated": 0.0}) == ""


def test_no_sentence_ends_without_a_full_stop(wh):
    """Captions are concatenated into a page; a missing stop runs two together."""
    totals = M.totals(wh, ["revenue", "revenue_ungated"])
    window = M.Filters(date_from="2016-04-01", date_to="2016-06-30")
    said = [
        I.say_anomalies(I.find_anomalies(
            M.timeseries(wh, ["revenue"], grain="month"), "revenue",
            label_col="month"), "revenue"),
        I.say_comparison(I.period_over_period(wh, ["revenue"])[0]),
        I.say_ranking(M.aggregate(wh, ["revenue"], by=["category"]), "revenue",
                      "category"),
        I.say_benchmark(I.benchmark_vs_parent(wh, "margin_pct"), "margin_pct"),
        I.say_losses(I.loss_makers(wh)),
        I.say_funnel(M.funnel(wh, by="product"), "product", M.funnel_totals(wh)),
        I.say_gate(totals),
        I.say_confound(I.confounding(wh, "market")),
        I.say_grid(M.aggregate(wh, ["on_time_pct"],
                               by=["market", "shipping_mode"]),
                   "market", "shipping_mode", "on_time_pct"),
        I.say_spread(M.aggregate(wh, ["shipped_lines"], by=["delay_days"]),
                     "delay_days", "shipped_lines"),
        I.say_movers(I.movers(wh, "revenue", by="category", filters=window),
                     "revenue", "category"),
        I.say_trend(M.aggregate(wh, ["revenue"], by=["month", "market"]),
                    "month", "market", "revenue"),
        # Built rather than derived: this fixture's composition is deliberately
        # stable, and `say_shift` says nothing at all when there is nothing to
        # warn about. The full stop still has to be there when there is.
        I.say_shift(I.Shift(at="Oct 2017", before=3.0, after=1.0, n_before=19,
                            n_after=3)),
    ]
    for sentence in said:
        assert sentence and sentence.endswith(".") and sentence[0].isupper() \
            or sentence[0].isdigit(), sentence


def test_every_public_sentence_builder_is_swept_above():
    """The sweep is only a guarantee while it names every builder there is.

    A new `say_*` that nothing else calls would otherwise ship with no full-stop
    check at all, and captions are joined into a paragraph.
    """
    import inspect

    builders = {name for name, _ in inspect.getmembers(I, inspect.isfunction)
                if name.startswith("say_")}
    source = inspect.getsource(test_no_sentence_ends_without_a_full_stop)
    missing = {name for name in builders if "I." + name + "(" not in source}
    assert not missing, missing





