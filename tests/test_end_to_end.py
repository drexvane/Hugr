"""The critical-path journeys, chained. Phase 4's end-to-end QA, as a gate.

Every other test module proves one layer. This one proves the seams hold in the order
a real user meets them, because that is where the failures nobody predicted live: a
snapshot the warehouse cannot open, a view whose frame the chart layer refuses, an
answer whose plan the registry no longer has a metric for.

Four journeys:

* **publish** - raw files through the pipeline to a snapshot, and the gate that
  refuses to publish one when validation fails;
* **read** - that snapshot through the warehouse to all six views;
* **ask** - the same snapshot through the agent to an answer, a follow-up and a
  refusal;
* **fresh clone** - no key, no `anthropic` install, no real data: the whole thing
  still runs and says what it cannot do.

The seam between the first journey and the rest is a published snapshot, and the two
halves use different fixtures because the real cleaning config is shaped for the
vendor's 53 columns. `scripts/qa_report.py` runs the same journeys against the real
extract, where they meet as one chain; that cannot live here because `dataset/` is
gitignored and CI has no copy of it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dtp import metrics as M
from dtp import pipeline as pipe
from dtp import versioning as version_mod
from dtp import warehouse
from dtp.agent import Session
from dtp.agent.client import KeywordModel
from dtp.dashboard import views as V

# The columns no screen and no plan may name, whatever else changes.
PERSONAL = ("customer_first_name", "customer_last_name", "customer_street",
            "client_ip", "customer_email", "customer_password")

FAILING_RULE = """
version: 1
tables:
  mini:
    rules:
      - id: no_cancelled_money
        type: expression
        expr: "is_revenue or line_total == 0"
        severity: error
        reason: Deliberately unsatisfiable, to exercise the snapshot gate.
