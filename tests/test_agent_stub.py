"""The keyless stub: what `dtp ask --stub` and the dashboard run with no credential.

`KeywordModel` is not a model and the tests here are not measuring intent parsing.
What they pin is the three properties that make it safe to ship as the default when
no key is present:

* **it recognises the plain shapes and declines the rest.** A stub that guessed would
  answer a question nobody asked, which is the failure the guardrails exist for. The
  decline uses the same `CANNOT_ANSWER` tag a real model uses, so it travels through
  the same path;
* **it writes no prose**, so every answer falls back to the templated sentence
  derived from the frame. There is no model here to have written one, and inventing
  one would be the fabrication reason 11 is about;
* **it cannot reach past the registry**, because a plan is the only thing it emits and
  the registry is the only thing it reads.

The last section is the useful one: every fallback suggestion the refusals offer is
run through the stub against a real snapshot, so a suggestion that cannot be answered
fails here rather than in front of a user.
"""

from __future__ import annotations

import pytest

from dtp import metrics as M
from dtp.agent import guard
from dtp.agent.client import KeywordModel, read_keywords
from dtp.agent.session import EXAMPLES, Session
from dtp.agent.tools import DECLINE_TAG

SHAPES = [
    ("revenue by market", {"metrics": ["revenue"], "by": ["market"]}),
    ("orders by country", {"metrics": ["orders"], "by": ["country"]}),
    ("revenue and margin by category",
     {"metrics": ["revenue", "margin_pct"], "by": ["category"]}),
    ("the on-time rate by shipping mode",
     {"metrics": ["on_time_pct"], "by": ["shipping_mode"]}),
    ("discount rate by department",
     {"metrics": ["discount_pct"], "by": ["department"]}),
    ("view-to-order rate by product",
     {"metrics": ["view_to_order_pct"], "by": ["product"]}),
    ("what is our total revenue?", {"metrics": ["revenue"]}),
]


@pytest.mark.parametrize("question,fields", SHAPES,
                         ids=[q for q, _ in SHAPES])
def test_the_plain_shapes_are_read(question, fields):
    assert read_keywords(question) == fields


def test_a_longer_key_is_not_eaten_by_a_shorter_one():
    # `discount` and `discount rate` are different metrics, and `views` sits inside
    # `view-to-order rate`. Longest-first matching is what keeps them apart.
    assert read_keywords("discount by market")["metrics"] == ["discount"]
    assert read_keywords("discount rate by market")["metrics"] == ["discount_pct"]
    assert read_keywords("page views by product")["metrics"] == ["views"]


def test_the_metrics_come_back_in_the_order_the_question_named_them():
    # A funnel reads left to right on screen, so this is not cosmetic.
    assert read_keywords("views and orders by category")["metrics"] \
        == ["views", "orders"]
    assert read_keywords("orders and views by category")["metrics"] \
        == ["orders", "views"]


def test_a_grouping_can_be_named_on_either_side_of_by():
    # "the ten worst products by profit" groups by product and ranks by profit; the
    # dimension is on the left of "by" and the metric on the right.
    assert read_keywords("the ten worst products by profit") == {
        "metrics": ["profit"], "by": ["product"], "limit": 10,
        "order_by": "profit"}


def test_a_ranking_word_sorts_ascending_and_a_count_becomes_a_limit():
    assert read_keywords("top 3 categories by revenue")["limit"] == 3
    assert "order_by" not in read_keywords("top 3 categories by revenue")
    worst = read_keywords("the 5 worst products by margin")
    assert worst["limit"] == 5 and worst["order_by"] == "margin_pct"


def test_a_count_that_is_not_a_number_leaves_the_ranking_unlimited():
    # "the worst products" is a ranking without a cut-off, and inventing one would be
    # answering a question about ten rows that was asked about all of them.
    read = read_keywords("the worst products by profit")
    assert "limit" not in read and read["order_by"] == "profit"


def test_a_trend_word_asks_for_a_grain_rather_than_a_grouping():
    # `by: [month]` is a bar chart with one bar per month; `grain: month` is a line.
    monthly = read_keywords("monthly revenue")
    assert monthly["grain"] == "month" and "by" not in monthly
    assert read_keywords("quarterly profit")["grain"] == "quarter"
    assert read_keywords("revenue year over year")["grain"] == "year"


