"""The password gate: off by default, honest about what it is, and hard to weaken.

The gate exists to unblock Phase 4's launch task — a demo on a host should not be
open — and the tests below are mostly about the ways a gate like this goes wrong:

* **it is off unless an operator turns it on**, so a local run is untouched and no
  test in this repository has to know a password;
* **a weak secret refuses to serve rather than pretending.** A gate the operator
  believes in and that accepts `1234` is worse than no gate;
* **the comparison is constant-time**, and neither the password nor a failed attempt
  is written anywhere;
* **nothing renders before it passes** — not the charts, and not the sidebar, whose
  snapshot list and filter boxes are made of real market and product values.

What it is *not* is asserted too: `auth` imports no Streamlit, so the decision is
reachable without a browser.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest                          # noqa: E402

from dtp.dashboard import auth                                    # noqa: E402

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
SRC = Path(auth.__file__)

GOOD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def no_secret(monkeypatch):
    """Every test starts with the gate off, whatever the developer's shell holds."""
    monkeypatch.delenv(auth.PASSWORD_ENV, raising=False)
    monkeypatch.delenv(auth.DIGEST_ENV, raising=False)


@pytest.fixture
def app(snapshot_dir: Path, monkeypatch):
    import streamlit as st

    from dtp import versioning, warehouse
    from dtp.agent import client

    monkeypatch.setattr(warehouse, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(versioning, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(client, "api_key", lambda: None)
    st.cache_resource.clear()
    st.cache_data.clear()

    def _run() -> AppTest:
        at = AppTest.from_file(str(APP), default_timeout=180)
        at.run()
        return at
    return _run


# --------------------------------------------------------------------------- #
# off by default
# --------------------------------------------------------------------------- #

def test_no_environment_variable_means_no_gate():
    assert auth.required() is False
    # And nothing is accepted, so a caller that forgets to check `required` first
    # cannot be let through by an empty password.
    assert auth.verify("") is False
    assert auth.verify(GOOD) is False


def test_a_blank_variable_is_the_same_as_an_unset_one(monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, "   ")
    assert auth.required() is False


def test_the_dashboard_opens_straight_onto_a_view_without_a_secret(app):
    at = app()
    assert "Overview" in [t.value for t in at.title]
    assert at.text_input.values == []            # no password box


# --------------------------------------------------------------------------- #
# a configured secret
# --------------------------------------------------------------------------- #

def test_a_password_is_required_and_checked(monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, GOOD)
    assert auth.required() and auth.weakness() == ""
    assert auth.verify(GOOD)
    for wrong in (GOOD.upper(), GOOD + " ", GOOD[:-1], "", "hunter2"):
        assert not auth.verify(wrong), wrong


def test_a_digest_can_be_configured_instead_of_the_plaintext(monkeypatch):
    monkeypatch.setenv(auth.DIGEST_ENV,
                       hashlib.sha256(GOOD.encode()).hexdigest())
    assert auth.required() and auth.weakness() == ""
    assert auth.verify(GOOD) and not auth.verify("hunter2")


def test_the_digest_is_read_case_insensitively(monkeypatch):
    monkeypatch.setenv(auth.DIGEST_ENV,
                       hashlib.sha256(GOOD.encode()).hexdigest().upper())
    assert auth.verify(GOOD)


def test_the_digest_wins_when_both_are_set(monkeypatch):
    # Otherwise a leftover plaintext variable silently outranks the digest the
    # operator moved to.
    monkeypatch.setenv(auth.PASSWORD_ENV, "a-different-long-password")
    monkeypatch.setenv(auth.DIGEST_ENV, hashlib.sha256(GOOD.encode()).hexdigest())
    assert auth.verify(GOOD)
    assert not auth.verify("a-different-long-password")


# --------------------------------------------------------------------------- #
# a weak secret is a configuration error, not a smaller amount of security
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("weak", ["1234", "password", "letmein", "short"])
def test_a_short_password_refuses_to_serve(weak, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, weak)
    assert auth.required()
    assert auth.MIN_LENGTH >= 12
    assert auth.PASSWORD_ENV in auth.weakness()
    # And it does not quietly work: a misconfigured gate admits nobody.
    assert not auth.verify(weak)


def test_a_digest_that_is_not_a_digest_refuses_to_serve(monkeypatch):
    monkeypatch.setenv(auth.DIGEST_ENV, "not-a-hash")
    assert "sha256" in auth.weakness()
    assert not auth.verify("not-a-hash")


def test_the_weakness_message_says_how_to_fix_it(monkeypatch):
    monkeypatch.setenv(auth.DIGEST_ENV, "abc")
    assert "hashlib.sha256" in auth.weakness()
    monkeypatch.delenv(auth.DIGEST_ENV)
    monkeypatch.setenv(auth.PASSWORD_ENV, "tiny")
    assert "unset it" in auth.weakness()


def test_the_gate_stops_the_page_when_the_secret_is_misconfigured(app,
                                                                 monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, "1234")
    at = app()
    assert at.error and "misconfigured" in at.error[0].value
    # Nothing else: no view, no sidebar controls, no password box either.
    assert at.text_input.values == []
    assert "Overview" not in [t.value for t in at.title]


# --------------------------------------------------------------------------- #
# through the page
# --------------------------------------------------------------------------- #

def test_the_gate_hides_the_sidebar_as_well_as_the_charts(app, monkeypatch):
    # The snapshot picker and the filter boxes are made of real market, segment and
    # product values, so rendering them and hiding only the charts still answers a
    # question for whoever found the URL.
    monkeypatch.setenv(auth.PASSWORD_ENV, GOOD)
    at = app()
    assert len(at.text_input) == 1                # the password box, and nothing else
    assert at.sidebar.radio.values == []
    assert at.multiselect.values == []
    assert at.metric.values == []


def test_the_right_password_opens_the_dashboard(app, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, GOOD)
    at = app()
    at.text_input[0].set_value(GOOD)
    at.button[0].click().run()
    assert "Overview" in [t.value for t in at.title]
    assert at.metric.values                      # a real KPI row


def test_the_wrong_password_says_nothing_useful(app, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, GOOD)
    at = app()
    at.text_input[0].set_value("hunter2")
    at.button[0].click().run()
    assert at.error and at.error[0].value == "Not that."
    assert "Overview" not in [t.value for t in at.title]


def test_the_password_is_asked_once_not_on_every_rerun(app, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, GOOD)
    at = app()
    at.text_input[0].set_value(GOOD)
    at.button[0].click().run()
    # The radio's options are the titles but its *value* is the view key, and
    # `set_value` matches the value. Passing a title silently lands somewhere else.
    at.sidebar.radio[0].set_value("delivery").run()
    assert at.sidebar.radio[0].value == "delivery"
    assert "Delivery" in [t.value for t in at.title]


def test_attempts_are_capped_within_a_session(app, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, GOOD)
    at = app()
    for _ in range(auth.MAX_ATTEMPTS + 1):
        at.text_input[0].set_value("hunter2")
        at.button[0].click().run()
    assert any("Too many attempts" in e.value for e in at.error)
    # And the right password no longer helps in this session.
    at.text_input[0].set_value(GOOD)
    at.button[0].click().run()
    assert "Overview" not in [t.value for t in at.title]


# --------------------------------------------------------------------------- #
# what the module is not
# --------------------------------------------------------------------------- #

def test_the_gate_knows_nothing_about_streamlit():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "hashlib", "hmac", "os"}, imported


def test_the_comparison_is_constant_time():
    """`==` on strings returns at the first differing character.

    How long a comparison took is a description of the secret, so the source is
    checked rather than the timing - a timing assertion is flaky and this is not.
    """
    text = SRC.read_text(encoding="utf-8")
    assert "compare_digest" in text
    assert "== _env(" not in text and "== os.environ" not in text


def test_nothing_is_written_and_no_attempt_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV, GOOD)
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    auth.verify("hunter2")
    auth.verify(GOOD)
    assert set(tmp_path.rglob("*")) == before
    # A log of failed passwords is a log of passwords. Checked on the parsed tree
    # rather than the text, because the docstring quotes a `print(...)` one-liner for
    # generating a digest and a substring scan cannot tell the two apart.
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not called & {"print", "open"}, called


def test_the_module_says_plainly_that_it_is_not_access_control():
    # The docstring is the thing an operator reads before hosting this. If the
    # sentence goes, so does the only warning that a password is not identity.
    assert "not access control" in auth.__doc__
    assert "client IPs" in auth.__doc__
