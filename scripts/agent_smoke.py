"""A live run against the real model. Key-gated, and its output is a log.

Everything in `tests/` runs against `ScriptedModel`, which is the point: the
guardrails are testable without a credential and without spend. But "does the real
model produce the plan we expected?" is a different question, and it is not one a
test suite should answer on someone else's card. So it lives here, it is skipped
without a key, and what it produces is a report.

    python scripts/agent_smoke.py                    # every question in the set
    python scripts/agent_smoke.py --limit 5          # the first five
    python scripts/agent_smoke.py --only revenue     # questions matching a substring
    python scripts/agent_smoke.py --model claude-opus-5

The question set is `tests/agent_questions.yml` - the same reviewed file the offline
driver reads, so the two measure the same thing from opposite sides. Fourteen of its
entries state a `resolved` plan, and agreement with those is the headline number.

**Exit codes.** 0 when nothing broke, 1 when something structural did (an exception,
or a plan the validator could not read), 2 when there was no key and nothing was
measured. Model *disagreement* on a plan is a 0: it is the measurement, not a build
failure. A live gate that fails the build when a model rewords a plan is a gate
somebody disables in a week.

Nothing personal can reach the log because nothing personal can reach a plan: the
columns are not dimensions. What is written is the question, the plan as JSON, the
refusal code if any, and whether the sentence survived verification.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

QUESTIONS = ROOT / "tests" / "agent_questions.yml"


class Counted:
    """The real model, wrapped so the log can say what the run cost in calls.

    A proxy rather than a field on `AnthropicModel`, because counting is this
    script's concern and the client's job is to be the thin seam it is.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.calls = 0

    def respond(self, system: str, user: str,
                tools: Sequence[dict[str, Any]] | None = None,
                max_tokens: int = 1024):
        self.calls += 1
        return self.inner.respond(system, user, tools=tools,
                                  max_tokens=max_tokens)


@dataclass
class Result:
    """One question's outcome, as the log will print it."""

    question: str
    expected: dict[str, Any] | None = None
    resolved: dict[str, Any] | None = None
    refusal: str = ""
    expected_refusal: str = ""
    summary: str = ""
    verified: bool | None = None
    withheld: str = ""
    calls: int = 0
    seconds: float = 0.0
    error: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def agrees(self) -> bool | None:
        """None when the set states no expectation, so nothing was measured."""
        if self.expected_refusal:
            return self.refusal == self.expected_refusal
        if self.expected is None:
            return None
        return self.resolved == self.expected

    @property
    def broke(self) -> bool:
        """Structural failure: an exception, or a plan nothing could read.

        A model that answers a different question than the set expected is a
        disagreement. `unparseable` is the one refusal that is not: it means the tool
        call could not be turned into a plan at all, which is the schema or the
        prompt being wrong rather than the model being creative. Unless the set
        expected it - two entries do, where the decline is the right answer.
        """
        if self.error:
            return True
        return self.refusal == "unparseable" != self.expected_refusal


def load_questions(path: Path) -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data["questions"])


def run_one(session: Any, model: Counted, entry: dict[str, Any]) -> Result:
    """Ask one question for real, and record what came back.

    Every question gets its own session: the set is a set of independent questions,
    and carrying a plan between them would make each one's result depend on the order
    the file happens to be in. The follow-up patch has its own offline tests.
    """
    result = Result(question=entry["question"],
                    expected=entry.get("expect", {}).get("resolved"),
                    expected_refusal=entry.get("refusal", ""))
    before, started = model.calls, time.monotonic()
    try:
        session.reset()
        answer = session.ask(entry["question"])
    except Exception:                                   # noqa: BLE001 - it is a log
        result.error = traceback.format_exc(limit=3).strip()
    else:
        if answer.plan is not None:
            result.resolved = answer.plan.to_dict()
        if answer.refusal is not None:
            result.refusal = answer.refusal.code
        result.summary = answer.summary
        result.verified = answer.verified if answer.ok else None
        result.withheld = answer.withheld
        result.notes = list(answer.notes)
    result.calls = model.calls - before
    result.seconds = round(time.monotonic() - started, 2)
    return result


def _verdict(r: Result) -> str:
    if r.error:
        return "ERROR"
    agrees = r.agrees
    if agrees is None:
        return "recorded"
    return "agrees" if agrees else "DIFFERS"


