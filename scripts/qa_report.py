"""The critical-path journeys against the real extract, timed. Phase 4's QA log.

`tests/test_end_to_end.py` asserts these journeys against a seven-row fixture, which
is what CI can run. This runs them against whatever snapshot `data/versions/` holds -
647,247 rows of it in this project - and writes a log, because three of the things
worth checking cannot be checked on a fixture:

* **the timings**, which only mean something at real row counts;
* **the privacy boundary**, because the fixture has no personal columns at all: the
  real clean data carries customer names, streets and 3,340 client IPs, and "no view
  surfaces one" is a claim about *that* data;
* **the agent's reading of the reviewed question set**, which is a percentage and
  needs the real dimension values behind the filters.

    python scripts/qa_report.py                    # everything, against the newest
    python scripts/qa_report.py --snapshot 20260903T012120
    python scripts/qa_report.py --out reports/qa-report.md

Exit 0 when every journey passed, 1 otherwise. The model is the keyless matcher
unless `--model` is given and a key is present: a live run is `agent_smoke.py`, and
its cost belongs to whoever owns the spend.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

QUESTIONS = ROOT / "tests" / "agent_questions.yml"

# `docs/00-success-metrics.md`, so a slip shows up as a number beside its target.
TARGETS = {"render": 2000.0, "interact": 500.0, "ask": 8000.0}

PERSONAL = ("customer_first_name", "customer_last_name", "customer_street",
            "client_ip", "customer_email", "customer_password",
            "product_image_url")


@dataclass
class Step:
    """One checked thing: what it was, whether it held, and how long it took."""

    name: str
    ok: bool = True
    detail: str = ""
    ms: float = 0.0
    target: float | None = None
    error: str = ""

    @property
    def verdict(self) -> str:
        if self.error:
            return "ERROR"
        if not self.ok:
            return "FAIL"
        if self.target is not None and self.ms > self.target:
            return "slow"
        return "pass"

    @property
    def failed(self) -> bool:
        return bool(self.error) or not self.ok


@dataclass
class Journey:
    title: str
    why: str
    steps: list[Step] = field(default_factory=list)

    def run(self, name: str, work: Callable[[], tuple[bool, str]],
            target: float | None = None) -> Step:
        step = Step(name=name, target=target)
        started = time.perf_counter()
        try:
            step.ok, step.detail = work()
        except Exception:                               # noqa: BLE001 - it is a log
            step.error = traceback.format_exc(limit=4).strip()
            step.ok = False
        step.ms = (time.perf_counter() - started) * 1000
        self.steps.append(step)
        print(("  " + step.verdict.ljust(6) + name).ljust(64)
              + format(step.ms, ".0f") + " ms"
              + (("  " + step.detail) if step.detail else ""))
        if step.error:
            print("         " + step.error.splitlines()[-1])
        return step

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if s.failed]


# --------------------------------------------------------------------------- #
# journey 1: the snapshot a stakeholder would open
# --------------------------------------------------------------------------- #

def journey_snapshot(wh) -> Journey:
    from dtp import metrics as M
    from dtp import warehouse

    j = Journey("Open the published snapshot",
                "Everything downstream reads one snapshot. If it cannot be opened, "
                "or its window is not what the manifest says, nothing below means "
                "anything.")

    def tables() -> tuple[bool, str]:
        names = list(wh.tables)
        return ("order_items" in names and "access_logs" in names,
                ", ".join(names))

    def rows() -> tuple[bool, str]:
        n = wh.sql("SELECT count(*) AS n FROM order_items").iat[0, 0]
        logs = wh.sql("SELECT count(*) AS n FROM access_logs").iat[0, 0]
        return int(n) > 0, f"{int(n):,} order lines, {int(logs):,} log rows"

    def window() -> tuple[bool, str]:
        lo, hi = M.date_bounds(wh)
        return lo < hi, lo.strftime("%Y-%m-%d") + " to " + hi.strftime("%Y-%m-%d")

    def registry() -> tuple[bool, str]:
        # Every registry key against the real columns, which is the check a fixture
        # with five categories cannot make: a renamed clean column shows up here.
        # `views` and `view_to_order_pct` carry `aggregatable = False` - they come
        # from the funnel's own query - so the check for those two is that asking for
        # them the ordinary way *refuses* rather than returning a plausible number.
        totals = grouped = refused = 0
        for key, met in M.METRICS.items():
            if getattr(met, "aggregatable", True):
                M.totals(wh, [key])
                totals += 1
                continue
            try:
                M.totals(wh, [key])
            except KeyError:
                refused += 1
        for key in M.DIMENSIONS:
            M.aggregate(wh, ["lines"], by=[key], limit=1)
            grouped += 1
        return (totals + refused == len(M.METRICS)
                and grouped == len(M.DIMENSIONS) and refused == 2), \
            (str(totals) + " metrics total, " + str(grouped) + " dimensions group, "
             + str(refused) + " funnel metrics refuse as documented")

    def snapshots() -> tuple[bool, str]:
        ids = [m.version_id for m in warehouse.available_versions()]
        return ids == sorted(ids, reverse=True), str(len(ids)) + " on disk, newest first"

    j.run("both tables are registered", tables)
    j.run("the fact table and the log carry rows", rows)
    j.run("the order window is readable", window)
    j.run("every registry key resolves against the real columns", registry)
    j.run("the snapshot list is newest-first", snapshots)
    return j


# --------------------------------------------------------------------------- #
# journey 2: the analyst reading the dashboard
# --------------------------------------------------------------------------- #

def journey_dashboard(wh) -> Journey:
    from dtp import metrics as M
    from dtp.dashboard import views as V

    j = Journey("Read every view",
                "The six views are the product for anyone who does not type "
                "questions. Each is timed against the first-render target, and the "
                "filter and drill-down against the interaction target.")

    for key, title, _ in V.CATALOGUE:
        def build(key=key) -> tuple[bool, str]:
            view = V.build(key, **({} if key == "health" else {"wh": wh}))
            drawn = sum(1 for p in view.panels
                        if p.figure is not None or p.table is not None)
            return bool(view.tiles or view.panels), \
                str(len(view.tiles)) + " tiles, " + str(drawn) + "/" \
                + str(len(view.panels)) + " panels drawn"
        j.run("build " + title, build, target=TARGETS["render"])

    def filtered() -> tuple[bool, str]:
        where = {"market": ["Europe"]}
        view = V.overview(wh, filters=M.Filters(where=where))
        return bool(view.tiles), "overview filtered to one market"

    def drilled() -> tuple[bool, str]:
        view = V.geography(wh, filters=V.drill_into(M.Filters(), "market",
                                                    ["Europe"]),
                           level="region")
        return bool(view.panels), "geography at region level"

    j.run("apply a filter", filtered, target=TARGETS["interact"])
    j.run("drill market to region", drilled, target=TARGETS["interact"])
    return j


def journey_privacy(wh) -> Journey:
    from dtp import metrics as M
    from dtp.agent import Session
    from dtp.agent.client import KeywordModel
    from dtp.dashboard import views as V

    j = Journey("The privacy boundary, on the real data",
                "The fixture has no personal columns, so the test suite cannot "
                "prove this where it matters. This snapshot carries customer names, "
                "streets and client IPs. Nothing a user can reach may show one.")

    def in_the_data() -> tuple[bool, str]:
        columns = {c.lower() for c in wh.sql(
            "SELECT * FROM order_items LIMIT 0").columns}
        present = sorted(set(PERSONAL) & columns)
        # The check is only meaningful if the columns are actually there.
        return bool(present), ", ".join(present) + " present in the clean table"

    def not_on_a_view() -> tuple[bool, str]:
        seen: set[str] = set()
        for key, _, _ in V.CATALOGUE:
            view = V.build(key, **({} if key == "health" else {"wh": wh}))
            for panel in view.panels:
                if panel.table is not None:
                    seen |= {str(c).lower() for c in panel.table.columns}
        return not seen & set(PERSONAL), str(len(seen)) + " columns across all panels"

    def not_a_dimension() -> tuple[bool, str]:
        expressions = {d.expr.lower() for d in M.DIMENSIONS.values()}
        return not expressions & set(PERSONAL), \
            str(len(M.DIMENSIONS)) + " dimension expressions checked"

    def not_in_an_answer() -> tuple[bool, str]:
        session = Session(wh, KeywordModel())
        answer = session.ask("who is our biggest customer?")
        refused = answer.refusal is not None \
            and answer.refusal.code == "personal_data"
        return refused, "refused as " + (answer.refusal.code if answer.refusal
                                         else "answered, which is the failure")

    j.run("the columns are in the clean data", in_the_data)
    j.run("no view surfaces one", not_on_a_view)
    j.run("none is a registry dimension", not_a_dimension)
    j.run("a naming question is refused", not_in_an_answer)
    return j


# --------------------------------------------------------------------------- #
# journey 3: the agent, over the reviewed question set
# --------------------------------------------------------------------------- #

def load_questions() -> list[dict[str, Any]]:
    import yaml

    return list(yaml.safe_load(
        QUESTIONS.read_text(encoding="utf-8"))["questions"])


def _outcome(entry: dict[str, Any], answer) -> bool | None:
    """Did the answer match what the set states? `None` when it states nothing."""
    wanted = entry.get("refusal")
    if wanted:
        return answer.refusal is not None and answer.refusal.code == wanted
    resolved = entry.get("expect", {}).get("resolved")
    if resolved is None:
        return None
    return answer.plan is not None and answer.plan.to_dict() == resolved


def journey_agent(wh, model) -> tuple[Journey, dict[str, Any]]:
    from dtp.agent import Session
    from dtp.agent import guard

    j = Journey("Ask the reviewed question set",
                "Every question in `tests/agent_questions.yml`, asked against this "
                "snapshot. Agreement is measured only where the set states an "
                "outcome; the rest are asked to prove they do not raise.")
    entries = load_questions()
    agreed = measured = 0
    times: list[float] = []
    disagreed: list[tuple[str, str]] = []
    codes: set[str] = set()

    for entry in entries:
        question = entry["question"]

        def ask(question=question, entry=entry) -> tuple[bool, str]:
            nonlocal agreed, measured
            session = Session(wh, model)
            answer = session.ask(question)
            if answer.refusal is not None:
                codes.add(answer.refusal.code)
            verdict = _outcome(entry, answer)
            got = (answer.refusal.code if answer.refusal is not None
                   else str(answer.plan.to_dict() if answer.plan else {}))
            if verdict is not None:
                measured += 1
                if verdict:
                    agreed += 1
                else:
                    disagreed.append((question, got))
            # A question that raises is a failure; one that is read differently from
            # the set is a measurement, reported below rather than failed here.
            return True, got[:70]

        step = j.run(question[:52], ask, target=TARGETS["ask"])
        times.append(step.ms)

    def fallbacks() -> tuple[bool, str]:
        missing = [c for c in guard._SCOPE_SUGGESTIONS
                   if not guard._scope_suggestions(c)]
        return not missing, str(len(guard.REFUSALS)) + " codes defined, " \
            + str(len(codes)) + " raised by this run"

    j.run("every screened refusal offers a fallback", fallbacks)
    stats = {"agreed": agreed, "measured": measured, "asked": len(entries),
             "disagreed": disagreed, "codes": sorted(codes),
             "median_ms": statistics.median(times) if times else 0.0,
             "max_ms": max(times) if times else 0.0}
    return j, stats


def journey_session(wh, model) -> Journey:
    from dtp.agent import Session

    j = Journey("Hold a conversation",
                "Roadmap 3.3's own test, against real data: a follow-up that names "
                "one field keeps the rest, a refusal does not cost the memory, and "
                "pointing at another snapshot clears it.")
    session = Session(wh, model)

    def first() -> tuple[bool, str]:
        answer = session.ask("revenue by market")
        return answer.ok and bool(answer.tiles), answer.caption

    def follow_up() -> tuple[bool, str]:
        answer = session.ask("by category")
        return (answer.ok and answer.plan.by == ["category"]
                and answer.plan.metrics == ["revenue"]), answer.caption

    def after_a_refusal() -> tuple[bool, str]:
        before = session.plan.to_dict()
        answer = session.ask("why did revenue fall in October?")
        return (answer.refusal is not None
                and session.plan.to_dict() == before), \
            "refused as " + (answer.refusal.code if answer.refusal else "answered")

    def nothing_saved() -> tuple[bool, str]:
        session.reset()
        return session.plan is None, "plan cleared on reset"

    j.run("ask", first, target=TARGETS["ask"])
    j.run("follow up with two words", follow_up, target=TARGETS["ask"])
    j.run("a refusal keeps the working plan", after_a_refusal)
    j.run("reset forgets it", nothing_saved)
    return j


# --------------------------------------------------------------------------- #
# journey 5: more than one reader at once
# --------------------------------------------------------------------------- #

def journey_concurrency(wh, readers: int = 8, each: int = 3) -> Journey:
    from concurrent.futures import ThreadPoolExecutor

    from dtp.agent import Session
    from dtp.agent.client import KeywordModel
    from dtp.dashboard import views as V

    j = Journey("Serve more than one reader at once",
                "Roadmap 4 wants realistic concurrent usage. One Streamlit process "
                "holds one cached DuckDB connection, so this measures what happens "
                "when " + str(readers) + " sessions share it: the interesting number "
                "is not the median but how much worse it gets than one reader.")

    def timed(work: Callable[[], Any]) -> float:
        started = time.perf_counter()
        work()
        return (time.perf_counter() - started) * 1000

    def alone(work: Callable[[], Any], runs: int = 3) -> float:
        return statistics.median(timed(work) for _ in range(runs))

    def together(work: Callable[[], Any]) -> list[float]:
        with ThreadPoolExecutor(max_workers=readers) as pool:
            futures = [pool.submit(timed, work)
                       for _ in range(readers * each)]
            return sorted(f.result() for f in futures)

    def _view() -> Any:
        return V.overview(wh)

    def _ask() -> Any:
        return Session(wh, KeywordModel()).ask("revenue by market")

    def views_at_once() -> tuple[bool, str]:
        one = alone(_view)
        many = together(_view)
        p95 = many[int(len(many) * 0.95) - 1]
        return True, ("one reader " + format(one, ".0f") + " ms; "
                      + str(readers) + " readers median "
                      + format(statistics.median(many), ".0f") + " ms, p95 "
                      + format(p95, ".0f") + " ms ("
                      + format(statistics.median(many) / max(one, 0.01), ".1f")
                      + "x)")

    def asks_at_once() -> tuple[bool, str]:
        one = alone(_ask)
        many = together(_ask)
        p95 = many[int(len(many) * 0.95) - 1]
        return p95 < TARGETS["ask"], ("one reader " + format(one, ".0f")
                                      + " ms; " + str(readers) + " readers median "
                                      + format(statistics.median(many), ".0f")
                                      + " ms, p95 " + format(p95, ".0f") + " ms")

    def nothing_shared() -> tuple[bool, str]:
        # Two sessions over one warehouse must not see each other's plan: session
        # memory is per-object, and a shared one would leak a question between users.
        first, second = Session(wh, KeywordModel()), Session(wh, KeywordModel())
        first.ask("revenue by market")
        answer = second.ask("by category")
        return answer.refusal is not None, \
            "a second session's follow-up has nothing to patch, as it should"

    j.run(str(readers) + " readers building the overview", views_at_once)
    j.run(str(readers) + " readers asking a question", asks_at_once,
          target=TARGETS["ask"])
    j.run("two sessions do not share memory", nothing_shared)
    return j


# --------------------------------------------------------------------------- #
# the log
# --------------------------------------------------------------------------- #

def report(journeys: list[Journey], stats: dict[str, Any], *, snapshot: str,
           model: str, seconds: float) -> str:
    failed = [s for j in journeys for s in j.failed]
    slow = [s for j in journeys for s in j.steps if s.verdict == "slow"]
    lines = [
        "# QA report",
        "",
        "Generated by `scripts/qa_report.py`. Every row was run against the "
        "snapshot named below, not against a fixture.",
        "",
        "| | |",
        "|---|---|",
        "| snapshot | `" + snapshot + "` |",
        "| model | `" + model + "` |",
        "| journeys | " + str(len(journeys)) + " |",
        "| checks | " + str(sum(len(j.steps) for j in journeys)) + " |",
        "| failures | " + str(len(failed)) + " |",
        "| over target | " + str(len(slow)) + " |",
        "| wall clock | " + format(seconds, ".1f") + "s |",
        "",
    ]
    if failed:
        lines += ["## Failures", ""]
        for step in failed:
            lines.append("- **" + step.name + "** — "
                         + (step.error.splitlines()[-1] if step.error
                            else step.detail or "returned false"))
        lines.append("")
    lines += _benchmark(stats)
    for journey in journeys:
        lines += ["## " + journey.title, "", journey.why, "",
                  "| Check | Verdict | Time | Detail |", "|---|---|---:|---|"]
        for step in journey.steps:
            target = ("" if step.target is None
                      else " / " + format(step.target, ".0f"))
            lines.append("| " + step.name.replace("|", "\\|") + " | "
                         + step.verdict + " | " + format(step.ms, ".0f")
                         + target + " ms | "
                         + step.detail.replace("|", "\\|") + " |")
        lines.append("")
    return "\n".join(lines) + "\n"


def _benchmark(stats: dict[str, Any]) -> list[str]:
    """The accuracy section, and what it is and is not a measurement of."""
    measured, agreed = stats["measured"], stats["agreed"]
    share = (100.0 * agreed / measured) if measured else 0.0
    out = [
        "## Accuracy on the reviewed question set", "",
        "| | |", "|---|---:|",
        "| questions asked | " + str(stats["asked"]) + " |",
        "| stating an expected plan or refusal | " + str(measured) + " |",
        "| read as the set states | " + str(agreed) + " (" + format(share, ".0f")
        + "%) |",
        "| refusal codes reached | " + str(len(stats["codes"])) + " |",
        "| local time per question | median " + format(stats["median_ms"], ".0f")
        + " ms, max " + format(stats["max_ms"], ".0f") + " ms |",
        "",
        "This measures the *whole path* for whichever model ran it. With the keyless "
        "matcher it is a floor, not the agent's accuracy: everything it cannot read "
        "- a filter, a thin-group threshold, a grouping alongside a grain - counts "
        "against it here and is exactly what the model is for. The target in "
        "`docs/00-success-metrics.md` (>= 90% of 20+) is about a real model, and "
        "`scripts/agent_smoke.py` is what measures that.",
        "",
        "The time column excludes the two API calls, so it is the platform's own "
        "share of the 8 s end-to-end target.",
        "",
    ]
    if stats["disagreed"]:
        out += ["Read differently from the set:", ""]
        for question, got in stats["disagreed"]:
            out.append("- " + question + " → `" + got.replace("|", "\\|") + "`")
        out.append("")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default=None,
                    help="a named snapshot id (default: the newest)")
    ap.add_argument("--versions", type=Path, default=None,
                    help="snapshot directory (default: data/versions)")
    ap.add_argument("--model", action="store_true",
                    help="use the real model if a key is present (it costs money)")
    ap.add_argument("--readers", type=int, default=8,
                    help="concurrent readers to simulate (default: 8)")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the log (default: reports/qa-report.md)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    from dtp import REPORTS_DIR
    from dtp.agent import client as agent_client
    from dtp.warehouse import open_warehouse

    model: Any = agent_client.KeywordModel()
    if args.model and agent_client.api_key():
        model = agent_client.AnthropicModel()
    elif args.model:
        print("no " + agent_client.KEY_ENV + " found; running the keyless matcher.")

    started = time.perf_counter()
    try:
        wh = open_warehouse(version_id=args.snapshot, versions_dir=args.versions)
    except FileNotFoundError as exc:
        print(str(exc))
        print("Publish one first:  dtp pipeline --raw dataset")
        return 1

    with wh:
        print("snapshot " + wh.version_id + "   model " + model.name + "\n")
        journeys = []
        for title, build in (("Open the published snapshot",
                              lambda: journey_snapshot(wh)),
                             ("Read every view", lambda: journey_dashboard(wh)),
                             ("The privacy boundary, on the real data",
                              lambda: journey_privacy(wh))):
            print(title)
            journeys.append(build())
            print()
        print("Ask the reviewed question set")
        agent, stats = journey_agent(wh, model)
        journeys.append(agent)
        print("\nHold a conversation")
        journeys.append(journey_session(wh, model))
        print("\nServe more than one reader at once")
        journeys.append(journey_concurrency(wh, readers=args.readers))
        text = report(journeys, stats, snapshot=wh.version_id,
                      model=model.name, seconds=time.perf_counter() - started)

    out = args.out or (REPORTS_DIR / "qa-report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    failed = [s for j in journeys for s in j.failed]
    slow = [s for j in journeys for s in j.steps if s.verdict == "slow"]
    print("\n" + str(len(failed)) + " failure(s), " + str(len(slow))
          + " over target; agreement " + str(stats["agreed"]) + "/"
          + str(stats["measured"]))
    print("wrote " + str(out.relative_to(ROOT) if out.is_relative_to(ROOT) else out))
    for step in failed:
        print("failed: " + step.name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
