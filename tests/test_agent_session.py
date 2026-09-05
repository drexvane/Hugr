"""The ask loop: the order of the guardrails, session memory, and the boundary.

Three things this module is for, and the third is the one that would be hardest to
notice going wrong:

* **the order** - screen, plan, validate, execute, then summarise. A question this
  platform does not answer never becomes an API call, which is a privacy property
  before it is a cost one;
* **session memory as a patch** (design reason 13) - the model is handed the previous
  plan as JSON and returns only what changes, so "break that down by region" is
  `{"by": ["region"]}`. `ScriptedModel.calls` is how the prompt is inspected;
* **what leaves the process** (reason 20) - two calls, the second carrying the head
  of an *aggregated* frame. No row-level data and no personal column, because those
  are not dimensions and so cannot be in a plan. `calls` is the evidence.

The model here is always `ScriptedModel`, so nothing in this file needs a key.
"""

from __future__ import annotations

import pandas as pd
import pytest

from dtp import metrics as M
from dtp.agent import tools as T
from dtp.agent.client import Reply, ScriptedModel, plan_reply
from dtp.agent.session import Answer, Session, Tile, ask

PERSONAL = ("customer_first_name", "customer_last_name", "customer_street",
            "client_ip", "customer_email", "customer_password")


def scripted(*replies: Reply) -> ScriptedModel:
    return ScriptedModel(replies=list(replies))


def one(wh, plan: dict, summary: str = "") -> tuple[Answer, ScriptedModel]:
    model = scripted(plan_reply(**plan), Reply(text=summary))
    return Session(wh, model).ask("a question"), model


# --------------------------------------------------------------------------- #
# the order of the guardrails
# --------------------------------------------------------------------------- #

def test_a_screened_question_never_reaches_the_model(wh):
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]))
    answer = Session(wh, model).ask("who is our biggest customer?")
    assert answer.refusal.code == "personal_data"
    assert model.calls == []


def test_an_empty_question_is_refused_with_the_fallback_examples(wh):
    model = scripted()
    answer = Session(wh, model).ask("   ")
    assert answer.refusal.code == "unparseable"
    assert answer.refusal.suggestions
    assert model.calls == []


def test_a_model_that_calls_no_tool_is_read_as_a_decline(wh):
    model = scripted(Reply(text="CANNOT_ANSWER: there is no returns column here."))
    answer = Session(wh, model).ask("how many returns did we get?")
    assert answer.refusal.code == "unparseable"
    assert "no returns column" in answer.refusal.message
    # The tag is stripped: it is a protocol marker, not part of the sentence.
    assert T.DECLINE_TAG not in answer.refusal.message


def test_a_decline_is_classified_by_the_screen_not_by_the_model(wh):
    # Reason 8: the model's refusal is the fourth line of defence, so the code is
    # decided here. A decline that says "why" is one of ours, with a fallback
    # already written for it.
    model = scripted(Reply(text="CANNOT_ANSWER: I cannot say why revenue fell."))
    answer = Session(wh, model).ask("what is driving the drop in October?")
    assert answer.refusal.code == "causal"
    assert answer.refusal.suggestions


def test_a_model_calling_a_tool_this_platform_does_not_have_is_unparseable(wh):
    from dtp.agent.client import ToolCall
    model = scripted(Reply(tool_call=ToolCall(name="run_sql", input={})))
    answer = Session(wh, model).ask("revenue by market")
    assert answer.refusal.code == "unparseable"
    assert "run_sql" in answer.refusal.detail


def test_a_refusal_carries_no_frame_and_no_figure(wh):
    answer, _ = one(wh, {"metrics": ["returns"]})
    assert answer.refusal.code == "unknown_metric"
    assert answer.frame is None and answer.figure is None and answer.tiles == ()
    assert not answer.ok


# --------------------------------------------------------------------------- #
# the answer as a value
# --------------------------------------------------------------------------- #