def report(results: list[Result], model: str, snapshot: str,
           seconds: float, calls: int) -> str:
    measured = [r for r in results if r.agrees is not None]
    agreed = [r for r in measured if r.agrees]
    broke = [r for r in results if r.broke]
    lines = [
        "# Agent smoke run",
        "",
        "| | |",
        "|---|---|",
        "| model | `" + model + "` |",
        "| snapshot | `" + snapshot + "` |",
        "| questions | " + str(len(results)) + " |",
        "| API calls | " + str(calls) + " |",
        "| wall clock | " + str(round(seconds, 1)) + "s |",
        "| agreement | " + str(len(agreed)) + " of " + str(len(measured))
        + " measured |",
        "| structural failures | " + str(len(broke)) + " |",
        "",
        "Agreement is against the `resolved` plan or `refusal` code stated in "
        "`tests/agent_questions.yml`. A disagreement is a measurement, not a "
        "failure: read the plan and decide whether the set or the prompt is the "
        "thing to change.",
        "",
        "| # | question | verdict | calls | plan or refusal |",
        "|---:|---|---|---:|---|",
    ]
    for i, r in enumerate(results, 1):
        got = r.refusal or json.dumps(r.resolved, sort_keys=True)
        lines.append("| " + str(i) + " | " + r.question.replace("|", "\\|")
                     + " | " + _verdict(r) + " | " + str(r.calls)
                     + " | `" + got.replace("|", "\\|") + "` |")
    lines += ["", "## Every question in full", ""]
    for i, r in enumerate(results, 1):
        lines += _detail(i, r)
    return "\n".join(lines) + "\n"


def _detail(i: int, r: Result) -> list[str]:
    out = ["### " + str(i) + ". " + r.question, "",
           "*" + _verdict(r) + "* - " + str(r.calls) + " call(s), "
           + str(r.seconds) + "s", ""]
    if r.error:
        return out + ["```", r.error, "```", ""]
    if r.expected is not None:
        out += ["- expected plan: `" + json.dumps(r.expected, sort_keys=True) + "`"]
    if r.resolved is not None:
        out += ["- resolved plan: `" + json.dumps(r.resolved, sort_keys=True) + "`"]
    if r.expected_refusal:
        out += ["- expected refusal: `" + r.expected_refusal + "`"]
    if r.refusal:
        out += ["- refused: `" + r.refusal + "`"]
    if r.summary:
        out += ["- summary: " + r.summary,
                "- verified: " + str(r.verified)]
    if r.withheld:
        out += ["- " + r.withheld]
    out += ["- note: " + n for n in r.notes]
    return out + [""]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", type=Path, default=QUESTIONS,
                    help="the question set to run (default: the tests' own)")
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N questions")
    ap.add_argument("--only", default="",
                    help="run only questions containing this substring")
    ap.add_argument("--model", default=None,
                    help="override DTP_AGENT_MODEL for this run")
    ap.add_argument("--versions", type=Path, default=None,
                    help="snapshot directory (default: data/versions)")
    ap.add_argument("--snapshot", default=None,
                    help="a named snapshot id (default: the newest)")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the log (default: reports/agent/)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    return _run(args)


def _run(args: argparse.Namespace) -> int:
    from dtp import REPORTS_DIR
    from dtp.agent import Session
    from dtp.agent import client as C
    from dtp.warehouse import open_warehouse

    if not C.api_key():
        print(C.KEY_ENV + " is not set, so there is nothing to measure here.")
        print("Copy .env.example to .env and put a key in it, or export the name.")
        print("Every guardrail is tested without one:  python -m pytest -q")
        return 2

    entries = load_questions(args.questions)
    if args.only:
        entries = [e for e in entries
                   if args.only.lower() in e["question"].lower()]
    if args.limit:
        entries = entries[:args.limit]
    if not entries:
        print("no questions matched")
        return 1

    model = Counted(C.AnthropicModel(model=args.model))
    started = time.monotonic()
    with open_warehouse(version_id=args.snapshot,
                        versions_dir=args.versions) as wh:
        session = Session(wh, model)
        print("model    " + model.name)
        print("snapshot " + wh.version_id)
        print("asking   " + str(len(entries)) + " question(s)\n")
        results = []
        for i, entry in enumerate(entries, 1):
            result = run_one(session, model, entry)
            results.append(result)
            print(" " + str(i).rjust(3) + ". " + _verdict(result).ljust(9)
                  + " " + result.question)
            if result.error:
                print("      " + result.error.splitlines()[-1])
        text = report(results, model.name, wh.version_id,
                      time.monotonic() - started, model.calls)

    out = args.out or (REPORTS_DIR / "agent"
                       / ("smoke-" + time.strftime("%Y%m%dT%H%M%S") + ".md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    measured = [r for r in results if r.agrees is not None]
    print("\nagreement " + str(sum(1 for r in measured if r.agrees)) + "/"
          + str(len(measured)) + "   calls " + str(model.calls))
    print("wrote " + str(out.relative_to(ROOT) if out.is_relative_to(ROOT) else out))
    broke = [r for r in results if r.broke]
    for r in broke:
        print("broke: " + r.question)
    return 1 if broke else 0


if __name__ == "__main__":
    raise SystemExit(main())

