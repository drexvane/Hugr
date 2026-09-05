"""Run every question in `tests/agent_questions.yml` against the fixture snapshot.

The set is YAML so that the argument in it is reviewable by someone who does not
read Python (`docs/03-agent-design.md` reason 22). This module is the part that
makes the YAML *binding*: each entry's `plan` is scripted as the model's tool call,
the answer is computed for real by `metrics`, `charts` and `insights`, and every
`expect` is asserted.

Two constraints the design doc puts on this driver, both structural here:

* **It does not call the API.** The only model is `client.ScriptedModel`, so what is
  proved is "*if* the model produces this plan, the answer is correct, the chart is
  the honest one and the refusal is the documented one". A live measurement against
  the real model is `scripts/agent_smoke.py`, key-gated.
* **It does not grade prose.** `summaries` entries assert the *verifier's* verdict,
  not the sentence's quality, and the set includes fabricated summaries that must be
  caught along with true ones that must not be.

`test_every_expectation_key_is_understood` is the one that keeps the rest honest: a
driver that silently ignores a misspelled `expect` key turns the whole file into
documentation that cannot fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from dtp.agent import guard
from dtp.agent.client import Reply, ScriptedModel, plan_reply
from dtp.agent.session import Answer, Session

QUESTIONS = Path(__file__).parent / "agent_questions.yml"

# The whole schema, in one place. Anything else in the file is a typo, and a typo in
# an expectation is worse than a missing expectation because it reads as covered.
ENTRY_KEYS = {"question", "why", "plan", "declined", "screened", "refusal",
              "expect", "summaries"}
EXPECT_KEYS = {"resolved", "chart", "rows", "figure", "caption", "note", "says",
               "tiles", "message", "suggestions"}
SUMMARY_KEYS = {"text", "verified", "why"}

ENTRIES: list[dict[str, Any]] = yaml.safe_load(
    QUESTIONS.read_text(encoding="utf-8"))["questions"]
IDS = [entry["question"] for entry in ENTRIES]

# (entry, summary) for every scripted sentence, flattened so a failing verdict names
# the sentence rather than the question it hangs off.
SUMMARIES = [(entry, said) for entry in ENTRIES
             for said in entry.get("summaries") or ()]
SUMMARY_IDS = [said["text"][:60] for _, said in SUMMARIES]


def run(entry: dict[str, Any], wh, summary: str = "") -> tuple[Answer,
                                                               ScriptedModel]:
    """Ask one entry's question of a model scripted to behave as the entry says."""
    replies: list[Reply] = []
    if entry.get("plan") is not None:
        replies = [plan_reply(**entry["plan"]), Reply(text=summary)]
    elif entry.get("declined"):
        replies = [Reply(text=entry["declined"])]
    model = ScriptedModel(replies=replies)
    return Session(wh, model).ask(entry["question"]), model


# --------------------------------------------------------------------------- #
# the file itself
# --------------------------------------------------------------------------- #

def test_the_set_is_big_enough_to_be_a_measurement():
    # `docs/00-success-metrics.md` proposes >=90% on 20+ questions; roadmap 3.2 asks
    # for 10+. Twenty is the floor because a set of ten makes every question worth
    # 10% and turns one disagreement about English into a failed metric.
    assert len(ENTRIES) >= 20, len(ENTRIES)


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_entry_states_a_question_and_why(entry):
    assert entry.get("question", "").strip()
    # `why` is the reviewable part - the reason the set is YAML at all. An entry
    # without one asserts a reading nobody can disagree with.
    assert len(entry.get("why", "").split()) >= 12, entry["question"]
    assert set(entry) <= ENTRY_KEYS, set(entry) - ENTRY_KEYS


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_entry_says_what_the_model_does(entry):
    # Exactly one of the three: a plan it produces, a sentence it declines with, or
    # nothing at all because the question never reaches it.
    given = [k for k in ("plan", "declined", "screened") if entry.get(k)]
    assert len(given) == 1, given


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_expectation_key_is_understood(entry):
    expect = entry.get("expect") or {}
    assert set(expect) <= EXPECT_KEYS, set(expect) - EXPECT_KEYS
    for said in entry.get("summaries") or ():
        assert set(said) <= SUMMARY_KEYS, set(said) - SUMMARY_KEYS
        assert isinstance(said.get("verified"), bool), said["text"]
    if entry.get("refusal"):
        assert entry["refusal"] in guard.REFUSALS, entry["refusal"]