"""


# --------------------------------------------------------------------------- #
# journey 1: publish
# --------------------------------------------------------------------------- #

@pytest.fixture
def published(mini_raw, mini_config, mini_rules, tmp_path):
    """One pipeline run, all of it inside tmp_path."""
    return pipe.run(raw_dir=mini_raw, config_path=mini_config,
                    rules_path=mini_rules,
                    clean_dir=tmp_path / "clean",
                    versions_dir=tmp_path / "versions",
                    reports_dir=tmp_path / "reports",
                    docs_dir=tmp_path / "docs",
                    thresholds_path=tmp_path / "none.yml")


def test_raw_files_become_a_readable_snapshot_in_one_command(published, tmp_path):
    assert published.ok, published.verdict()
    versions = version_mod.list_versions(tmp_path / "versions")
    assert len(versions) == 1
    latest = versions[0]
    # The journey's output is not a report, it is a snapshot something can open.
    with warehouse.open_warehouse(versions_dir=tmp_path / "versions") as wh:
        assert wh.version_id == latest.version_id
        assert "mini" in wh.tables
        assert len(wh.sql("SELECT * FROM mini")) > 0


def test_the_documentation_and_the_reports_are_written_by_the_same_run(
        published, tmp_path):
    # A snapshot nobody can look up is a snapshot nobody will trust six months on.
    assert (tmp_path / "docs" / "data-dictionary.md").exists()
    # `out_dir` is not optional here on purpose: `write_report` defaults to the
    # repository's own `reports/`, and a test that let it default would replace the
    # real pipeline report with seven rows of fixture data.
    paths = pipe.write_report(published, out_dir=tmp_path / "reports")
    assert paths["markdown"].exists() and paths["json"].exists()
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["ok"] is True


def test_a_failed_validation_publishes_nothing_and_the_run_says_so(
        mini_raw, mini_config, tmp_path):
    rules = tmp_path / "failing.yml"
    rules.write_text(FAILING_RULE, encoding="utf-8")
    result = pipe.run(raw_dir=mini_raw, config_path=mini_config, rules_path=rules,
                      clean_dir=tmp_path / "clean",
                      versions_dir=tmp_path / "versions",
                      reports_dir=tmp_path / "reports",
                      docs_dir=tmp_path / "docs",
                      thresholds_path=tmp_path / "none.yml")
    assert not result.ok
    assert version_mod.list_versions(tmp_path / "versions") == []
    # And nothing downstream can be opened, rather than opening something stale.
    with pytest.raises(FileNotFoundError):
        warehouse.open_warehouse(versions_dir=tmp_path / "versions")


# --------------------------------------------------------------------------- #
# journey 2: read
# --------------------------------------------------------------------------- #

def test_every_view_builds_off_one_snapshot_with_something_on_it(wh):
    for key, title, question in V.CATALOGUE:
        view = V.build(key, **({} if key == "health" else {"wh": wh}))
        assert view.title == title and view.question == question
        assert view.tiles or view.panels, key
        for panel in view.panels:
            assert panel.figure is not None or panel.table is not None \
                or panel.caption, (key, panel.key)


def test_a_filter_and_a_drill_down_change_the_numbers_rather_than_erroring(wh):
    whole = V.geography(wh)
    narrowed = V.geography(wh, filters=V.drill_into(M.Filters(), "market",
                                                   ["Europe"]),
                           level="region")
    places = next(p for p in narrowed.panels if p.key == "places")
    assert places.table is not None or places.figure is not None
    assert narrowed.tiles and whole.tiles
    assert [t.value for t in narrowed.tiles] != [t.value for t in whole.tiles]


def test_no_view_and_no_answer_names_a_personal_column(wh):
    seen: set[str] = set()
    for key, _, _ in V.CATALOGUE:
        view = V.build(key, **({} if key == "health" else {"wh": wh}))
        for panel in view.panels:
            if panel.table is not None:
                seen |= {str(c).lower() for c in panel.table.columns}
    answer = Session(wh, KeywordModel()).ask("revenue by market")
    seen |= {str(c).lower() for c in answer.frame.columns}
    assert not seen & set(PERSONAL), seen & set(PERSONAL)


# --------------------------------------------------------------------------- #
# journey 3: ask
# --------------------------------------------------------------------------- #

def test_a_question_a_follow_up_and_a_refusal_in_one_session(wh):
    session = Session(wh, KeywordModel())

    first = session.ask("revenue by market")
    assert first.ok and first.tiles and first.summary
    assert first.figure is not None

    second = session.ask("by category")
    assert second.ok and second.plan.by == ["category"]
    assert second.plan.metrics == first.plan.metrics

    refused = session.ask("who is our biggest customer?")
    assert refused.refusal.code == "personal_data"
    assert refused.refusal.suggestions
    # The refusal did not cost the session its memory.
    assert session.plan.to_dict() == second.plan.to_dict()
    assert len(session.log) == 3


def test_the_number_on_the_screen_and_the_number_in_the_sentence_are_one_number(wh):
    # The whole argument for the plan-not-SQL design, end to end: the tile comes from
    # the registry's own formatter over the registry's own query, and the sentence is
    # derived from the frame beside it. Nothing here was written by a model.
    answer = Session(wh, KeywordModel()).ask("revenue by market")
    total = M.fmt_metric("revenue", M.totals(wh, ["revenue"])["revenue"],
                         compact=True)
    assert answer.tiles[0].value == total
    assert answer.verified is False and answer.summary == answer.computed


# --------------------------------------------------------------------------- #
# journey 4: a fresh clone
#
# No key, no `anthropic` install, no `dataset/`. Everything here is what someone who
# has just cloned the repository can actually do, which is the difference between a
# platform and a screenshot.
# --------------------------------------------------------------------------- #

def test_the_agent_package_imports_without_touching_the_sdk():
    """In a fresh interpreter, so nothing this suite already imported can mask it.

    `client.AnthropicModel` imports `anthropic` inside `__init__`, which is what lets
    `import dtp.agent` work in an environment that has never installed it. Asserting
    on `sys.modules` after the import is the only way to see that from outside.
    """
    root = Path(__file__).resolve().parents[1]
    code = ("import sys; sys.path.insert(0, r'" + str(root / "src") + "');"
            "import dtp.agent;"
            "assert 'anthropic' not in sys.modules, 'imported eagerly';"
            "print(dtp.agent.KeywordModel().name)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == "keyword-stub"


def test_asking_for_the_real_model_without_the_sdk_says_what_to_install(monkeypatch):
    import builtins

    from dtp.agent import client

    real_import = builtins.__import__

    def no_anthropic(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no anthropic here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    monkeypatch.setattr(client, "api_key", lambda: "not-a-real-key")
    with pytest.raises(RuntimeError) as err:
        client.AnthropicModel()
    assert "pip install" in str(err.value) and "--stub" in str(err.value)


def test_without_a_key_the_error_names_the_file_that_would_hold_one(monkeypatch):
    from dtp.agent import client

    monkeypatch.setattr(client, "api_key", lambda: None)
    with pytest.raises(RuntimeError) as err:
        client.AnthropicModel()
    assert ".env.example" in str(err.value)
    assert "nothing in this repository holds a key" in str(err.value)


def test_the_committed_example_env_holds_a_name_and_no_value():
    root = Path(__file__).resolve().parents[1]
    for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("ANTHROPIC_API_KEY"):
            assert line.strip() == "ANTHROPIC_API_KEY="
            break
    else:
        pytest.fail("the example env should name the key it wants")


def test_a_whole_question_is_answered_with_the_network_taken_away(wh):
    """The claim every phase of this project makes, in the one place it can be tested.

    Not a mock of the model - the model here is keyless anyway. `socket.socket` is
    replaced for the length of the call, so the screen, the validator, DuckDB reading
    local Parquet, the chart and the verifier all have to complete without one. If
    any layer ever reaches for a network, this is what fails.
    """
    import socket

    real = socket.socket

    class Refused(socket.socket):
        def __init__(self, *args, **kwargs):
            raise AssertionError("something opened a socket")

    socket.socket = Refused                     # type: ignore[misc]
    try:
        answer = Session(wh, KeywordModel()).ask("revenue and margin by category")
    finally:
        socket.socket = real                    # type: ignore[misc]
    assert answer.ok and answer.figure is not None and answer.summary