def test_an_answer_carries_the_plan_the_frame_the_chart_and_both_sentences(wh):
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["market"]},
                    "Europe leads.")
    assert answer.ok and isinstance(answer.frame, pd.DataFrame)
    assert answer.chart == "bar" and answer.figure is not None
    assert answer.plan.to_dict()["by"] == ["market"]
    # `computed` is always populated beside the model's, which is what makes the
    # fallback in reason 11 a downgrade rather than a blank.
    assert answer.summary and answer.computed
    assert answer.model == "scripted"


def test_the_caption_says_what_actually_ran(wh):
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["category"],
                         "where": {"market": ["europe"]}, "limit": 3})
    # A user who cannot see the SQL can still see which metric, which grouping and
    # which window produced the number.
    for part in ("Revenue", "by category", "Market in Europe", "top 3"):
        assert part in answer.caption, answer.caption


def test_a_worst_first_ranking_is_described_as_the_end_it_shows(wh):
    # `say_ranking` sorts by the metric's own direction by default, so an ascending
    # plan used to get a sentence about the opposite end of its own chart. The plan
    # chose the order, so the plan tells the sentence which order it chose.
    answer, _ = one(wh, {"metrics": ["profit"], "by": ["product"],
                         "order_by": "profit", "limit": 3})
    assert "are the lowest at" in answer.computed
    assert "lead" not in answer.computed
    # And the sentence names the frame's own first row.
    assert str(answer.frame["product"].iloc[0])[:12] in answer.computed


def test_a_best_first_ranking_still_names_its_leaders(wh):
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["category"],
                         "order_by": "-revenue", "limit": 3})
    assert "lead with" in answer.computed and "at the top" in answer.computed


def test_a_sort_on_another_column_says_nothing_about_this_metric(wh):
    # The frame is ordered by orders; the sentence is about revenue, so the frame's
    # order tells it nothing and it falls back to the metric's own direction.
    answer, _ = one(wh, {"metrics": ["revenue", "orders"], "by": ["market"],
                         "order_by": "orders"})
    assert "lead with" in answer.computed


def test_the_tiles_come_from_their_own_query_not_from_the_frame(wh):
    # Summing a grouped `margin_pct` column averages a ratio, and a frame with a
    # limit holds the leaders rather than the total. One query per answer, so no two
    # numbers on screen disagree.
    answer, _ = one(wh, {"metrics": ["revenue", "margin_pct"],
                         "by": ["category"], "limit": 2})
    assert len(answer.frame) == 2
    assert [t.label for t in answer.tiles] == ["Revenue", "Margin"]
    assert answer.tiles[0].value == M.fmt_metric(
        "revenue", M.totals(wh, ["revenue"])["revenue"], compact=True)
    assert any("totals for the whole window" in n for n in answer.notes)


def test_every_tile_value_is_formatted_by_the_metric_registry(wh):
    answer, _ = one(wh, {"metrics": ["revenue", "margin_pct", "orders"]})
    assert all(isinstance(t, Tile) and t.value for t in answer.tiles)
    assert answer.tiles[0].value.startswith("$")
    assert answer.tiles[1].value.endswith("%")


def test_the_text_of_a_refusal_is_the_refusal(wh):
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["salesperson"]})
    assert answer.text() == answer.refusal.message


def test_the_text_of_an_answer_holds_the_caption_the_tiles_and_the_sentence(wh):
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["market"]},
                    "Europe leads.")
    text = answer.text()
    assert answer.caption in text and "Revenue:" in text
    assert answer.summary in text


# --------------------------------------------------------------------------- #
# session memory: a patch, not a transcript
# --------------------------------------------------------------------------- #

def test_a_follow_up_changes_only_what_it_names(wh):
    # Roadmap 3.3's own test: "break that down by region".
    model = scripted(plan_reply(metrics=["revenue"], where={"market": ["Europe"]}),
                     Reply(text=""),
                     plan_reply(by=["region"]), Reply(text=""))
    s = Session(wh, model)
    s.ask("revenue in Europe")
    second = s.ask("break that down by region")
    assert second.ok
    assert second.plan.by == ["region"]
    assert second.plan.metrics == ["revenue"]
    assert second.plan.where == {"market": ["Europe"]}


