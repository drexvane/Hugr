"""The live-run script's verdict logic, which is the part that has an opinion.

`scripts/agent_smoke.py` cannot be tested where it matters - the whole point of it is
the network call this suite refuses to make. What *can* be tested is the judgement it
encodes, and that judgement is the reason the script exists in the shape it does:

* **without a key it measures nothing and says so**, exit 2 rather than 0 or 1, so a
  CI job that runs it by accident neither passes silently nor fails loudly;
* **a model that disagrees with the reviewed plan is a measurement, not a failure**
  (exit 0). A live gate that breaks the build when a model rewords a plan is a gate
  somebody disables within a week. Only a plan nothing could read is a break;
* **the log renders every question**, including the ones the set states no
  expectation for, because a run whose output is only a percentage is a run nobody
  can act on.

Reversing any of those three should fail here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "agent_smoke.py"


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("agent_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered before executing, because `@dataclass` resolves annotations through
    # `sys.modules[cls.__module__]` and a module that is not there yet has none.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def result(smoke, **fields):
    return smoke.Result(question=fields.pop("question", "revenue by market"),
                        **fields)


# --------------------------------------------------------------------------- #
# no key: nothing measured, and a third exit code to say so
# --------------------------------------------------------------------------- #

def test_without_a_key_the_script_exits_two_and_makes_no_call():
    from dtp.agent import client

    env = {k: v for k, v in os.environ.items() if k != client.KEY_ENV}
    if client.api_key():
        # On the machine that owns the spend there is a `.env`, and the script reads
        # it whatever the environment says. Nothing to assert there, and guessing
        # would make this test fail for exactly one person.
        pytest.skip("a key is resolvable here, so the keyless path cannot be run")
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                          text=True, env=env, cwd=str(REPO))
    assert done.returncode == 2, done.stdout + done.stderr
    assert "ANTHROPIC_API_KEY is not set" in done.stdout
    # And it points at the thing that does work without one.
    assert "pytest" in done.stdout


def test_the_script_reads_the_same_question_set_as_the_offline_driver(smoke):
    entries = smoke.load_questions(REPO / "tests" / "agent_questions.yml")
    assert len(entries) >= 20
    assert all("question" in e for e in entries)
    assert smoke.QUESTIONS == REPO / "tests" / "agent_questions.yml"


# --------------------------------------------------------------------------- #
# agreement, and what counts as broken
# --------------------------------------------------------------------------- #

def test_a_plan_matching_the_set_agrees(smoke):
    plan = {"metrics": ["revenue"], "by": ["market"]}
    assert result(smoke, expected=dict(plan), resolved=dict(plan)).agrees is True


def test_a_plan_the_set_did_not_state_is_recorded_rather_than_scored(smoke):
    # Eighteen of the set's entries state no `resolved` plan, and counting those as
    # failures would make the headline number meaningless.
    r = result(smoke, resolved={"metrics": ["revenue"]})
    assert r.agrees is None
    assert not r.broke


def test_a_different_plan_differs_without_breaking_the_run(smoke):
    r = result(smoke, expected={"metrics": ["revenue"], "by": ["market"]},
               resolved={"metrics": ["revenue"], "by": ["region"]})
    assert r.agrees is False
    assert not r.broke


def test_the_expected_refusal_is_what_a_refusal_is_scored_against(smoke):
    assert result(smoke, expected_refusal="personal_data",
                  refusal="personal_data").agrees is True
    assert result(smoke, expected_refusal="personal_data",
                  refusal="unknown_dimension").agrees is False


def test_a_refusal_outranks_a_plan_when_the_set_expects_one(smoke):
    # A screened question can still carry a `resolved` expectation from the entry it
    # shares a shape with; the refusal is the claim being measured.
    r = result(smoke, expected={"metrics": ["revenue"]},
               expected_refusal="causal", refusal="causal")
    assert r.agrees is True


def test_an_unreadable_plan_is_a_break(smoke):
    assert result(smoke, refusal="unparseable").broke


def test_an_unreadable_plan_the_set_asked_for_is_not_a_break(smoke):
    # The two `declined` entries expect exactly this: a model that says it cannot
    # answer is classified `unparseable`, and there it is the right outcome.
    r = result(smoke, refusal="unparseable", expected_refusal="unparseable")
    assert not r.broke and r.agrees is True


def test_an_exception_is_a_break(smoke):
    assert result(smoke, error="Traceback ...").broke


def test_a_real_refusal_is_not_a_break(smoke):
    for code in ("personal_data", "empty_result", "unknown_metric", "causal"):
        assert not result(smoke, refusal=code).broke, code


# --------------------------------------------------------------------------- #
# the log
# --------------------------------------------------------------------------- #

@pytest.fixture
def results(smoke):
    return [
        result(smoke, question="revenue by market",
               expected={"metrics": ["revenue"], "by": ["market"]},
               resolved={"metrics": ["revenue"], "by": ["market"]},
               summary="Europe leads.", verified=True, calls=2, seconds=1.5),
        result(smoke, question="who is our biggest customer?",
               expected_refusal="personal_data", refusal="personal_data",
               calls=0, seconds=0.0),
        result(smoke, question="margin by category",
               resolved={"metrics": ["margin_pct"], "by": ["category"]},
               summary="", verified=False, withheld="model summary withheld",
               calls=2, seconds=1.1),
        result(smoke, question="revenue by salesperson",
               error="Traceback (most recent call last): boom", calls=1),
    ]


def test_the_log_states_the_model_the_snapshot_and_the_cost(smoke, results):
    text = smoke.report(results, "claude-sonnet-5", "20200101T000000", 4.2, 5)
    for part in ("claude-sonnet-5", "20200101T000000", "API calls", "| 5 |",
                 "wall clock"):
        assert part in text, part


def test_the_log_counts_agreement_over_what_was_measured_not_over_everything(
        smoke, results):
    text = smoke.report(results, "m", "s", 1.0, 5)
    # Two of the four state an expectation; both agree.
    assert "2 of 2 measured" in text
    assert "structural failures | 1" in text


def test_every_question_appears_in_the_log_with_its_plan(smoke, results):
    text = smoke.report(results, "m", "s", 1.0, 5)
    for r in results:
        assert r.question in text
    assert json.dumps({"by": ["market"], "metrics": ["revenue"]},
                      sort_keys=True) in text
    # Including the one nobody stated an expectation for.
    assert "recorded" in text
    assert "margin_pct" in text


def test_the_log_shows_what_the_verifier_withheld(smoke, results):
    text = smoke.report(results, "m", "s", 1.0, 5)
    assert "model summary withheld" in text


def test_the_log_carries_the_traceback_of_anything_that_broke(smoke, results):
    text = smoke.report(results, "m", "s", 1.0, 5)
    assert "ERROR" in text and "boom" in text


def test_a_pipe_in_a_question_cannot_break_the_table(smoke):
    r = result(smoke, question="revenue | margin", resolved={"metrics": ["revenue"]})
    text = smoke.report([r], "m", "s", 1.0, 2)
    row = [line for line in text.splitlines() if line.startswith("| 1 |")]
    assert len(row) == 1
    assert "revenue \\| margin" in row[0]
    # Six unescaped bars, so five cells: the row has the shape the header promises.
    assert len(re.findall(r"(?<!\\)\|", row[0])) == 6
