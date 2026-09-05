"""The Ask screen, driven through Streamlit's own test harness.

`test_views.py` proves the six data views are values and that the renderer does no
arithmetic. This file covers the seventh screen, which is the only interactive one
and so the only one where placement can go wrong on its own:

* **it is a seventh nav entry and not a seventh view.** `V.CATALOGUE` stays six long
  because the six are built from the sidebar's filters and this one is built from a
  question - a distinction `test_views.py` pins from the other side;
* **the controls that do not apply are absent**, not present and ignored. A date range
  the answer overrides is worse than no date range;
* **an answer renders as a view does** - tiles, a figure, the frame, the sentence -
  because `Answer` was given a view's shape for exactly this reason;
* **a refusal renders as a refusal**, with no chart and no tile beside it.

The model is the keyless stub throughout, so nothing here needs a credential.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest                          # noqa: E402

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
# The nav radio shows titles but its *value* is the view key, and `set_value` matches
# the value. Selecting by title lands on whatever the widget falls back to.
ASK = "Ask a question"
ASK_KEY = "ask"


@pytest.fixture
def app(snapshot_dir: Path, monkeypatch):
    """The app, pointed at the fixture snapshot and given the keyless stub."""
    import streamlit as st

    from dtp import versioning
    from dtp import warehouse
    from dtp.agent import client

    monkeypatch.setattr(warehouse, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(versioning, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(client, "api_key", lambda: None)
    # `_open` and `_model` are `cache_resource`, which outlives one test.
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    return at


def ask(at, question: str):
    at.text_input[0].set_value(question)
    at.button[0].click().run()
    return at


@pytest.fixture
def asking(app):
    at = app.sidebar.radio[0].set_value(ASK_KEY).run()
    assert at.sidebar.radio[0].value == ASK_KEY
    return at


# --------------------------------------------------------------------------- #
# the nav
# --------------------------------------------------------------------------- #

def test_ask_is_the_last_entry_after_the_six_views(app):
    from dtp.dashboard import views as V

    options = app.sidebar.radio[0].options
    assert options == [title for _, title, _ in V.CATALOGUE] + [ASK]


def test_the_six_views_still_open_without_a_question(app):
    assert [t.value for t in app.title][0] == "Overview"


# --------------------------------------------------------------------------- #
# the screen before a question
# --------------------------------------------------------------------------- #

def test_the_screen_offers_examples_and_an_empty_box(asking):
    assert ASK in [t.value for t in asking.title]
    assert len(asking.text_input) == 1
    assert asking.text_input[0].value in ("", None)
    assert any("Try:" in c.value for c in asking.caption)


def test_the_controls_that_do_not_apply_are_not_shown(asking):
    # The plan carries its own window, filters and thin-group floor. A date range
    # here would be a control the answer silently overrides.
    assert asking.date_input.values == []
    assert asking.multiselect.values == []
    assert asking.number_input.values == []
    assert any("sets its own window" in c.value for c in asking.caption)


def test_the_privacy_note_is_on_this_screen_too(asking):
    assert any("not shown on any view" in c.value for c in asking.caption)


def test_nothing_is_rendered_before_a_question_is_asked(asking):
    assert asking.metric.values == []
    assert asking.warning.values == []


# --------------------------------------------------------------------------- #
# an answer
# --------------------------------------------------------------------------- #

def test_an_answer_renders_a_tile_a_caption_and_a_sentence(asking):
    at = ask(asking, "revenue by market")
    assert [m.label for m in at.metric] == ["Revenue"]
    assert at.metric[0].value.startswith("$")
    assert any("Revenue by market" in c.value for c in at.caption)
    assert at.markdown and at.markdown[0].value
    assert not at.exception


def test_the_plan_is_shown_beside_the_answer(asking):
    # The one thing a user cannot otherwise check: which question was actually run.
    at = ask(asking, "revenue by market")
    assert len(at.json) == 1
    assert "revenue" in str(at.json[0].value)
    assert any("No SQL came from the model" in c.value for c in at.caption)


def test_the_model_that_filled_the_plan_in_is_named(asking):
    at = ask(asking, "revenue by market")
    assert any("keyword-stub" in c.value for c in at.caption)


def test_a_refusal_renders_as_a_warning_with_no_figure(asking):
    at = ask(asking, "who is our biggest customer?")
    assert at.warning
    assert "deliberately not available to query" in at.warning[0].value
    assert at.metric.values == []
    assert at.json.values == []
    assert not at.exception


def test_a_question_the_stub_cannot_read_says_so_rather_than_answering(asking):
    at = ask(asking, "how many returns did we get?")
    assert at.warning and "keyless stub" in at.warning[0].value
    assert at.metric.values == []


def test_an_empty_question_asks_nothing(asking):
    at = ask(asking, "   ")
    assert at.metric.values == [] and at.warning.values == []


# --------------------------------------------------------------------------- #
# the session
# --------------------------------------------------------------------------- #

def test_a_follow_up_patches_the_last_plan(asking):
    at = ask(asking, "revenue by market")
    at = ask(at, "by category")
    plans = [str(j.value) for j in at.json]
    assert any("category" in p for p in plans)
    # The metric came from the first question, which is what a patch means.
    assert all("revenue" in p for p in plans)


def test_the_earlier_answers_stay_on_the_page(asking):
    at = ask(asking, "revenue by market")
    at = ask(at, "orders by country")
    # Both plans are on screen: the newest in full, the earlier one folded away.
    assert len(at.json) == 2


def test_a_follow_up_is_offered_only_once_there_is_a_plan(asking):
    assert not any("Follow-ups patch" in c.value for c in asking.caption)
    at = ask(asking, "revenue by market")
    assert any("Follow-ups patch" in c.value for c in at.caption)


def test_start_over_clears_the_screen_and_the_memory(asking):
    at = ask(asking, "revenue by market")
    over = next(b for b in at.button if b.label == "Start over")
    over.click().run()
    assert at.metric.values == []
    assert at.json.values == []
    assert not any("Follow-ups patch" in c.value for c in at.caption)


def test_the_start_over_button_appears_only_after_an_answer(asking):
    assert [b.label for b in asking.button] == ["Ask"]
    at = ask(asking, "revenue by market")
    assert "Start over" in [b.label for b in at.button]