def test_the_previous_plan_is_what_the_model_is_given_as_memory(wh):
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""),
                     plan_reply(by=["region"]), Reply(text=""))
    s = Session(wh, model)
    s.ask("revenue by market")
    s.ask("by region instead")
    # The third call is the second question's plan call. Memory is the plan's own
    # JSON, not a transcript - which is a much smaller thing to get right.
    system = model.calls[2][0]
    assert "This is a follow-up" in system
    assert '"by": ["market"]' in system
    assert "anything you omit stays as it is" in system
    # And no previous *question* is replayed, because nothing here stores one.
    assert "revenue by market" not in model.calls[2][1]


def test_the_first_question_is_not_told_it_is_a_follow_up(wh):
    _, model = one(wh, {"metrics": ["revenue"], "by": ["market"]})
    assert "This is a follow-up" not in model.calls[0][0]


def test_a_follow_up_that_clears_a_filter_clears_it(wh):
    model = scripted(plan_reply(metrics=["revenue"], where={"market": ["Europe"]}),
                     Reply(text=""),
                     plan_reply(where={}), Reply(text=""))
    s = Session(wh, model)
    s.ask("revenue in Europe")
    assert s.ask("across all markets now").plan.where == {}


def test_a_follow_up_after_a_funnel_question_is_not_still_a_funnel(wh):
    # `kind` is derived from the metrics, never remembered. A remembered funnel would
    # answer "revenue by market" by product and silently drop the grouping.
    model = scripted(plan_reply(metrics=["views", "orders"], by=["category"]),
                     Reply(text=""),
                     plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""))
    s = Session(wh, model)
    assert s.ask("views and orders by category").plan.kind == "funnel"
    second = s.ask("revenue by market")
    assert second.plan.kind == "aggregate" and second.plan.by == ["market"]


def test_a_refused_follow_up_gets_the_refusal_not_the_previous_answer_again(wh):
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""),
                     plan_reply(by=["salesperson"]), Reply(text=""))
    s = Session(wh, model)
    first = s.ask("revenue by market")
    second = s.ask("by salesperson")
    assert second.refusal.code == "unknown_dimension"
    # And the plan that worked is still the memory, so "no, by region" after a
    # refusal is a follow-up to the last thing that worked.
    assert s.plan.to_dict() == first.plan.to_dict()


def test_reset_makes_the_next_question_a_new_one(wh):
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""),
                     plan_reply(metrics=["profit"], by=["category"]),
                     Reply(text=""))
    s = Session(wh, model)
    s.ask("revenue by market")
    s.reset()
    assert s.ask("profit by category").plan.metrics == ["profit"]
    assert "This is a follow-up" not in model.calls[2][0]


def test_pointing_at_another_snapshot_clears_the_plan(wh):
    # Reason 15: a plan is only meaningful inside one snapshot, and reinterpreting
    # yesterday's keys against different data is worse than asking again.
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""))
    s = Session(wh, model)
    s.ask("revenue by market")
    assert s.plan is not None
    s.use(wh, snapshot="20200202T000000")
    assert s.plan is None and s.snapshot == "20200202T000000"


def test_pointing_at_the_same_snapshot_keeps_the_plan(wh):
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""))
    s = Session(wh, model)
    s.ask("revenue by market")
    s.use(wh)
    assert s.plan is not None


def test_the_session_log_holds_every_answer_including_the_refusals(wh):
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""),
                     plan_reply(metrics=["returns"]))
    s = Session(wh, model)
    s.ask("revenue by market")
    s.ask("how many returns?")
    s.ask("who is our biggest customer?")
    assert len(s.log) == 3
    assert [a.ok for a in s.log] == [True, False, False]


