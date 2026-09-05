"""Command-line entry point.

Phase 1.1 - understand the data as it arrived:

    python -m dtp.cli audit          profile + schema matrix + risk summary
    python -m dtp.cli profile        profiling report only
    python -m dtp.cli schema         schema inconsistency matrix only
    python -m dtp.cli risks          risk summary only
    python -m dtp.cli synthetic      regenerate the messy test fixture

Phase 1.2/1.3 - turn it into something dependable:

    python -m dtp.cli pipeline       clean, validate, snapshot, document, alert
    python -m dtp.cli clean          apply config/cleaning_rules.yml
    python -m dtp.cli validate       apply config/validation_rules.yml
    python -m dtp.cli dict           rebuild docs/data-dictionary.md
    python -m dtp.cli versions       list snapshots, or diff two of them

Phase 3 - ask it something:

    python -m dtp.cli ask "revenue by market"
    python -m dtp.cli ask --repl                 a session, so follow-ups work
    python -m dtp.cli ask --stub "margin by category"    no key, no network

`ask` needs a snapshot, so `pipeline` comes first. Without ANTHROPIC_API_KEY it runs
the keyless keyword stub and says so rather than failing.

Every command takes --raw to point at a different source directory, which is
how the same pipeline runs against the fixture and against the real data:

    python -m dtp.cli audit --raw data/_synthetic
    python -m dtp.cli pipeline --raw dataset
    python -m dtp.cli audit                          # defaults to data/raw

Exit codes are the contract: 0 means every check held, 1 means something the
caller should act on. `pipeline` returns 1 if validation fails, if a field is
undocumented, or if monitoring raised a critical alert.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import CLEAN_DIR, RAW_DIR, REPORTS_DIR, ROOT, VERSIONS_DIR
from . import io_utils, profile as profile_mod, risks as risks_mod, schema_map


def _utf8_stdout() -> None:
    """Findings contain currency symbols; a cp1252 console would crash on them."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _load(raw: Path) -> list[profile_mod.TableProfile]:
    tables, errors = io_utils.load_all(raw)
    for e in errors:
        print("  ! unreadable: " + e)
    if not tables:
        print("No readable tabular sources in " + str(raw))
        print("Drop the data there, or point at another directory with --raw.")
        print("To exercise the pipeline without real data:")
        print("    python -m dtp.cli synthetic")
        print("    python -m dtp.cli audit --raw data/_synthetic")
        return []
    print("Loaded " + str(len(tables)) + " table(s) from " + str(raw))
    for t in tables:
        detail = t.source_format
        if t.encoding and t.encoding != "utf-8":
            detail += ", " + t.encoding
        print(
            "  - " + t.name + ": " + f"{t.shape[0]:,}" + " rows x "
            + str(t.shape[1]) + " cols (" + detail + ")"
        )
    return [profile_mod.profile_table(t) for t in tables]


def cmd_profile(args: argparse.Namespace) -> int:
    profiles, errors, paths = profile_mod.run(args.raw, args.out)
    if not profiles:
        _load(args.raw)
        return 1
    print("")
    for tp in profiles:
        print(tp.name + ": " + str(tp.n_issues) + " finding(s)")
    print("\nwrote " + str(paths["markdown"]))
    print("wrote " + str(paths["json"]))
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    profiles = _load(args.raw)
    if not profiles:
        return 1
    mx, paths = schema_map.run(profiles, REPORTS_DIR / "schema")
    print("")
    if mx.findings:
        for f in mx.findings:
            print("  - " + f)
    else:
        print("  no cross-source inconsistencies found")
    print("\nwrote " + str(paths["markdown"]))
    return 0


def cmd_risks(args: argparse.Namespace) -> int:
    profiles = _load(args.raw)
    if not profiles:
        return 1
    mx = schema_map.build_matrix(profiles)
    reg, paths = risks_mod.run(profiles, mx, REPORTS_DIR)
    _print_risks(reg)
    print("\nwrote " + str(paths["markdown"]))
    return 1 if reg.counts["BLOCKER"] else 0


def _print_risks(reg: risks_mod.RiskRegister) -> None:
    c = reg.counts
    print("")
    print("Risk register: " + ", ".join(k + "=" + str(v) for k, v in c.items()))
    print(reg.go_no_go)
    for sev in ("BLOCKER", "HIGH"):
        for r in reg.by_severity(sev):
            print("  [" + sev + "] " + r.where + ": " + r.finding)


def cmd_audit(args: argparse.Namespace) -> int:
    """Phase 1.1 end to end: profile, reconcile schemas, rank the risks."""
    print("=== Phase 1.1 audit ===")
    tables, errors = io_utils.load_all(args.raw)
    for e in errors:
        print("  ! unreadable: " + e)
    if not tables:
        _load(args.raw)
        return 1

    profiles, _, prof_paths = profile_mod.run(args.raw, args.out)
    print("Loaded " + str(len(profiles)) + " table(s) from " + str(args.raw))
    for tp in profiles:
        print(
            "  - " + tp.name + ": " + f"{tp.n_rows:,}" + " rows x "
            + str(tp.n_cols) + " cols, " + str(tp.n_issues) + " finding(s)"
        )

    mx, schema_paths = schema_map.run(profiles, REPORTS_DIR / "schema")
    reg, risk_paths = risks_mod.run(profiles, mx, REPORTS_DIR)
    _print_risks(reg)

    print("\nReports")
    for p in (prof_paths["markdown"], schema_paths["markdown"], risk_paths["markdown"]):
        print("  " + str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p))
    return 1 if reg.counts["BLOCKER"] else 0


