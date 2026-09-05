"""The validator: everything checked before a query runs, and without one.

`validate(plan)` takes an optional warehouse, and the split matters here. Without
one it checks every property of the *registry* - unknown keys, too many groupings, a
sort on a column that is not in the answer, a funnel metric in an aggregate shape -
so most of this module needs no data at all. With one it additionally checks the
window against what the snapshot covers and every filter value against what the
column holds, which is the half that needs the fixture.

The two behaviours worth stating plainly, because both are corrections rather than
refusals: a window that *overlaps* the data is clamped with a note, and a sort on a
metric the question did not ask to see adds the column with a note. Silence about
either would be indistinguishable from a misread question, which is why `notes`
travels with the plan into the answer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dtp import metrics as M
from dtp.agent import plan as P
from dtp.agent.guard import PlanError
from dtp.agent.plan import Plan, execute, from_tool_input, validate

SRC = Path(P.__file__)


def refused(code: str, plan: Plan, wh=None) -> PlanError:
    """Assert `validate` refuses with `code`, and hand back the error to read."""
    with pytest.raises(PlanError) as caught:
        validate(plan, wh)
    assert caught.value.refusal.code == code, caught.value.refusal.message
    return caught.value


# --------------------------------------------------------------------------- #
# the tool call, before it is a plan
# --------------------------------------------------------------------------- #

def test_a_single_string_is_read_as_the_one_element_list():
    # A model that sends "revenue" rather than ["revenue"] means the same thing, and
    # coercing is cheaper than a refusal the user has to read.
    plan = from_tool_input({"metrics": "revenue", "by": "market"})
    assert plan.metrics == ["revenue"] and plan.by == ["market"]


def test_a_numeric_string_limit_is_read_as_the_number():
    assert from_tool_input({"metrics": ["revenue"], "limit": "10"}).limit == 10


def test_an_unknown_field_is_refused_rather_than_ignored():
    # Silently dropping it would answer a different question from the one the model
    # asked for, and the model is the one place a typo is not a user's mistake.
    error = refused_input({"metrics": ["revenue"], "group_by": ["market"]})
    assert "'group_by'" in error.refusal.detail


def test_a_mis_shaped_payload_is_unparseable():
    assert refused_input({"metrics": {"a": 1}}).refusal.code == "unparseable"
    assert refused_input({"where": ["market"]}).refusal.code == "unparseable"
    assert refused_input({"limit": "ten"}).refusal.code == "unparseable"


def refused_input(payload: dict) -> PlanError:
    with pytest.raises(PlanError) as caught:
        from_tool_input(payload)
    return caught.value


def test_the_defaults_are_an_aggregate_over_products():
    plan = from_tool_input({})
    assert plan.kind == "aggregate" and plan.funnel_by == "product"
    assert plan.metrics == [] and plan.min_lines == 0


# --------------------------------------------------------------------------- #
# metrics and dimensions, against the registry alone
# --------------------------------------------------------------------------- #

def test_an_unknown_metric_names_the_nearest_registry_keys():
    error = refused("unknown_metric", Plan(metrics=["profits"]))
    # `difflib` gets "profits" -> profit, which is the difference between a dead end
    # and a second attempt.
    assert "profit" in error.refusal.suggestions


def test_an_unknown_metric_with_no_near_match_lists_what_there_is():
    error = refused("unknown_metric", Plan(metrics=["xyzzy"]))
    listing = " ".join(error.refusal.suggestions)
    # The fallback is a listing rather than guesses: "aov" first alphabetically, and
    # a count of the rest, because naming all nineteen in a refusal is unreadable.
    assert "aov" in listing and "more" in listing


def test_a_near_miss_is_only_ever_suggested_as_a_real_key():
    # "returns" is close enough to `orders` for difflib, which is the right answer
    # here: a suggestion that is not askable is worse than no suggestion at all.
    error = refused("unknown_metric", Plan(metrics=["returns"]))
    assert set(error.refusal.suggestions) <= set(M.METRICS)
    assert error.refusal.suggestions


def test_an_unknown_dimension_lists_the_dimensions():
    error = refused("unknown_dimension",
                    Plan(metrics=["revenue"], by=["salesperson"]))
    assert "market" in " ".join(error.refusal.suggestions)


def test_no_personal_column_can_be_named_by_a_plan():
    # The structural half of the `personal_data` refusal (design reason 7): these
    # columns are in the snapshot and are deliberately not registry dimensions, so
    # the screen being bypassed changes nothing about what can be queried.
    for column in ("customer_first_name", "customer_last_name", "customer_street",
                   "client_ip", "customer_email", "customer_password"):
        assert column not in M.DIMENSIONS
        refused("unknown_dimension", Plan(metrics=["revenue"], by=[column]))
        refused("unknown_dimension", Plan(metrics=["revenue"],
                                          where={column: ["x"]}))


def test_no_metric_key_means_the_ungated_figure_except_the_one_that_says_so():
    # Reason 1's first clause. `revenue_ungated` exists so the $1,570,305.33 gap is
    # measurable, and it is labelled not-for-reporting in the registry itself.
    ungated = [k for k, m in M.METRICS.items() if "ungated" in k]
    assert ungated == ["revenue_ungated"]
    assert "not a figure to report" in M.metric("revenue_ungated").about.lower()
    # And the reportable one carries the gate, which is the whole of reason 1.
    assert M.metric("revenue").gate and not M.metric("revenue_ungated").gate


def test_no_metric_means_counting_lines_and_says_so():
    out = validate(Plan(by=["market"]))
    assert out.metrics == ["lines"]
    assert any("counts order lines" in n for n in out.notes)


def test_a_grain_asked_for_as_a_grouping_is_moved_to_the_grain():
    # Same request, spelled the other way. Moving it keeps `execute`'s routing to
    # `timeseries()` - the only call that guarantees one row per period.
    out = validate(Plan(metrics=["revenue"], by=["month"]))
    assert out.grain == "month" and out.by == []


def test_a_second_time_grain_is_dropped_with_a_note():
    out = validate(Plan(metrics=["revenue"], grain="month", by=["quarter"]))
    assert out.grain == "month" and out.by == []
    assert any("rather than by both" in n for n in out.notes)


def test_a_grain_that_is_not_a_time_grain_is_refused():
    error = refused("unknown_dimension", Plan(metrics=["revenue"], grain="market"))
    assert error.refusal.suggestions == P.TIME_GRAINS


def test_two_groupings_are_fine_and_three_are_a_table():
    two = validate(Plan(metrics=["revenue"], by=["market", "category"]))
    assert two.notes == () or all("table" not in n for n in two.notes)
    three = validate(Plan(metrics=["revenue"], grain="month",
                          by=["market", "category"]))
    assert any("render as a table" in n for n in three.notes)


def test_four_groupings_is_a_cross_product_rather_than_an_answer():
    error = refused("unparseable",
                    Plan(metrics=["revenue"], grain="month",
                         by=["market", "category", "shipping_mode"]))
    assert "cross product" in error.refusal.detail


def test_duplicate_keys_are_deduped_in_the_order_they_were_asked_for():
    out = validate(Plan(metrics=["profit", "revenue", "profit"],
                        by=["market", "market"]))
    assert out.metrics == ["profit", "revenue"] and out.by == ["market"]


# --------------------------------------------------------------------------- #
# the sort and the limit
# --------------------------------------------------------------------------- #

def test_a_ranked_question_with_no_stated_sort_ranks_by_its_own_measure():
    assert validate(Plan(metrics=["revenue"], by=["market"])).order_by == "-revenue"


def test_a_series_gets_no_default_sort_because_it_is_chronological():
    # Sorting a monthly result by value destroys the one thing a series is for.
    assert validate(Plan(metrics=["revenue"], grain="month")).order_by is None


def test_an_ungrouped_answer_gets_no_sort():
    assert validate(Plan(metrics=["revenue"])).order_by is None


def test_sorting_by_a_metric_not_asked_for_adds_the_column_and_says_so():
    out = validate(Plan(metrics=["revenue"], by=["category"],
                        order_by="-margin_pct"))
    assert out.metrics == ["revenue", "margin_pct"]
    assert any("margin_pct was added because the sort is on it" in n
               for n in out.notes)


def test_ascending_and_descending_are_the_leading_minus():
    assert validate(Plan(metrics=["profit"], by=["product"],
                         order_by="profit")).order_by == "profit"
    assert "lowest profit first" in validate(
        Plan(metrics=["profit"], by=["product"], order_by="profit")).describe()
    assert "highest profit first" in validate(
        Plan(metrics=["profit"], by=["product"], order_by="-profit")).describe()


def test_sorting_by_a_dimension_in_the_answer_is_allowed():
    # A distribution is ordered by its own column, not by height: `delay_days`.
    out = validate(Plan(metrics=["shipped_lines"], by=["delay_days"],
                        order_by="delay_days"))
    assert out.order_by == "delay_days" and out.metrics == ["shipped_lines"]


def test_sorting_by_something_that_is_not_a_column_at_all_is_refused():
    refused("unknown_metric", Plan(metrics=["revenue"], by=["market"],
                                   order_by="-popularity"))


def test_a_limit_above_the_cap_is_lowered_with_a_note():
    # A 200-row bar chart is not an answer, and refusing a limit is refusing a
    # question over its edge.
    out = validate(Plan(metrics=["revenue"], by=["product"], limit=5000))
    assert out.limit == P.MAX_LIMIT
    assert any("limited to " + str(P.MAX_LIMIT) in n for n in out.notes)


def test_a_nonsense_limit_is_dropped_rather_than_refused():
    assert validate(Plan(metrics=["revenue"], by=["market"], limit=0)).limit is None
    assert validate(Plan(metrics=["revenue"], by=["market"], limit=-3)).limit is None


def test_a_negative_thin_group_floor_is_refused():
    refused("unparseable", Plan(metrics=["revenue"], by=["market"], min_lines=-1))


# --------------------------------------------------------------------------- #
# dates and filters: the half that needs the snapshot
# --------------------------------------------------------------------------- #

def test_dates_the_wrong_way_round_are_swapped_with_a_note():
    out = validate(Plan(metrics=["revenue"], date_from="2017-12-31",
                        date_to="2017-01-01"))
    assert (out.date_from, out.date_to) == ("2017-01-01", "2017-12-31")
    assert any("wrong way round" in n for n in out.notes)


def test_a_date_that_is_not_a_date_is_unparseable():
    error = refused("unparseable", Plan(metrics=["revenue"],
                                        date_from="last Tuesday"))
    assert "YYYY-MM-DD" in error.refusal.detail


def test_a_window_outside_the_snapshot_names_the_window_the_snapshot_has(wh):
    error = refused("out_of_window",
                    Plan(metrics=["revenue"], date_from="2019-07-01",
                         date_to="2019-09-30"), wh)
    # Read from the data rather than hardcoded, so the message does not go stale
    # without failing after the next load.
    lo, hi = M.date_bounds(wh)
    assert lo.strftime("%Y-%m-%d") + " to " + hi.strftime("%Y-%m-%d") \
        in error.refusal.detail


def test_a_window_that_overlaps_is_clamped_rather_than_refused(wh):
    lo, hi = M.date_bounds(wh)
    out = validate(Plan(metrics=["revenue"], date_from="2010-01-01",
                        date_to="2030-01-01"), wh)
    assert out.date_from == lo.strftime("%Y-%m-%d")
    assert out.date_to == hi.strftime("%Y-%m-%d")
    assert len(out.notes) == 2


def test_a_filter_value_is_case_folded_against_the_column(wh):
    out = validate(Plan(metrics=["revenue"], where={"market": ["europe"]}), wh)
    # `Filters` compares bound values to the column as text, so `europe` would match
    # nothing and be summarised as if it had matched something.
    assert out.where == {"market": ["Europe"]}


def test_a_filter_value_that_matches_nothing_names_the_ones_that_do(wh):
    error = refused("unknown_value",
                    Plan(metrics=["revenue"], where={"market": ["Wakanda"]}), wh)
    assert error.refusal.suggestions


def test_a_four_digit_year_filter_becomes_a_window_and_says_so(wh):
    out = validate(Plan(metrics=["revenue"], where={"year": ["2017"]}), wh)
    assert out.where == {}
    assert out.date_from == "2017-01-01"
    assert any("read 2017 as a date range" in n for n in out.notes)
    # And the range it became faces the same clamp as one the model wrote, which is
    # why `_check_where` runs before `_check_dates`.
    assert out.date_to == M.date_bounds(wh)[1].strftime("%Y-%m-%d")


def test_a_month_or_quarter_filter_is_refused_rather_than_guessed_at():
    # "March" and "Q3" have no unambiguous spelling, and guessing a format is how a
    # filter silently matches nothing.
    error = refused("unparseable", Plan(metrics=["revenue"],
                                        where={"month": ["March"]}))
    assert "date range" in error.refusal.detail


def test_an_empty_filter_is_dropped_with_a_note():
    out = validate(Plan(metrics=["revenue"], where={"market": [""]}))
    assert out.where == {}
    assert any("empty filter" in n for n in out.notes)


def test_a_filter_naming_more_values_than_the_cap_is_a_grouping():
    error = refused("unparseable",
                    Plan(metrics=["revenue"],
                         where={"product": [str(i) for i in range(30)]}))
    assert "revenue by product" in error.refusal.suggestions


# --------------------------------------------------------------------------- #
# the funnel: the one purpose-built query, and the window that makes it one
# --------------------------------------------------------------------------- #

def test_a_funnel_metric_beside_a_funnel_dimension_is_routed_not_refused():
    out = validate(Plan(metrics=["views", "orders"], by=["category"]))
    assert out.kind == "funnel" and out.funnel_by == "category"
    assert out.by == [] and out.limit == P.MAX_LIMIT
    assert any("funnel query" in n for n in out.notes)


def test_a_funnel_metric_in_a_shape_the_log_cannot_serve_is_refused():
    # The refusal that has to stay a refusal: the log covers five of the fact
    # table's months, so a rate over the order window understates by about seven
    # times, in the direction that flatters it.
    error = refused("funnel_window", Plan(metrics=["view_to_order_pct"],
                                          date_from="2016-01-01"))
    assert "access log" in error.refusal.detail
    refused("funnel_window", Plan(metrics=["views"], by=["market"]))
    refused("funnel_window", Plan(metrics=["views"], grain="month"))
    refused("funnel_window", Plan(metrics=["views", "margin_pct"],
                                  by=["category"]))


def test_the_funnel_groups_only_where_it_can_join():
    error = refused("unknown_dimension", Plan(metrics=["views"], kind="funnel",
                                              funnel_by="market"))
    assert error.refusal.suggestions == P.FUNNEL_DIMS


def test_the_funnel_drops_a_window_it_cannot_honour_and_says_so():
    out = validate(Plan(metrics=["views"], kind="funnel", funnel_by="category",
                        date_from="2017-01-01"))
    assert out.date_from is None
    assert any("access log's own window" in n for n in out.notes)


def test_the_funnel_refuses_filters_rather_than_ignoring_them():
    refused("funnel_window", Plan(metrics=["views"], kind="funnel",
                                  where={"market": ["Europe"]}))


def test_a_follow_up_that_says_by_department_moves_the_funnel_grouping():
    # "by department" is what a user types; `funnel_by` is what the tool calls it.
    # Adopting the one makes `patch()` work across both shapes.
    out = validate(Plan(metrics=["views"], kind="funnel", by=["department"]))
    assert out.funnel_by == "department" and out.by == []


def test_an_unknown_plan_kind_is_unparseable():
    refused("unparseable", Plan(metrics=["revenue"], kind="rollup"))


# --------------------------------------------------------------------------- #
# execution: the routing, and the empty frame that must never become a chart
# --------------------------------------------------------------------------- #

def test_a_bare_grain_goes_through_timeseries(wh):
    # The only call that guarantees one row per period. It does not densify, so an
    # absent period stays absent - a zero would read as "we sold nothing", which is
    # a different claim from "the extract covers nothing here".
    frame = execute(wh, validate(Plan(metrics=["revenue"], grain="quarter"), wh))
    assert list(frame.columns)[0] == "quarter"
    assert len(frame) == frame["quarter"].nunique()


def test_a_grain_with_a_limit_goes_through_aggregate(wh):
    frame = execute(wh, validate(Plan(metrics=["revenue"], grain="month", limit=3),
                                 wh))
    assert len(frame) == 3


def test_the_funnel_frame_carries_no_join_keys(wh):
    frame = execute(wh, validate(Plan(metrics=["views"], kind="funnel",
                                      funnel_by="category"), wh))
    assert not [c for c in frame.columns if c.endswith("_key")]
    assert "views" in frame.columns and "orders" in frame.columns


def test_an_empty_result_names_the_thin_group_cut_off_as_the_cause(wh):
    plan = validate(Plan(metrics=["margin_pct"], by=["product"],
                         min_lines=5000), wh)
    with pytest.raises(PlanError) as caught:
        execute(wh, plan)
    assert caught.value.refusal.code == "empty_result"
    assert "at least 5000 order lines" in caught.value.refusal.detail


def test_an_empty_result_names_the_combination_when_that_is_what_emptied_it(wh):
    plan = validate(Plan(metrics=["revenue"], where={"market": ["LATAM"]},
                         date_from="2017-06-01", date_to="2017-06-30"), wh)
    with pytest.raises(PlanError) as caught:
        execute(wh, plan)
    assert caught.value.refusal.code == "empty_result"
    assert "Each part exists; the combination does not." \
        in caught.value.refusal.detail


def test_an_ungrouped_query_that_matched_nothing_is_still_empty(wh):
    # The shape with no chart to look blank: SQL returns one row for `sum(...)` over
    # zero rows, so this arrives as `revenue = NaN, n_lines = 0`. Left alone it is a
    # KPI tile reading "n/a" beside whatever sentence the model writes.
    plan = validate(Plan(metrics=["revenue"], where={"market": ["LATAM"]},
                         date_from="2017-06-01", date_to="2017-06-30"), wh)
    raw = M.aggregate(wh, plan.metrics, by=[], filters=plan.filters())
    assert len(raw) == 1 and raw["n_lines"].iloc[0] == 0
    with pytest.raises(PlanError):
        execute(wh, plan)


# --------------------------------------------------------------------------- #
# the plan as a value: what the model sees, and what a follow-up changes
# --------------------------------------------------------------------------- #

def test_to_dict_drops_empty_fields_so_the_prompt_reads_as_a_question():
    plan = validate(Plan(metrics=["revenue"], by=["market"]))
    assert plan.to_dict() == {"metrics": ["revenue"], "by": ["market"],
                              "order_by": "-revenue"}


def test_to_dict_names_the_kind_only_when_it_is_not_the_ordinary_one():
    assert "kind" not in validate(Plan(metrics=["revenue"])).to_dict()
    funnel = validate(Plan(metrics=["views"], kind="funnel"))
    assert funnel.to_dict()["kind"] == "funnel"
    assert funnel.to_dict()["funnel_by"] == "product"


def test_a_patch_changes_only_what_it_names():
    first = validate(Plan(metrics=["revenue"], where={"market": ["Europe"]}))
    second = first.patch({"by": ["region"]})
    assert second.by == ["region"] and second.metrics == ["revenue"]
    assert second.where == {"market": ["Europe"]}


def test_a_patch_with_an_empty_value_clears_the_field():
    # "drop the filter" has to be expressible, so present-and-empty differs from
    # absent. A key that is absent leaves the field alone.
    first = validate(Plan(metrics=["revenue"], where={"market": ["Europe"]}))
    assert first.patch({"where": {}}).where == {}


def test_a_patch_does_not_mutate_the_plan_it_came_from():
    # A caller holding the previous plan for a follow-up must still be holding it.
    first = validate(Plan(metrics=["revenue"], by=["market"]))
    first.patch({"by": ["region"], "metrics": ["profit"]})
    assert first.by == ["market"] and first.metrics == ["revenue"]


def test_a_patch_naming_a_field_a_plan_does_not_have_is_refused():
    with pytest.raises(PlanError) as caught:
        validate(Plan(metrics=["revenue"])).patch({"group_by": ["market"]})
    assert caught.value.refusal.code == "unparseable"


def test_validate_does_not_mutate_its_argument():
    plan = Plan(metrics=["revenue"], by=["month"], limit=5000)
    validate(plan)
    assert plan.by == ["month"] and plan.grain is None and plan.limit == 5000


def test_describe_names_the_metric_the_grouping_the_window_and_the_sort(wh):
    plan = validate(Plan(metrics=["revenue"], by=["category"],
                         where={"market": ["Europe"]}, date_from="2017-01-01",
                         limit=5, min_lines=10), wh)
    said = plan.describe()
    for part in ("Revenue", "by category", "Market in Europe", "2017-01-01",
                 "highest revenue first", "top 5", "under 10 lines excluded"):
        assert part in said, said


# --------------------------------------------------------------------------- #
# the boundaries the design doc claims hold by construction
# --------------------------------------------------------------------------- #

def test_the_agent_writes_no_sql():
    # Reason 1, structurally: `metrics` stays the only module that writes analytical
    # SQL, so the gates cannot be bypassed by anything the model influences.
    agent = SRC.parent
    for path in sorted(agent.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for keyword in ("SELECT ", "select ", "FROM ", "GROUP BY", "sql("):
            assert keyword not in text, (path.name, keyword)


def test_nothing_in_the_agent_imports_streamlit():
    # One `Answer` serves the CLI, the dashboard and a test, and the dependency runs
    # dashboard -> agent, never the reverse.
    for path in sorted(SRC.parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith("streamlit"), (path.name, name)
                assert "dashboard" not in name, (path.name, name)