def test_the_set_exercises_every_refusal_code():
    # Roadmap 3.1's "every out-of-scope question type has a defined fallback" is only
    # a claim if each fallback is reached by something. A new code cannot be added to
    # REFUSALS without a question that produces it.
    used = {entry["refusal"] for entry in ENTRIES if entry.get("refusal")}
    assert used == set(guard.REFUSALS), set(guard.REFUSALS) - used


# --------------------------------------------------------------------------- #
# screened before anything leaves the process
# --------------------------------------------------------------------------- #

SCREENED = [e for e in ENTRIES if e.get("screened")]


@pytest.mark.parametrize("entry", SCREENED,
                         ids=[e["question"] for e in SCREENED])
def test_a_screened_question_is_refused_without_a_model_call(entry, wh):
    answer, model = run(entry, wh)
    assert answer.refusal is not None, answer.summary
    assert answer.refusal.code == entry["refusal"], answer.refusal.message
    # The privacy property, not the cost one (design reason 8): "who is our biggest
    # customer?" must not become a request to a third party.
    assert model.calls == []
    # Screening is a property of the question alone, so it holds outside `ask()` too.
    screened = guard.scope_screen(entry["question"])
    assert screened is not None and screened.code == entry["refusal"]
    # A fallback that names nothing askable is a dead end - roadmap 3.1 asks for one
    # per out-of-scope type, and this is where "defined" is checked.
    assert answer.refusal.suggestions, entry["question"]


# --------------------------------------------------------------------------- #
# answered, and refused by the validator or the executor
# --------------------------------------------------------------------------- #

ASKED = [e for e in ENTRIES if not e.get("screened")]


@pytest.mark.parametrize("entry", ASKED, ids=[e["question"] for e in ASKED])
def test_each_question_resolves_as_the_set_says(entry, wh):
    answer, model = run(entry, wh)
    assert model.calls, "the model was never asked for a plan"
    expect = entry.get("expect") or {}
    code = entry.get("refusal")

    if code:
        assert answer.refusal is not None, answer.text()
        assert answer.refusal.code == code, answer.refusal.message
        if "message" in expect:
            assert expect["message"] in answer.refusal.message
        if "suggestions" in expect:
            assert bool(answer.refusal.suggestions) is expect["suggestions"]
        # A refusal carries no numbers, which is the point of it being a refusal.
        assert answer.frame is None and answer.figure is None
        return

    assert answer.ok, answer.refusal.message if answer.refusal else ""
    assert answer.frame is not None
    if "resolved" in expect:
        # Exact, not a subset: a field the validator added and the set does not
        # mention is exactly the kind of silent correction reason 8 is about.
        assert answer.plan.to_dict() == expect["resolved"]
    if "chart" in expect:
        assert answer.chart == expect["chart"]
    if "rows" in expect:
        assert len(answer.frame) == expect["rows"]
    if "figure" in expect:
        assert (answer.figure is not None) is expect["figure"]
    else:
        assert (answer.figure is not None) is (answer.chart not in ("kpi", "table"))
    if "caption" in expect:
        assert expect["caption"] in answer.caption
    if "note" in expect:
        assert any(expect["note"] in note for note in answer.notes), answer.notes
    if "says" in expect:
        assert expect["says"] in answer.computed
    if "tiles" in expect:
        assert [t.label for t in answer.tiles] == expect["tiles"]
    # The templated sentence exists for every shape, because it is what a dropped
    # model sentence falls back to (design reason 11).
    assert answer.computed.strip()


# --------------------------------------------------------------------------- #
# the verifier, against a model that sometimes lies
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("entry,said", SUMMARIES, ids=SUMMARY_IDS)
def test_the_verifier_reaches_the_verdict_the_set_states(entry, said, wh):
    answer, _ = run(entry, wh, summary=said["text"])
    assert answer.ok, answer.refusal.message if answer.refusal else ""
    assert answer.verified is said["verified"], answer.withheld or answer.summary

    if said["verified"]:
        assert answer.summary.split() == said["text"].split()
        assert not answer.withheld
        return
    # A mismatch drops the *whole* sentence rather than editing a figure out of it,
    # and says so: reason 11's "model summary withheld (unverifiable: 41.2%)".
    assert answer.summary == answer.computed
    assert answer.withheld.startswith("model summary withheld")


def test_an_empty_summary_falls_back_without_claiming_it_was_verified(wh):
    # The model returning nothing is not the same as the model lying, and neither is
    # a pass: `verified` is what the CLI prints "withheld" from.
    entry = next(e for e in ASKED if e["question"] == "revenue by market")
    answer, _ = run(entry, wh, summary="")
    assert answer.summary == answer.computed
    assert answer.verified is False
    assert answer.withheld == ""