def cmd_synthetic(args: argparse.Namespace) -> int:
    script = ROOT / "scripts" / "make_synthetic_messy.py"
    return subprocess.call(
        [sys.executable, str(script), "--rows", str(args.rows),
         "--outdir", str(args.outdir)]
    )


# --------------------------------------------------------------------------- #
# Phase 1.2 / 1.3
# --------------------------------------------------------------------------- #

def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def cmd_clean(args: argparse.Namespace) -> int:
    from . import clean as clean_mod

    results, problems, paths = clean_mod.run(
        raw_dir=args.raw, out_dir=args.clean, config_path=args.config)
    for p in problems:
        print("  ! " + p)
    if not results:
        print("Nothing cleaned: no configured source in " + str(args.raw))
        return 1
    print("")
    for r in results:
        print(r.table + ": " + f"{r.rows_in:,}" + " -> " + f"{r.rows_out:,}"
              + " rows, " + str(r.df.shape[1]) + " cols, "
              + f"{r.n_rejects:,}" + " rejected cell(s)")
        for step, n in sorted(r.counts_by_step().items()):
            print("    " + step + ": " + f"{n:,}")
    for key in ("markdown", "json"):
        print("wrote " + _rel(paths[key]))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from . import validate as validate_mod

    report, paths = validate_mod.run(clean_dir=args.clean, rules_path=args.rules)
    print("")
    for r in report.results:
        if not r.passed:
            print("  [" + r.status + "] " + r.table + "." + r.rule.id
                  + ": " + r.detail)
    print("\n" + report.verdict())
    print(", ".join(k + "=" + str(v) for k, v in report.counts.items()))
    for key in ("markdown", "json"):
        print("wrote " + _rel(paths[key]))
    return 0 if report.ok else 1


def cmd_dict(args: argparse.Namespace) -> int:
    from . import dictionary as dict_mod

    doc, paths = dict_mod.run(clean_dir=args.clean, raw_dir=args.raw,
                              config_path=args.config, rules_path=args.rules)
    print(str(len(doc.fields)) + " field(s) across "
          + str(len(doc.tables())) + " table(s); "
          + format(doc.coverage, ".1f") + "% documented")
    for g in doc.gaps:
        print("  ! undocumented: " + g.table + "." + g.name)
    for key in ("markdown", "json"):
        print("wrote " + _rel(paths[key]))
    return 0 if not doc.gaps else 1


def cmd_versions(args: argparse.Namespace) -> int:
    from . import versioning as version_mod

    versions = version_mod.list_versions(args.versions)
    if args.diff:
        by_id = {m.version_id: m for m in versions}
        wanted = list(args.diff)
        missing = [v for v in wanted if v not in by_id]
        if missing:
            print("unknown snapshot(s): " + ", ".join(missing))
            print("known: " + (", ".join(by_id) or "none"))
            return 1
        print(version_mod.format_diff(by_id[wanted[0]], by_id[wanted[1]]))
        return 0
    print(version_mod.format_versions(versions), end="")
    return 0 if versions else 1


def cmd_pipeline(args: argparse.Namespace) -> int:
    from . import pipeline as pipeline_mod

    result = pipeline_mod.run(
        raw_dir=args.raw, clean_dir=args.clean, versions_dir=args.versions,
        config_path=args.config, rules_path=args.rules,
        stop_after=args.stop_after, force_snapshot=args.force, notes=args.notes)
    print("=== dtp pipeline ===")
    for s in result.stages:
        print(s.line())
    if result.alerts is not None:
        for a in result.alerts.sorted_alerts():
            print("  " + a.line())
    paths = pipeline_mod.write_report(result)
    print("\n" + result.verdict())
    print("wrote " + _rel(paths["markdown"]))
    return 0 if result.ok else 1


def cmd_ask(args: argparse.Namespace) -> int:
    """Ask the agent one question, or hold a session with `--repl`.

    The model is chosen here and nowhere else: `--stub` is the keyless keyword
    matcher, and without a key the command falls back to it rather than failing, so
    `dtp ask` does something useful on a fresh clone.
    """
    from .agent import Session
    from .agent import client as agent_client
    from .warehouse import open_warehouse

    if args.stub:
        model = agent_client.KeywordModel()
    elif agent_client.api_key():
        model = agent_client.AnthropicModel(model=args.model)
    else:
        print("no " + agent_client.KEY_ENV + " found, so this is the keyless stub:")
        print("it matches registry keys against your words and declines the rest.")
        print("Copy .env.example to .env for the real thing.\n")
        model = agent_client.KeywordModel()

    with open_warehouse(version_id=args.snapshot, versions_dir=args.versions) as wh:
        session = Session(wh, model)
        print("snapshot " + wh.version_id + "   model " + model.name)
        question = " ".join(args.question).strip()
        # `dtp ask` with nothing to ask means the session, not an empty question.
        if question and not args.repl:
            answer = session.ask(question)
            print("\n" + answer.text())
            return 0 if answer.ok else 1
        if question:
            print("\n" + session.ask(question).text())
        return _repl(session)


