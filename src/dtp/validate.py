"""Validation rule engine (Phase 1.2).

Deliberately not Great Expectations. The rules this project needs are a dozen
shapes, all of them declared in `config/validation_rules.yml`, and a dependency
that brings its own execution model, its own store and its own report format
would be more code to understand than the thirteen rule types below.

The design commitment is *failing loudly*. A rule is one of two things:

  error  the pipeline stops, exit code 1, no snapshot is written
  warn   reported and counted, run continues

and a rule may additionally pin `expect_violations` to an exact number. That
turns a documented defect into a tripwire: the 7,754 cancelled-but-billed rows
are allowed to exist, and are not allowed to become 7,755 without someone
noticing. A rule that silently tolerates any number of violations is a comment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from . import CLEAN_DIR, CONFIG_DIR, REPORTS_DIR
from .clean import eval_expr

SEVERITIES = ("error", "warn")

# How many offending values to keep per rule. Enough to diagnose from the report,
# few enough that a 100%-failing rule does not write a 180,000-row table.
SAMPLE_SIZE = 5


@dataclass
class Rule:
    id: str
    type: str
    severity: str = "error"
    reason: str | None = None
    expect_violations: int | None = None
    spec: dict[str, Any] = field(default_factory=dict)

    @property
    def columns(self) -> list[str]:
        """`column:` and `columns:` are interchangeable; one column is the common case."""
        if "columns" in self.spec:
            cols = self.spec["columns"]
            return list(cols) if isinstance(cols, list) else [cols]
        if "column" in self.spec:
            return [self.spec["column"]]
        return []


@dataclass
class Result:
    rule: Rule
    table: str
    n_checked: int
    n_violations: int
    detail: str
    samples: list[str] = field(default_factory=list)
    error: str | None = None      # the rule itself could not run

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        if self.rule.expect_violations is not None:
            return self.n_violations == self.rule.expect_violations
        return self.n_violations == 0

    @property
    def severity(self) -> str:
        # A rule that cannot run is never a warning: an unrunnable check is a
        # check nobody is doing, which is worse than one that fails.
        return "error" if self.error else self.rule.severity

    @property
    def status(self) -> str:
        if self.passed:
            return "pass"
        return "FAIL" if self.severity == "error" else "warn"


@dataclass
class ValidationReport:
    results: list[Result] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    @property
    def warnings(self) -> list[Result]:
        return [r for r in self.results if not r.passed and r.severity == "warn"]

    @property
    def ok(self) -> bool:
        return not self.failures and not self.missing_tables

    @property
    def counts(self) -> dict[str, int]:
        return {
            "rules": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": len(self.failures),
            "warned": len(self.warnings),
        }

    def verdict(self) -> str:
        if self.missing_tables:
            return ("FAIL - expected table(s) absent: "
                    + ", ".join(self.missing_tables))
        if self.failures:
            return ("FAIL - " + str(len(self.failures))
                    + " error-severity rule(s) violated; no snapshot should be published")
        if self.warnings:
            return ("PASS with " + str(len(self.warnings))
                    + " warning(s) - review them, they are not blocking")
        return "PASS - every rule held"


def load_rules(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = path or CONFIG_DIR / "validation_rules.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw.get("tables") or {}


# --------------------------------------------------------------------------- #
# Checkers. Each returns (n_checked, n_violations, detail, samples).
# --------------------------------------------------------------------------- #

def _sample(values: pd.Series) -> list[str]:
    return [str(v) for v in values.head(SAMPLE_SIZE).tolist()]


def _check_not_null(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    total = 0
    per_col: list[str] = []
    for col in rule.columns:
        n = int(df[col].isna().sum())
        total += n
        if n:
            per_col.append(col + "=" + f"{n:,}")
    detail = "nulls: " + (", ".join(per_col) if per_col else "none")
    # No samples: the offending value is null by definition, so there is no
    # example to show. The per-column counts belong in `detail`, where they are
    # labelled as counts - reported as samples they render as "e.g.
    # order_zipcode=155,679", which reads as a zipcode of 155,679.
    return len(df) * len(rule.columns), total, detail, []


def _check_unique(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    cols = rule.columns
    dupes = df.duplicated(subset=cols, keep=False)
    n = int(dupes.sum())
    detail = ("all " + f"{len(df):,}" + " rows distinct on " + ", ".join(cols)
              if not n else f"{n:,}" + " rows share a key value")
    samples = _sample(df.loc[dupes, cols[0]].astype("string")) if n else []
    return len(df), n, detail, samples


def _check_range(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    lo, hi = rule.spec.get("min"), rule.spec.get("max")
    total, checked = 0, 0
    bits: list[str] = []
    samples: list[str] = []
    for col in rule.columns:
        s = pd.to_numeric(df[col], errors="coerce")
        present = s.notna()
        checked += int(present.sum())
        bad = pd.Series(False, index=df.index)
        if lo is not None:
            bad |= present & (s < lo)
        if hi is not None:
            bad |= present & (s > hi)
        n = int(bad.sum())
        total += n
        bits.append(col + " [" + str(s.min()) + ", " + str(s.max()) + "]")
        if n and len(samples) < SAMPLE_SIZE:
            samples += _sample(s[bad].astype("string"))
    bound = "min=" + str(lo) + " max=" + str(hi)
    return checked, total, bound + "; observed " + "; ".join(bits), samples[:SAMPLE_SIZE]


def _check_allowed_values(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    col = rule.columns[0]
    allowed = {str(v) for v in rule.spec["values"]}
    s = df[col].astype("string")
    bad = s.notna() & ~s.isin(allowed)
    n = int(bad.sum())
    unseen = sorted(allowed - set(s.dropna().unique()))
    detail = str(len(allowed)) + " allowed value(s)"
    if unseen:
        # Not a violation, but worth saying: a vocabulary entry with no rows is
        # either a status that has not happened yet or a spelling that is wrong.
        detail += "; declared but never observed: " + ", ".join(unseen)
    return int(s.notna().sum()), n, detail, _sample(s[bad])


def _check_regex(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    col = rule.columns[0]
    pattern = rule.spec["pattern"]
    s = df[col].astype("string")
    present = s.notna()
    hit = s.str.fullmatch(pattern).fillna(False)
    bad = present & ~hit
    n = int(bad.sum())
    return int(present.sum()), n, "pattern " + pattern, _sample(s[bad])


def _check_identity(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    lhs = pd.to_numeric(eval_expr(rule.spec["lhs"], df), errors="coerce")
    rhs = pd.to_numeric(eval_expr(rule.spec["rhs"], df), errors="coerce")
    both = lhs.notna() & rhs.notna()
    resid = (lhs - rhs).abs().where(both)
    tol_abs = float(rule.spec.get("tol_abs", 0) or 0)
    bad = both & (resid > tol_abs)
    if rule.spec.get("tol_rel"):
        # Relative tolerance forgives large values; both must be exceeded to fail,
        # so a cent of drift on $1,900 is not treated like a cent on $9.99.
        scale = rhs.abs().clip(lower=1e-9)
        bad &= (resid / scale) > float(rule.spec["tol_rel"])
    n = int(bad.sum())
    worst = float(resid.max()) if both.any() else 0.0
    detail = (rule.spec["lhs"] + " == " + rule.spec["rhs"]
              + " (tol " + str(tol_abs) + "); max residual " + f"{worst:.6f}")
    return int(both.sum()), n, detail, _sample(resid[bad].round(6).astype("string"))


def _check_expression(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    """The expression must not be FALSE on any row.

    Three-valued logic, like a SQL CHECK constraint and like every other value
    check here: a row where the expression evaluates to null was not checked, and
    an unchecked row is not a violation. Nulls are `not_null`'s job, and treating
    them as failures here would make `expression: "qty >= 1"` and `range: qty
    min 1` disagree about the same column. Exempting them is not allowed to be
    invisible, so they are excluded from `n_checked` and named in the detail.
    """
    got = eval_expr(rule.spec["expr"], df)
    if not isinstance(got, pd.Series):
        got = pd.Series(bool(got), index=df.index)
    known = got.notna()
    bad = known & ~got.fillna(False).astype(bool)
    n = int(bad.sum())
    detail = "expects " + rule.spec["expr"]
    unknown = len(df) - int(known.sum())
    if unknown:
        detail += ("; " + f"{unknown:,}" + " row(s) not evaluable (null operand) "
                   "and so not counted - the not_null rules cover those columns")
    samples: list[str] = []
    if n:
        # Show the row keys, not the booleans: "False" five times explains nothing.
        first = df.columns[0]
        samples = _sample(df.loc[bad, first].astype("string"))
    return int(known.sum()), n, detail, samples


def _check_consistency(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    """Functional dependency: each determinant value maps to exactly one dependent."""
    det = rule.spec["determinant"]
    deps = rule.spec["dependent"]
    deps = deps if isinstance(deps, list) else [deps]
    total = 0
    bits: list[str] = []
    samples: list[str] = []
    grouped = df.groupby(det, observed=True)
    for dep in deps:
        counts = grouped[dep].nunique(dropna=False)
        bad = counts > 1
        n = int(bad.sum())
        total += n
        if n:
            bits.append(dep + "=" + f"{n:,}")
            if len(samples) < SAMPLE_SIZE:
                samples += [str(det) + " " + str(k) + " -> " + str(int(counts[k]))
                            + " values of " + dep for k in counts[bad].index[:SAMPLE_SIZE]]
    detail = (det + " determines " + ", ".join(deps)
              + ("; broken by " + ", ".join(bits) if bits else "; holds"))
    return int(grouped.ngroups), total, detail, samples[:SAMPLE_SIZE]


def _check_referential(rule: Rule, df: pd.DataFrame, tables: dict[str, pd.DataFrame]):
    """Every distinct value must exist in another table's column.

    Counted in DISTINCT values, not rows. "3 categories do not match" is a
    statement someone can act on; "41,000 rows do not match" is the same fact
    weighted by popularity and tells you less.
    """
    col = rule.columns[0]
    target = str(rule.spec["references"])
    if "." not in target:
        raise ValueError("references must be 'table.column', got " + repr(target))
    ref_table, ref_col = target.split(".", 1)
    if ref_table not in tables:
        raise KeyError("referenced table not available: " + ref_table)
    if ref_col not in tables[ref_table].columns:
        raise KeyError("referenced column not available: " + target)

    left = df[col].astype("string")
    right = tables[ref_table][ref_col].astype("string")
    if rule.spec.get("casefold"):
        left, right = left.str.casefold(), right.str.casefold()
    have = set(right.dropna().unique())
    distinct = sorted(set(left.dropna().unique()))
    missing = [v for v in distinct if v not in have]
    detail = (str(len(distinct)) + " distinct value(s) checked against " + target
              + " (" + str(len(have)) + " distinct)"
              + ("; casefolded" if rule.spec.get("casefold") else ""))
    return len(distinct), len(missing), detail, [str(v) for v in missing[:SAMPLE_SIZE]]


def _check_min_rows(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    floor = int(rule.spec.get("min_rows", 1))
    n = 0 if len(df) >= floor else 1
    return len(df), n, f"{len(df):,}" + " row(s), floor " + f"{floor:,}", []


def _check_no_duplicate_rows(rule: Rule, df: pd.DataFrame, _: dict[str, pd.DataFrame]):
    dupes = int(df.duplicated().sum())
    return len(df), dupes, f"{dupes:,}" + " exact duplicate row(s)", []


CHECKERS = {
    "not_null": _check_not_null,
    "unique": _check_unique,
    "range": _check_range,
    "allowed_values": _check_allowed_values,
    "regex": _check_regex,
    "identity": _check_identity,
    "expression": _check_expression,
    "consistency": _check_consistency,
    "referential": _check_referential,
    "min_rows": _check_min_rows,
    "no_duplicate_rows": _check_no_duplicate_rows,
}



# --------------------------------------------------------------------------- #
# Driving the rules
# --------------------------------------------------------------------------- #

def _parse_rule(raw: dict[str, Any]) -> Rule:
    reason = raw.get("reason")
    if reason:
        reason = re.sub(r"\s+", " ", str(reason)).strip()
    severity = str(raw.get("severity", "error"))
    if severity not in SEVERITIES:
        raise ValueError("rule " + str(raw.get("id")) + " has unknown severity "
                         + repr(severity))
    if raw.get("type") not in CHECKERS:
        raise ValueError("rule " + str(raw.get("id")) + " has unknown type "
                         + repr(raw.get("type")))
    spec = {k: v for k, v in raw.items()
            if k not in {"id", "type", "severity", "reason", "expect_violations"}}
    return Rule(
        id=str(raw["id"]), type=str(raw["type"]), severity=severity,
        reason=reason, expect_violations=raw.get("expect_violations"), spec=spec,
    )


def _table_level_rules(table: str, cfg: dict[str, Any]) -> list[Rule]:
    """`min_rows` and `no_duplicate_rows` are written as table properties rather
    than rules because that is how they read; they become real rules here."""
    out: list[Rule] = []
    reason = cfg.get("reason")
    reason = re.sub(r"\s+", " ", str(reason)).strip() if reason else None
    if cfg.get("min_rows") is not None:
        out.append(Rule(
            id="min_rows", type="min_rows", severity="error",
            reason="An empty table means the pipeline produced nothing.",
            spec={"min_rows": int(cfg["min_rows"])},
        ))
    if cfg.get("no_duplicate_rows"):
        out.append(Rule(
            id="no_duplicate_rows", type="no_duplicate_rows", severity="error",
            reason=reason or
            "Roadmap exit criterion: duplicate count must be 0 on re-profile.",
        ))
    return out


def validate_tables(tables: dict[str, pd.DataFrame],
                    rules_cfg: dict[str, dict[str, Any]] | None = None
                    ) -> ValidationReport:
    rules_cfg = load_rules() if rules_cfg is None else rules_cfg
    report = ValidationReport()

    for table, cfg in rules_cfg.items():
        if table not in tables:
            report.missing_tables.append(table)
            continue
        df = tables[table]
        every = _table_level_rules(table, cfg) + [
            _parse_rule(r) for r in (cfg.get("rules") or [])
        ]
        for rule in every:
            report.results.append(_run_rule(rule, table, df, tables))
    return report


def _run_rule(rule: Rule, table: str, df: pd.DataFrame,
              tables: dict[str, pd.DataFrame]) -> Result:
    missing = [c for c in rule.columns if c not in df.columns]
    if missing:
        return Result(rule, table, 0, 0, "", error="column(s) not in table: "
                      + ", ".join(missing))
    try:
        checked, violations, detail, samples = CHECKERS[rule.type](rule, df, tables)
    except Exception as exc:  # noqa: BLE001 - a broken rule is reported, not raised
        # One unrunnable rule must not stop the other forty from reporting; the
        # run still fails, because `Result.severity` forces an error on `error`.
        return Result(rule, table, 0, 0, "",
                      error=type(exc).__name__ + ": " + str(exc))
    return Result(rule, table, checked, violations, detail, samples)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: ValidationReport) -> str:
    c = report.counts
    out = ["# Validation report", "",
           "Generated by `python -m dtp.cli validate`. Contract: "
           "`config/validation_rules.yml`.", "",
           "**" + report.verdict() + "**", "",
           "| rules | passed | failed | warnings |", "|---:|---:|---:|---:|",
           "| " + " | ".join([str(c["rules"]), str(c["passed"]),
                              str(c["failed"]), str(c["warned"])]) + " |", ""]

    if report.missing_tables:
        out += ["## Missing tables", ""]
        out += ["- `" + t + "` has rules but was not produced" for t in report.missing_tables]
        out += [""]

    for label, rows in (("Failures", report.failures), ("Warnings", report.warnings)):
        if not rows:
            continue
        out += ["## " + label, ""]
        for r in rows:
            expected = ("" if r.rule.expect_violations is None
                        else " (expected exactly " + f"{r.rule.expect_violations:,}" + ")")
            out.append("### `" + r.table + "` / " + r.rule.id
                       + " (" + r.rule.type + ")")
            if r.error:
                out += ["", "Rule could not run: " + _md(r.error), ""]
                continue
            out += ["", "- violations: " + f"{r.n_violations:,}" + " of "
                    + f"{r.n_checked:,}" + " checked" + expected,
                    "- " + _md(r.detail)]
            if r.samples:
                out.append("- examples: " + ", ".join("`" + _md(s) + "`"
                                                      for s in r.samples))
            if r.rule.reason:
                out.append("- rationale: " + _md(r.rule.reason))
            out.append("")

    out += ["## Every rule", "",
            "| table | rule | type | severity | checked | violations | status |",
            "|---|---|---|---|---:|---:|---|"]
    for r in report.results:
        out.append("| " + " | ".join([
            r.table, "`" + r.rule.id + "`", r.rule.type, r.severity,
            f"{r.n_checked:,}", f"{r.n_violations:,}", r.status,
        ]) + " |")
    return "\n".join(out) + "\n"


def to_payload(report: ValidationReport) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ok": report.ok,
        "verdict": report.verdict(),
        "counts": report.counts,
        "missing_tables": report.missing_tables,
        "results": [
            {
                "table": r.table, "rule": r.rule.id, "type": r.rule.type,
                "severity": r.severity, "status": r.status,
                "checked": r.n_checked, "violations": r.n_violations,
                "expect_violations": r.rule.expect_violations,
                "detail": r.detail, "samples": r.samples,
                "reason": r.rule.reason, "error": r.error,
            }
            for r in report.results
        ],
    }


def load_clean_tables(clean_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    clean_dir = clean_dir or CLEAN_DIR
    out: dict[str, pd.DataFrame] = {}
    for p in sorted(clean_dir.glob("*.parquet")):
        if p.stem.endswith("__rejects"):
            continue
        out[p.stem] = pd.read_parquet(p)
    return out


def run(clean_dir: Path | None = None, rules_path: Path | None = None,
        tables: dict[str, pd.DataFrame] | None = None,
        reports_dir: Path | None = None
        ) -> tuple[ValidationReport, dict[str, Path]]:
    if tables is None:
        tables = load_clean_tables(clean_dir)
    report = validate_tables(tables, load_rules(rules_path))

    reports_dir = reports_dir or REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    md = reports_dir / "validation-report.md"
    js = reports_dir / "validation-report.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(json.dumps(to_payload(report), indent=2, default=str),
                  encoding="utf-8")
    return report, {"markdown": md, "json": js}









