"""Command-line entry point.

    python -m dtp.cli audit          profile + schema matrix + risk summary
    python -m dtp.cli profile        profiling report only
    python -m dtp.cli schema         schema inconsistency matrix only
    python -m dtp.cli risks          risk summary only
    python -m dtp.cli synthetic      regenerate the messy test fixture

Every command takes --raw to point at a different source directory, which is
how the same pipeline runs against the fixture and against the real data:

    python -m dtp.cli audit --raw data/_synthetic
    python -m dtp.cli audit                          # defaults to data/raw
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import RAW_DIR, REPORTS_DIR, ROOT
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
    return ap


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