def test_nothing_is_written_to_disk(wh, tmp_path, monkeypatch):
    # Reason 15: sessions are one process. Persisting question history would mean
    # storing what people asked about customers, which is a decision nobody took.
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text="Europe leads."))
    Session(wh, model).ask("revenue by market")
    assert set(tmp_path.rglob("*")) == before


def test_the_one_shot_helper_keeps_no_memory(wh):
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text=""),
                     plan_reply(metrics=["profit"], by=["category"]),
                     Reply(text=""))
    assert ask("revenue by market", wh, model).ok
    # A second `ask` is a new session, so the second plan is read on its own rather
    # than patched onto the first.
    second = ask("profit by category", wh, model)
    assert second.plan.metrics == ["profit"]
    assert "This is a follow-up" not in model.calls[2][0]


# --------------------------------------------------------------------------- #
# what leaves the process
#
# This is the one place in the project that sends anything anywhere - `dtp.monitoring`
# has no network sink at all and a test enforces that - so the payload is asserted
# rather than described (design reason 20).
# --------------------------------------------------------------------------- #

def test_a_question_costs_two_calls_and_no_more(wh):
    _, model = one(wh, {"metrics": ["revenue"], "by": ["market"]}, "Europe leads.")
    assert len(model.calls) == 2


def test_a_refused_question_costs_one_call(wh):
    # The validator refuses before anything runs, so there is no frame to summarise.
    _, model = one(wh, {"metrics": ["revenue"], "by": ["salesperson"]})
    assert len(model.calls) == 1


def test_no_personal_column_is_named_in_anything_sent(wh):
    _, model = one(wh, {"metrics": ["revenue"], "by": ["market", "category"]},
                   "Europe leads.")
    sent = " ".join(system + " " + user for system, user in model.calls)
    # A column *name* in the prompt is what would invite the model to put it in a
    # plan, so the names are the thing to keep out. The English words are not: rule 3
    # says "a name, email, street or IP" on purpose, because a model told what the
    # platform will not answer declines instead of guessing.
    for column in PERSONAL:
        assert column not in sent


def test_no_personal_value_appears_in_anything_sent(wh):
    _, model = one(wh, {"metrics": ["views"], "by": ["product"]}, "Polo leads.")
    sent = " ".join(system + " " + user for system, user in model.calls)
    # The fixture's one personal column is `client_ip` on the access logs, and a
    # views question is the one that reads that table. Its values are the concrete
    # thing that must not cross the boundary (design reason 20).
    addresses = wh.sql("SELECT DISTINCT client_ip FROM access_logs")["client_ip"]
    assert len(addresses) > 1
    for address in addresses:
        assert str(address) not in sent


def test_the_second_call_carries_only_an_aggregated_frame(wh):
    _, model = one(wh, {"metrics": ["revenue"], "by": ["market"]}, "Europe leads.")
    digest = model.calls[1][1]
    # Group labels and metric values, formatted the way the screen shows them.
    assert "Market | Revenue" in digest
    assert "Europe" in digest and "$" in digest
    # And nothing row-level: an order id is not a dimension and cannot be in a plan.
    assert "order_id" not in digest and "order_item_id" not in digest
    assert digest.count("\n") < 20


def test_the_frame_is_truncated_before_the_model_sees_it(wh):
    _, model = one(wh, {"metrics": ["revenue"], "by": ["product", "category"]},
                   "")
    digest = model.calls[1][1]
    body = [line for line in digest.splitlines() if " | " in line]
    assert len(body) <= T.HEAD_ROWS + 1     # + the header


def test_the_digest_says_how_many_rows_were_withheld(wh):
    frame = pd.DataFrame({"market": list("abcdefghijklmnop"),
                          "revenue": range(16)})
    from dtp.agent.plan import Plan
    digest = T.frame_digest(frame, Plan(metrics=["revenue"], by=["market"]),
                            rows=4)
    assert "12 more rows, sorted the same way" in digest
    assert "Rows: 16." in digest