def test_a_year_becomes_the_window():
    read = read_keywords("revenue by market in 2017")
    assert read["date_from"] == "2017-01-01" and read["date_to"] == "2017-12-31"


def test_the_ungated_figure_is_only_ever_named_on_request():
    # Matching it on the word "revenue" would make the stub answer with the number
    # the whole registry exists to avoid.
    assert read_keywords("revenue by market")["metrics"] == ["revenue"]
    assert read_keywords("revenue ungated by market")["metrics"] \
        == ["revenue_ungated"]


def test_a_question_naming_no_metric_reads_as_nothing():
    for question in ("how many returns did we get?", "who is our biggest customer?",
                     "", "hello"):
        assert "metrics" not in read_keywords(question), question


# --------------------------------------------------------------------------- #
# through the session
# --------------------------------------------------------------------------- #

def test_a_recognised_question_answers_with_no_model_and_no_key(wh):
    answer = Session(wh, KeywordModel()).ask("revenue by market")
    assert answer.ok and answer.model == "keyword-stub"
    # `order_by` is the validator's own default for a grouped query, not something
    # the stub read: it is in every plan of this shape whoever sent it.
    assert answer.plan.to_dict() == {"metrics": ["revenue"], "by": ["market"],
                                     "order_by": "-revenue"}
    assert answer.frame is not None and answer.tiles


def test_an_unrecognised_question_declines_rather_than_guessing(wh):
    answer = Session(wh, KeywordModel()).ask("how many returns did we get?")
    assert answer.refusal.code == "unparseable"
    assert "keyless stub" in answer.refusal.message
    assert DECLINE_TAG not in answer.refusal.message
    # And it says what to do about it, which is the whole point of the fallback.
    assert "ANTHROPIC_API_KEY" in answer.refusal.message


def test_the_stub_writes_no_sentence_so_the_computed_one_stands(wh):
    answer = Session(wh, KeywordModel()).ask("revenue by market")
    assert answer.summary == answer.computed
    assert answer.summary
    # Not "verified": nothing was checked, because nothing was claimed.
    assert answer.verified is False
    assert answer.withheld == ""


def test_the_screen_still_runs_before_the_stub(wh):
    answer = Session(wh, KeywordModel()).ask("why did revenue drop in October?")
    assert answer.refusal.code == "causal"


def test_a_follow_up_patches_the_stub_s_plan_too(wh):
    # "by category" names no metric, and on a first question that is a decline. On a
    # follow-up it is a patch: the metric is in the plan the prompt already carries,
    # which is how `--repl` works without a key.
    session = Session(wh, KeywordModel())
    session.ask("revenue by market")
    second = session.ask("by category")
    assert second.ok
    assert second.plan.metrics == ["revenue"]
    assert second.plan.by == ["category"]


def test_naming_no_metric_is_still_a_decline_on_a_first_question(wh):
    assert Session(wh, KeywordModel()).ask("by category").refusal.code \
        == "unparseable"


# --------------------------------------------------------------------------- #
# every fallback the refusals offer, actually asked
# --------------------------------------------------------------------------- #

def _suggestions() -> list[str]:
    out = list(EXAMPLES)
    for group in guard._SCOPE_SUGGESTIONS.values():
        out += list(group)
    return out


@pytest.mark.parametrize("suggestion", _suggestions())
def test_no_fallback_suggestion_names_something_that_does_not_exist(suggestion, wh):
    """Take every refusal's own advice, and see what happens.

    The stub cannot read every one of them - "the biggest movers month over month" is
    prose a real model would turn into a plan - and that is allowed. What is not
    allowed is a suggestion that parses into a plan the registry rejects: that is a
    refusal handing the user a second refusal.
    """
    answer = Session(wh, KeywordModel()).ask(suggestion)
    if answer.ok:
        return
    assert answer.refusal.code not in (
        "unknown_metric", "unknown_dimension", "unknown_value"), \
        (suggestion, answer.refusal.message)


def test_the_suggestions_are_not_all_unreadable(wh):
    # Otherwise the test above passes by never testing anything.
    answered = [s for s in _suggestions()
                if Session(wh, KeywordModel()).ask(s).ok]
    assert len(answered) >= 6, answered
