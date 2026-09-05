"""`dtp ask`: the command, its exit codes, and the session behind `--repl`.

Three things worth pinning, all of them about a first run on a fresh clone:

* **it works without a key.** No credential means the keyless stub and a line saying
  so, not a stack trace. A demo that cannot be run is not a demo;
* **the exit code is the contract**, as it is for every other command here: 0 when the
  question was answered, 1 when it was refused. A shell loop over questions can tell
  the difference;
* **`--repl` is a session**, so a follow-up patches the last plan, `:plan` prints it,
  and nothing is written down.

The model is always the stub, so nothing here needs a key either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dtp import cli


def run(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


@pytest.fixture
def base(snapshot_dir: Path) -> list[str]:
    return ["ask", "--stub", "--versions", str(snapshot_dir)]


# --------------------------------------------------------------------------- #
# one question
# --------------------------------------------------------------------------- #

def test_an_answered_question_prints_the_caption_the_tiles_and_a_sentence(
        capsys, base):
    code, out = run(capsys, *base, "revenue", "by", "market")
    assert code == 0
    assert "Revenue by market" in out
    assert "Revenue:" in out
    assert "keyword-stub" in out and "20200101T000000" in out


def test_the_question_does_not_need_quoting(capsys, base):
    # `nargs="*"`, because a shell user types the question and a quoted string is a
    # thing people forget.
    code, out = run(capsys, *base, "revenue", "and", "margin", "by", "category")
    assert code == 0 and "by category" in out


def test_a_refused_question_exits_one_and_says_why(capsys, base):
    code, out = run(capsys, *base, "who is our biggest customer?")
    assert code == 1
    assert "deliberately not available to query" in out
    # And it names something askable instead.
    assert "Try:" in out


def test_a_question_the_stub_cannot_read_exits_one(capsys, base):
    code, out = run(capsys, *base, "how many returns did we get?")
    assert code == 1 and "keyless stub" in out


def test_a_named_snapshot_can_be_asked_instead_of_the_newest(capsys, base):
    code, out = run(capsys, *base, "--snapshot", "20200101T000000",
                    "revenue by market")
    assert code == 0 and "20200101T000000" in out


def test_an_unknown_snapshot_is_an_error_not_an_empty_answer(base):
    with pytest.raises(FileNotFoundError):
        cli.main(base + ["--snapshot", "19990101T000000", "revenue by market"])


def test_without_a_key_the_command_falls_back_to_the_stub_and_says_so(
        capsys, snapshot_dir, monkeypatch):
    # No `--stub` here: this is what a fresh clone does. Failing on a missing key
    # would make the one command that shows Phase 3 off the only one that needs
    # setting up.
    from dtp.agent import client as agent_client

    monkeypatch.setattr(agent_client, "api_key", lambda: None)
    code, out = run(capsys, "ask", "--versions", str(snapshot_dir),
                    "revenue by market")
    assert code == 0
    assert "no ANTHROPIC_API_KEY found" in out
    assert ".env.example" in out
    assert "keyword-stub" in out


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #

def test_ask_is_wired_into_the_parser_with_its_own_flags():
    parsed = cli.build_parser().parse_args(["ask", "--repl", "--stub", "hello"])
    assert parsed.func is cli.cmd_ask
    assert parsed.repl and parsed.stub and parsed.question == ["hello"]


def test_the_help_names_ask_so_it_can_be_found():
    assert "ask" in cli.__doc__ and "--repl" in cli.__doc__


# --------------------------------------------------------------------------- #
# --repl
# --------------------------------------------------------------------------- #

def feed(monkeypatch, *lines: str) -> None:
    """Answer `input()` from a script, then end the session as Ctrl-D would."""
    queue = list(lines)

    def fake_input(prompt: str = "") -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)


def test_a_follow_up_in_the_repl_patches_the_last_plan(capsys, base, monkeypatch):
    feed(monkeypatch, "revenue by market", "by category", ":plan")
    code, out = run(capsys, *base, "--repl")
    assert code == 0
    assert "by market" in out and "by category" in out
    # `:plan` prints the memory, which is the patched plan rather than the first one.
    assert "'by': ['category']" in out
    assert "asked 2 question(s)" in out


def test_new_forgets_the_plan_so_the_next_question_starts_fresh(
        capsys, base, monkeypatch):
    feed(monkeypatch, "revenue by market", ":new", ":plan")
    code, out = run(capsys, *base, "--repl")
    assert code == 0 and "(no plan yet)" in out


def test_the_repl_offers_help_and_leaves_on_quit(capsys, base, monkeypatch):
    feed(monkeypatch, ":help", ":quit", "revenue by market")
    code, out = run(capsys, *base, "--repl")
    assert code == 0
    assert ":plan" in out and ":quit" in out
    # The question after `:quit` was never asked.
    assert "asked 0 question(s)" in out


def test_a_blank_line_is_not_a_question(capsys, base, monkeypatch):
    feed(monkeypatch, "", "   ", "revenue by market")
    code, out = run(capsys, *base, "--repl")
    assert "asked 1 question(s)" in out


def test_ask_with_nothing_to_ask_opens_the_session(capsys, base, monkeypatch):
    # `dtp ask` alone is an invitation, not an empty question.
    feed(monkeypatch)
    code, out = run(capsys, *base)
    assert code == 0
    assert ":quit" in out and "asked 0 question(s)" in out


def test_the_repl_says_that_nothing_was_saved(capsys, base, monkeypatch):
    # Reason 15: sessions are one process, and the line exists so a user does not
    # assume otherwise.
    feed(monkeypatch, "revenue by market")
    code, out = run(capsys, *base, "--repl")
    assert "nothing was saved" in out


def test_the_repl_writes_no_file(capsys, base, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    feed(monkeypatch, "revenue by market", "by category")
    run(capsys, *base, "--repl")
    assert set(tmp_path.rglob("*")) == before