def test_the_summary_call_gets_no_tools(wh):
    # A tool schema on the summary call would let the model start another query from
    # inside a sentence. There is one plan per answer, and it has already run.
    seen: list[object] = []
    model = scripted(plan_reply(metrics=["revenue"], by=["market"]),
                     Reply(text="Europe leads."))
    original = model.respond

    def spy(system, user, tools=None, max_tokens=1024):
        seen.append(tools)
        return original(system, user, tools=tools, max_tokens=max_tokens)

    model.respond = spy                                  # type: ignore[method-assign]
    Session(wh, model).ask("revenue by market")
    assert seen[0] and seen[1] is None


def test_the_plan_prompt_names_every_registry_key_and_no_personal_column(wh):
    _, model = one(wh, {"metrics": ["revenue"], "by": ["market"]})
    system = model.calls[0][0]
    for key in M.METRICS:
        assert key in system, key
    for key in M.DIMENSIONS:
        assert key in system, key
    for column in PERSONAL:
        assert column not in system


def test_the_plan_prompt_states_the_window_read_from_the_data(wh):
    _, model = one(wh, {"metrics": ["revenue"], "by": ["market"]})
    lo, hi = M.date_bounds(wh)
    assert lo.strftime("%Y-%m-%d") in model.calls[0][0]
    assert hi.strftime("%Y-%m-%d") in model.calls[0][0]


# --------------------------------------------------------------------------- #
# the hallucination guardrail, end to end
# --------------------------------------------------------------------------- #

def test_a_true_sentence_is_kept_and_recorded_as_verified(wh):
    revenue = M.fmt_metric("revenue", M.totals(wh, ["revenue"])["revenue"])
    answer, _ = one(wh, {"metrics": ["revenue"]},
                    "Revenue across the window is " + revenue + ".")
    assert answer.verified is True
    assert answer.withheld == ""
    assert revenue in answer.summary


def test_a_fabricated_figure_drops_the_whole_sentence(wh):
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["market"]},
                    "Europe leads with $9,100.00, well clear of the rest.")
    assert answer.verified is False
    # The templated line substitutes, and the downgrade is visible rather than quiet.
    assert answer.summary == answer.computed
    assert "$9,100.00" in answer.withheld
    assert "withheld" in answer.text()


def test_a_fabricated_group_name_drops_the_sentence_too(wh):
    # Reason 21's strict half, reached through the session's own label scan: the
    # frame holds three markets and "Western Europe" is a region in the data.
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["market"]},
                    "Western Europe leads the field.")
    assert answer.verified is False
    assert "Western Europe" in answer.withheld


def test_the_label_scan_reads_the_whole_drill_path(wh):
    # A model shown five markets knows world geography and may name a country it was
    # never given, so the scan widens to the path rather than the one column.
    countries = M.dimension_values(wh, "country", limit=50)
    answer, _ = one(wh, {"metrics": ["revenue"], "by": ["market"]},
                    str(countries[0]) + " leads the field.")
    assert answer.verified is False
    assert str(countries[0]) in answer.withheld


def test_no_number_the_model_writes_is_ever_a_tile_or_an_axis(wh):
    # Layer 1 (reason 9): the model is never asked for a figure and no figure it
    # emits is rendered. The tiles here are the same whatever it says.
    truthful, _ = one(wh, {"metrics": ["revenue"], "by": ["market"]}, "")
    lying, _ = one(wh, {"metrics": ["revenue"], "by": ["market"]},
                   "Revenue was $99,999.00 across two markets.")
    assert [t.value for t in truthful.tiles] == [t.value for t in lying.tiles]
    assert truthful.frame.equals(lying.frame)
    # Not in the sentence shown and not in the frame behind it. It does appear in the
    # withheld note, which is the point of that note: the drop is visible.
    assert "99,999" not in lying.summary
    assert "99,999" not in lying.frame.to_string()
    assert "99,999" in lying.withheld
