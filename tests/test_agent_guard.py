"""The guardrails, tested against a model that lies and a doc that could drift.

Three things live here, and they are the three the design doc claims are structural
rather than prompted (`docs/03-agent-design.md` 6-12, 19-21):

* **the refusal table** - every code the package can raise is documented in
  `REFUSALS` *and* in the doc's guardrail table, and every documented code is used.
  A refusal nobody wrote a fallback for is the failure roadmap 3.1 names;
* **the scope screen** - precision-first, so both halves are measured: the eleven
  questions that must fire, and the near-misses that must not, because a false
  positive costs an answerable question;
* **the numeric verifier** - checked against fabricated sentences that must be
  caught and true derived readings that must not be.

Nothing here needs a warehouse or a model: a frame and a string are the whole input.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from dtp import metrics as M
from dtp.agent import guard
from dtp.agent.guard import PlanError, Refusal, refuse, scope_screen, verify_summary

DOC = Path(__file__).resolve().parents[1] / "docs" / "03-agent-design.md"
AGENT = Path(M.__file__).parent / "agent"


def frame(**columns) -> pd.DataFrame:
    return pd.DataFrame(columns)


# --------------------------------------------------------------------------- #
# the refusal table
# --------------------------------------------------------------------------- #

def test_every_refusal_code_is_documented_in_the_design_doc():
    # The doc's guardrail table is the reviewable half of this contract: a code
    # added in code and not in the table is a refusal with no defined fallback.
    # Scoped to that one section, because the tool-schema table above it has the
    # same shape and its rows are field names rather than codes.
    text = DOC.read_text(encoding="utf-8")
    start = text.index("## Guardrails")
    section = text[start:text.index("\n## ", start)]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", section,
                                flags=re.MULTILINE))
    assert set(guard.REFUSALS) == documented, \
        set(guard.REFUSALS) ^ documented


def test_every_refusal_code_is_actually_raised_somewhere():
    used: set[str] = set()
    for path in sorted(AGENT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"refuse\(\s*\"([a-z_]+)\"", text))
        used |= set(re.findall(r"code=\"([a-z_]+)\"", text))
    # `_SCOPE_DETAIL`'s five are raised through `scope_screen`, which builds the
    # Refusal from the table rather than naming a literal code.
    used |= set(guard._SCOPE_DETAIL)
    assert used == set(guard.REFUSALS), set(guard.REFUSALS) - used


def test_an_undocumented_code_cannot_be_constructed():
    with pytest.raises(ValueError, match="undocumented refusal code"):
        Refusal(code="not_in_the_table")


def test_a_refusal_names_what_to_ask_instead():
    r = Refusal(code="unknown_metric", detail="'returns' is not a metric here.",
                suggestions=("revenue by market", "margin by category"))
    assert r.message.startswith(guard.REFUSALS["unknown_metric"])
    assert "'returns' is not a metric here." in r.message
    assert r.message.endswith("Try: revenue by market; margin by category.")


def test_every_screened_code_offers_a_fallback():
    # Roadmap 3.1's "defined fallback for every out-of-scope type". The screened
    # five are the ones a user meets most often, so silence here is worst there.
    for code in guard._SCOPE_DETAIL:
        assert guard._scope_detail(code).strip()
        assert guard._scope_suggestions(code)


def test_every_suggestion_groups_by_something_the_registry_has():
    """A fallback that suggests an unaskable question is worse than none.

    `orders by city` was in this table, and city is deliberately not a dimension -
    the geography path stops at country so no amount of drilling reaches a customer.
    A user who took that suggestion got `unknown_dimension` for their trouble, which
    is the failure roadmap 3.1 is about, delivered by the fallback meant to prevent
    it.

    The rule this checks is small enough to hold everywhere: whatever follows "by" in
    a suggestion is either a dimension (a grouping) or a metric (a ranking).
    """
    from dtp.agent.session import EXAMPLES

    known = {key.replace("_", " ") for key in {**M.METRICS, **M.DIMENSIONS}}
    known |= {item.label.lower()
              for item in list(M.METRICS.values()) + list(M.DIMENSIONS.values())}
    known |= {word + "s" for word in known} | {word[:-1] + "ies"
                                               for word in known
                                               if word.endswith("y")}
    every = list(EXAMPLES)
    for suggestions in guard._SCOPE_SUGGESTIONS.values():
        every += list(suggestions)
    checked = 0
    for suggestion in every:
        _, sep, tail = suggestion.lower().partition(" by ")
        if not sep:
            continue
        for phrase in tail.split(" and "):
            checked += 1
            assert phrase.strip(" .?") in known, (suggestion, phrase)
    assert checked >= 8, "the parse found nothing, so it proved nothing"


def test_refuse_returns_an_exception_carrying_the_refusal():
    error = refuse("empty_result", "nothing matched", ["revenue by market"])
    assert isinstance(error, PlanError)
    assert error.refusal.code == "empty_result"
    assert "nothing matched" in str(error)


# --------------------------------------------------------------------------- #
# the scope screen: both halves, because precision is the property claimed
# --------------------------------------------------------------------------- #

FIRES = [
    ("who is our biggest customer?", "personal_data"),
    ("who are the top customers", "personal_data"),
    ("which customers ordered twice in March?", "personal_data"),
    ("list customer names by revenue", "personal_data"),
    ("show me their first names", "personal_data"),
    ("which IP hit us most?", "personal_data"),
    ("what is the client ip with the most views", "personal_data"),
    ("forecast revenue for the rest of the year", "forecast"),
    ("what will revenue be next quarter?", "forecast"),
    ("predicted margin by category", "forecast"),
    ("why did revenue drop in October?", "causal"),
    ("what caused the margin fall", "causal"),
    ("what drove the October break", "causal"),
    ("root cause of the late shipments", "causal"),
    ("delete the cancelled rows", "write"),
    ("remove all the cancelled orders", "write"),
    ("drop the order_items table", "write"),
    ("update the target to 5m", "write"),
    ("overwrite the status column", "write"),
    ("run SELECT * FROM order_items GROUP BY market", "raw_sql"),
    ("can you write me some SQL for this", "raw_sql"),
]

# The other half, and the more expensive one to get wrong: every question here is
# answerable, and several are deliberate near-misses of a pattern above.
PASSES = [
    "revenue by market",
    "revenue by customer segment",
    "orders by city",
    "drop groups with under 100 lines",
    "the on-time rate by shipping mode, ignoring modes under ten lines",
    "percentage change in orders month over month",
    "zip code coverage by region",
    "what is the biggest change in revenue?",
    "which categories are late most often?",
    "how many orders were cancelled?",
    "revenue by order status",
    "monthly revenue for 2017",
    "the ten worst products by profit",
    "how late are our shipments?",
    "what are our total revenue and margin?",
    "views and orders by category",
    "set the thin-group floor to 50 lines",
    "update on the pipeline please",
]


@pytest.mark.parametrize("question,code", FIRES, ids=[q for q, _ in FIRES])
def test_the_screen_fires_on_the_questions_it_is_for(question, code):
    screened = scope_screen(question)
    assert screened is not None, question
    assert screened.code == code, screened.code


@pytest.mark.parametrize("question", PASSES)
def test_the_screen_lets_answerable_questions_through(question):
    # A false positive here costs a question the platform can answer, which is a
    # worse trade than a miss: a missed pattern falls through to a structural code,
    # so it costs clarity rather than safety (design reason 8).
    assert scope_screen(question) is None, scope_screen(question).code


def test_naming_beats_ranking_when_a_question_is_both():
    # "who are our top customers" is a ranking *and* a naming question, and the
    # naming half is the one that must not be answered - hence personal_data first
    # in SCOPE_PATTERNS.
    assert scope_screen("who are our top customers by revenue").code \
        == "personal_data"


def test_the_screen_is_case_insensitive():
    assert scope_screen("WHY DID REVENUE DROP?").code == "causal"
    assert scope_screen("Delete The Cancelled Rows").code == "write"


def test_the_screen_reads_the_whole_question_not_only_the_start():
    assert scope_screen(
        "revenue by market, and also why did it fall in October").code == "causal"


# --------------------------------------------------------------------------- #
# the numeric verifier
#
# The frame is built by hand so every expected number can be done in the head:
# revenue 5,000 / 3,000 / 2,000 (total 10,000), margin 20% / 10% / 5%.
# --------------------------------------------------------------------------- #

@pytest.fixture
def market_frame() -> pd.DataFrame:
    return frame(market=["Europe", "Pacific Asia", "LATAM"],
                 revenue=[5000.0, 3000.0, 2000.0],
                 margin_pct=[20.0, 10.0, 5.0],
                 n_lines=[40, 15, 10])


@pytest.mark.parametrize("said", [
    "Europe leads with $5,000.00.",
    "Europe at $5,000.00 is ahead of Pacific Asia at $3,000.00.",
    "Revenue across the three markets is $10,000.00.",
    "Europe takes 50.0% of revenue.",
    "Europe's margin is 10.0 points above Pacific Asia's.",
    "Europe's revenue is 66.7% higher than Pacific Asia's.",
    "The top 2 markets carry $8,000.00 between them.",
    "Europe and Pacific Asia together are $8,000.00.",
    "Average revenue per market is $3,333.33.",
    "The gap between best and worst is $3,000.00.",
    "Europe carries 40 of the 65 order lines.",
    "There are 3 markets in this result.",
])
def test_a_true_sentence_survives(said, market_frame):
    check = verify_summary(said, market_frame)
    assert check.ok, check.problems


@pytest.mark.parametrize("said", [
    "Europe leads with $9,100.00.",
    "Europe takes 74.0% of revenue.",
    "Revenue across the three markets is $12,500.00.",
    "Europe's margin is 41.2%.",
])
def test_a_fabricated_figure_is_caught(said, market_frame):
    check = verify_summary(said, market_frame)
    assert not check.ok
    assert check.problems
    assert check.note.startswith("model summary withheld (unverifiable:")


def test_a_sentence_with_no_figures_passes_without_having_been_tested(market_frame):
    # `checked` is recorded because zero is a meaningful answer: this sentence is
    # unverifiable in the sense of untested, not in the sense of wrong.
    check = verify_summary("Europe is well ahead of the other markets.",
                           market_frame)
    assert check.ok and check.checked == 0


def test_a_decorated_literal_is_held_to_its_own_kind(market_frame):
    # 20 is a real percentage in this frame and is not a real dollar amount, so a
    # money literal must not be waved through by a coincidental ratio.
    assert verify_summary("Europe's margin is 20.0%.", market_frame).ok
    assert not verify_summary("Europe made $20.00.", market_frame).ok


def test_a_loss_verifies_however_the_model_writes_the_sign():
    losses = frame(category=["Games", "Apparel"], profit=[-40.0, 120.0])
    # `fmt_metric` prints -$40.00, a model may write "a loss of $40.00", and prose
    # carries direction in words. Magnitude matching is what keeps both true.
    assert verify_summary("Games lost $40.00.", losses).ok
    assert verify_summary("Games came in at -$40.00.", losses).ok
    assert not verify_summary("Games lost $70.00.", losses).ok


def test_a_period_in_the_prose_is_not_read_as_a_figure():
    series = frame(month=pd.to_datetime(["2017-11-01", "2017-12-01"]),
                   revenue=[1000.0, 1200.0])
    # "2017" is a label, not a claim about revenue, and reading it as one flagged
    # every true sentence about a time series.
    assert verify_summary("Revenue rose to $1,200.00 in December 2017.", series).ok
    assert verify_summary("Q4 2017 ended at $1,200.00.", series).ok


def test_a_group_the_frame_does_not_hold_is_fabrication_even_with_true_numbers(
        market_frame):
    # Reason 21's strict half: every figure here is real and the sentence is still
    # about a row nobody was shown.
    check = verify_summary("Western Europe leads with $5,000.00.", market_frame,
                           known_labels=("Western Europe", "Europe", "Oceania"))
    assert not check.ok and check.problems == ("Western Europe",)


def test_a_name_inside_a_label_the_frame_holds_is_not_a_claim_of_its_own():
    # `Apparel` is a department and `Women's Apparel` a category; one contains the
    # other as text. A sentence quoting the category is not naming the department.
    categories = frame(category=["Women's Apparel", "Sporting Goods"],
                       revenue=[5700.0, 2000.0])
    assert verify_summary("Women's Apparel takes $5,700.00.", categories,
                          known_labels=("Apparel", "Technology")).ok
    # The reverse still fails: a frame grouped by department, described in
    # categories, is a sentence about rows nobody was shown.
    departments = frame(department=["Apparel", "Technology"],
                        revenue=[5700.0, 2000.0])
    check = verify_summary("Women's Apparel takes $5,700.00.", departments,
                           known_labels=("Women's Apparel", "Apparel"))
    assert not check.ok and check.problems == ("Women's Apparel",)


def test_a_short_name_is_not_matched_on(market_frame):
    # Values this short exist in the data (`US`, `EU`, and a two-letter state code)
    # and are not distinctive enough to search prose for. Without the length floor,
    # "it" and "is" would be flagged as fabricated group names.
    assert guard._MIN_LABEL >= 3
    check = verify_summary("It is Europe, at $5,000.00.", market_frame,
                           known_labels=("It", "is", "EU"))
    assert check.ok, check.problems


def test_the_whole_sentence_is_dropped_not_the_offending_figure(market_frame):
    check = verify_summary(
        "Europe leads with $5,000.00, well ahead of the $9,100.00 second place.",
        market_frame)
    # One bad figure among true ones still fails the sentence: editing prose to
    # remove a number leaves grammar that reads as if it were checked (reason 11).
    assert not check.ok and check.checked == 2
    assert check.problems == ("$9,100.00",)