_REPL_HELP = """\
Ask a question, and follow up: "revenue by market", then "break that down by
region". Commands:

  :plan     the plan the last question resolved to
  :new      forget it, so the next question is not a follow-up
  :help     this
  :quit     leave (or Ctrl-D)
"""


def _repl(session) -> int:
    """One process, one session, nothing written down.

    A REPL is where the follow-up patch earns its keep, and `:plan` is here because a
    session whose memory cannot be printed is a session nobody can debug.
    """
    print("\n" + _REPL_HELP)
    asked = 0
    while True:
        try:
            line = input("ask> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":quit", ":q", ":exit"):
            break
        if line in (":help", ":h", "?"):
            print(_REPL_HELP)
            continue
        if line == ":new":
            session.reset()
            print("(forgotten - the next question starts fresh)")
            continue
        if line == ":plan":
            print(session.plan.to_dict() if session.plan is not None
                  else "(no plan yet)")
            continue
        answer = session.ask(line)
        asked += 1
        print(answer.text() + "\n")
    print("asked " + str(asked) + " question(s); nothing was saved.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m dtp.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def with_raw(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--raw", type=Path, default=RAW_DIR,
                       help="source directory (default: data/raw)")
        p.add_argument("--out", type=Path, default=REPORTS_DIR / "profiling",
                       help="where profiling reports are written")
        return p

    with_raw(sub.add_parser("profile", help="profile every source")).set_defaults(
        func=cmd_profile
    )
    with_raw(sub.add_parser("schema", help="cross-source schema matrix")).set_defaults(
        func=cmd_schema
    )
    with_raw(sub.add_parser("risks", help="ranked risk summary")).set_defaults(
        func=cmd_risks
    )
    with_raw(sub.add_parser("audit", help="profile + schema + risks")).set_defaults(
        func=cmd_audit
    )

    syn = sub.add_parser("synthetic", help="regenerate the messy test fixture")
    syn.add_argument("--rows", type=int, default=500)
    syn.add_argument("--outdir", type=Path, default=ROOT / "data" / "_synthetic")
    syn.set_defaults(func=cmd_synthetic)

    def with_config(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--raw", type=Path, default=RAW_DIR,
                       help="source directory (default: data/raw)")
        p.add_argument("--clean", type=Path, default=CLEAN_DIR,
                       help="where clean Parquet lives (default: data/clean)")
        p.add_argument("--config", type=Path, default=None,
                       help="override config/cleaning_rules.yml")
        p.add_argument("--rules", type=Path, default=None,
                       help="override config/validation_rules.yml")
        p.add_argument("--versions", type=Path, default=VERSIONS_DIR,
                       help="snapshot directory (default: data/versions)")
        return p

    with_config(sub.add_parser(
        "clean", help="apply cleaning rules, write data/clean")).set_defaults(
        func=cmd_clean)
    with_config(sub.add_parser(
        "validate", help="apply validation rules to the clean tables")).set_defaults(
        func=cmd_validate)
    with_config(sub.add_parser(
        "dict", help="rebuild the data dictionary")).set_defaults(func=cmd_dict)

    ver = with_config(sub.add_parser("versions", help="list or diff snapshots"))
    ver.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"),
                     help="compare two snapshot ids")
    ver.set_defaults(func=cmd_versions)

    pipe = with_config(sub.add_parser(
        "pipeline", help="clean, validate, snapshot, document, alert"))
    pipe.add_argument("--stop-after", choices=list(pipeline_stages()),
                      default=None, help="run only up to this stage")
    pipe.add_argument("--force", action="store_true",
                      help="publish a snapshot even if validation failed "
                           "(stamped as forced in the manifest)")
    pipe.add_argument("--notes", default=None,
                      help="free text recorded in the snapshot manifest")
    pipe.set_defaults(func=cmd_pipeline)

    ask = sub.add_parser("ask", help="ask the agent a question about a snapshot")
    ask.add_argument("question", nargs="*", help="the question, unquoted is fine")
    ask.add_argument("--repl", action="store_true",
                     help="hold a session, so follow-ups patch the last plan")
    ask.add_argument("--stub", action="store_true",
                     help="use the keyless keyword matcher instead of the model")
    ask.add_argument("--model", default=None,
                     help="override DTP_AGENT_MODEL for this run")
    ask.add_argument("--versions", type=Path, default=VERSIONS_DIR,
                     help="snapshot directory (default: data/versions)")
    ask.add_argument("--snapshot", default=None,
                     help="a named snapshot id (default: the newest)")
    ask.set_defaults(func=cmd_ask)
    return ap


def pipeline_stages() -> tuple[str, ...]:
    # Imported lazily so `--help` does not pay for pandas.
    from .pipeline import STAGES

    return STAGES


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
