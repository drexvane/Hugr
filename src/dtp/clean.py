"""Declarative cleaning engine (Phase 1.2).

The policy lives in `config/cleaning_rules.yml`; this module is only the engine
that executes it. That split is deliberate - every per-column decision the
roadmap asks to be "documented with rationale" is reviewable as data by someone
who does not read Python, and the rationale travels with the rule instead of
rotting in a commit message.

Two rules the whole module is built around:

1. **Nothing silent.** Every value that is changed, nulled, dropped or rejected
   is counted, and the counts land in `reports/cleaning-report.md`. A cleaning
   step whose effect you cannot see is indistinguishable from a bug.
2. **A bad cell loses the cell, not the row.** Coercion failures are quarantined
   into a rejects table with the raw text and the reason, and the cell becomes
   null. Dropping 40 good fields because one failed to parse destroys more
   information than it saves.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from . import CLEAN_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, io_utils

# Values that mean "no value" but arrive as text. Case-folded before comparison.
# Deliberately conservative: "0", "none of the above", "null island" are data.
SENTINEL_NULLS = frozenset(
    {"", "-", "--", "n/a", "na", "n.a.", "nan", "null", "none", "nil",
     "unknown", "unspecified", "not available", "not applicable", "?", "#n/a"}
)

TRUE_TOKENS = frozenset({"1", "true", "t", "yes", "y"})
FALSE_TOKENS = frozenset({"0", "false", "f", "no", "n"})

_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_URL_RE = re.compile(r"^(?:https?|ftp)://\S+$", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

NUMERIC_TYPES = frozenset({"integer", "money", "ratio", "coordinate", "float"})

# `load_sources` reports three kinds of problem in one list of strings. These
# markers are how dtp.monitoring tells them apart - a file we chose to skip is
# not the same event as one nobody has configured, which is not the same as one
# that failed to parse. Changing the wording means changing it here.
SKIPPED_MARKER = ": skipped by config - "
UNCONFIGURED_MARKER = ": no entry in cleaning_rules.yml, so not cleaned"


# --------------------------------------------------------------------------- #
# Config objects
# --------------------------------------------------------------------------- #

@dataclass
class ColumnRule:
    source: str
    clean: str
    type: str = "text"
    role: str | None = None
    nulls: str = "keep_null"
    fill: Any = None            # only read by impute_constant
    transform: str | None = None
    width: int | None = None    # only read by zero_pad
    description: str | None = None   # what the field means, for the dictionary
    reason: str | None = None        # why we treated it this way


@dataclass
class DerivedRule:
    name: str
    type: str
    expr: str
    null_when: str | None = None
    reason: str | None = None


@dataclass
class SourceRules:
    key: str                    # the YAML key, i.e. the file stem
    table: str
    role: str = "fact"
    encoding: str | None = None
    grain: str | None = None
    key_columns: list[str] = field(default_factory=list)
    date_formats: list[str] = field(default_factory=list)
    row_count_expected: int | None = None
    row_fixes: list[dict[str, Any]] = field(default_factory=list)
    value_maps: dict[str, dict[str, str]] = field(default_factory=dict)
    columns: list[ColumnRule] = field(default_factory=list)
    drop: list[dict[str, str]] = field(default_factory=list)
    derived: list[DerivedRule] = field(default_factory=list)
    deduplicate: dict[str, Any] = field(default_factory=dict)
    join_notes: str | None = None

    @property
    def dropped_names(self) -> list[str]:
        return [d["source"] for d in self.drop]

    @property
    def drop_reasons(self) -> dict[str, str]:
        return {d["source"]: d.get("reason", "") for d in self.drop}

    def rule_for(self, source_column: str) -> ColumnRule | None:
        for c in self.columns:
            if c.source == source_column:
                return c
        return None


@dataclass
class CleaningConfig:
    version: int
    defaults: dict[str, Any]
    sources: dict[str, SourceRules]
    skip: dict[str, str] = field(default_factory=dict)

    def for_table(self, name: str) -> SourceRules | None:
        """Match on the YAML key (file stem) first, then on `table:`."""
        if name in self.sources:
            return self.sources[name]
        lowered = name.casefold()
        for key, spec in self.sources.items():
            if lowered in (key.casefold(), spec.table.casefold()):
                return spec
        return None

    def skip_reason(self, name: str) -> str | None:
        for key, reason in self.skip.items():
            if name.casefold() == key.casefold():
                return reason
        return None


DEFAULT_DEFAULTS = {
    "trim_whitespace": True,
    "collapse_internal_spaces": True,
    "sentinel_nulls_to_null": True,
    "money_dp": 2,
    "ratio_dp": 6,
    "coordinate_dp": 6,
}


def load_config(path: Path | None = None) -> CleaningConfig:
    path = path or CONFIG_DIR / "cleaning_rules.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = {**DEFAULT_DEFAULTS, **(raw.get("defaults") or {})}
    sources: dict[str, SourceRules] = {}
    for key, spec in (raw.get("sources") or {}).items():
        sources[key] = _parse_source(key, spec)
    skip = {s["source"]: (_tidy(s.get("reason")) or "") for s in (raw.get("skip") or [])}
    return CleaningConfig(int(raw.get("version", 1)), defaults, sources, skip)


def _parse_source(key: str, spec: dict[str, Any]) -> SourceRules:
    columns = [
        ColumnRule(
            source=c["source"],
            clean=c["clean"],
            type=c.get("type", "text"),
            role=c.get("role"),
            nulls=c.get("nulls", "keep_null"),
            fill=c.get("fill"),
            transform=c.get("transform"),
            width=c.get("width"),
            description=_tidy(c.get("description")),
            reason=_tidy(c.get("reason")),
        )
        for c in (spec.get("columns") or [])
    ]
    derived = [
        DerivedRule(
            name=d["name"],
            type=d.get("type", "text"),
            expr=str(d["expr"]),
            null_when=(str(d["null_when"]) if d.get("null_when") else None),
            reason=_tidy(d.get("reason")),
        )
        for d in (spec.get("derived") or [])
    ]
    return SourceRules(
        key=key,
        table=spec.get("table", key),
        role=spec.get("role", "fact"),
        encoding=spec.get("encoding"),
        grain=spec.get("grain"),
        key_columns=list(spec.get("key") or []),
        date_formats=list(spec.get("date_formats") or []),
        row_count_expected=spec.get("row_count_expected"),
        row_fixes=list(spec.get("row_fixes") or []),
        value_maps={k: dict(v) for k, v in (spec.get("value_maps") or {}).items()},
        columns=columns,
        drop=list(spec.get("drop") or []),
        derived=derived,
        deduplicate=dict(spec.get("deduplicate") or {}),
        join_notes=_tidy(spec.get("join_notes")),
    )


def _tidy(text: Any) -> str | None:
    """YAML block scalars arrive with newlines; reports want one line."""
    if text is None:
        return None
    return _WS_RE.sub(" ", str(text)).strip()


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

@dataclass
class Action:
    """One thing the engine did, with the count that proves it happened."""

    step: str
    target: str            # column name, or "<table>" for row-level steps
    detail: str
    n: int = 0
    reason: str | None = None


@dataclass
class CleanTable:
    name: str                      # source stem
    table: str                     # clean table name
    df: pd.DataFrame
    rejects: pd.DataFrame
    actions: list[Action] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0
    cols_in: int = 0
    spec: SourceRules | None = None

    def log(self, step: str, target: str, detail: str, n: int = 0,
            reason: str | None = None) -> None:
        # Zero-effect steps are noise in the report but signal in a diff: a rule
        # that stops matching is how you learn the source data changed shape.
        self.actions.append(Action(step, target, detail, n, reason))

    @property
    def n_rejects(self) -> int:
        return len(self.rejects)

    def counts_by_step(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.actions:
            out[a.step] = out.get(a.step, 0) + a.n
        return out


# --------------------------------------------------------------------------- #
# Step 1 - text normalisation
# --------------------------------------------------------------------------- #

def normalise_text(df: pd.DataFrame, defaults: dict[str, Any],
                   result: CleanTable) -> pd.DataFrame:
    """Trim, collapse runs of whitespace, and turn sentinels into real nulls.

    Runs on the raw text frame before any typing, because "42 " and " 42" must
    become the same integer and " " must become null rather than a number that
    fails to parse.
    """
    trim = bool(defaults.get("trim_whitespace", True))
    collapse = bool(defaults.get("collapse_internal_spaces", True))
    sentinels = bool(defaults.get("sentinel_nulls_to_null", True))

    for col in df.columns:
        s = df[col]
        if not _is_texty(s):
            continue
        original = s
        if trim:
            s = s.str.strip()
            n = int((original.fillna("") != s.fillna("")).sum())
            if n:
                result.log("trim_whitespace", col, "stripped leading/trailing", n)
        if collapse:
            before = s
            s = s.str.replace(r"\s+", " ", regex=True)
            n = int((before.fillna("") != s.fillna("")).sum())
            if n:
                result.log("collapse_spaces", col, "collapsed internal runs", n)
        if sentinels:
            mask = s.str.casefold().isin(SENTINEL_NULLS) & s.notna()
            n = int(mask.sum())
            if n:
                s = s.mask(mask, pd.NA)
                result.log("sentinel_null", col, "recognised as missing", n)
        df[col] = s
    return df


def _is_texty(s: pd.Series) -> bool:
    return bool(pd.api.types.is_string_dtype(s) or s.dtype == object)


# --------------------------------------------------------------------------- #
# Step 2 - conditional row repairs
# --------------------------------------------------------------------------- #

# `where:` is a tiny fixed vocabulary, not a general language. A row fix that
# can express anything can also silently rewrite the wrong rows, and these run
# before typing when the data is at its least trustworthy.
_COND_BLANK = re.compile(r"^(?P<col>[\w ]+?)\s+is\s+blank$", re.IGNORECASE)
_COND_MATCH = re.compile(r"^(?P<col>[\w ]+?)\s+matches\s+(?P<pat>.+)$", re.IGNORECASE)


def _resolve(df: pd.DataFrame, spec: SourceRules, name: str) -> str:
    """Rules name columns by their clean name; the frame still has source names."""
    if name in df.columns:
        return name
    for rule in spec.columns:
        if rule.clean == name:
            return rule.source
    raise KeyError(
        "cleaning_rules.yml refers to unknown column " + repr(name)
        + " in source " + spec.key
    )


def _condition_mask(df: pd.DataFrame, spec: SourceRules, where: str) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for clause in re.split(r"\s+and\s+", where.strip(), flags=re.IGNORECASE):
        clause = clause.strip()
        if m := _COND_BLANK.match(clause):
            col = _resolve(df, spec, m.group("col").strip())
            s = df[col]
            mask &= s.isna() | (s.astype("string").fillna("").str.strip() == "")
        elif m := _COND_MATCH.match(clause):
            col = _resolve(df, spec, m.group("col").strip())
            pat = m.group("pat").strip().strip("'\"")
            hit = df[col].astype("string").str.fullmatch(pat)
            mask &= hit.fillna(False)
        else:
            raise ValueError("unsupported row_fix condition: " + repr(clause))
    return mask


def apply_row_fixes(df: pd.DataFrame, spec: SourceRules,
                    result: CleanTable) -> pd.DataFrame:
    for fix in spec.row_fixes:
        fid = fix.get("id", "row_fix")
        cols = [_resolve(df, spec, c) for c in fix.get("columns", [])]
        mask = _condition_mask(df, spec, fix["where"])
        n = int(mask.sum())
        if fix.get("do") != "shift_right":
            raise ValueError("unsupported row_fix action " + repr(fix.get("do")))
        if n:
            # Values sit one field to the left of where they belong: the last
            # column takes the middle one's value, the middle takes the first's,
            # and the first is left null rather than guessed at.
            for dst, src in zip(reversed(cols), reversed(cols[:-1])):
                df.loc[mask, dst] = df.loc[mask, src]
            df.loc[mask, cols[0]] = pd.NA
        result.log("row_fix:" + fid, ", ".join(cols),
                   "shift_right where " + fix["where"], n, _tidy(fix.get("reason")))
    return df


# --------------------------------------------------------------------------- #
# Step 3 - controlled vocabularies
# --------------------------------------------------------------------------- #

def apply_value_maps(df: pd.DataFrame, spec: SourceRules,
                     result: CleanTable) -> pd.DataFrame:
    """Two shapes. `<column>:` remaps a column's own values. `<column>_by_<driver>:`
    rewrites `<column>` on rows selected by `<driver>`'s value - which exists
    because two distinct category ids share the display name "Electronics", and
    only the id can tell them apart."""
    for target, mapping in spec.value_maps.items():
        if "_by_" in target:
            base, _, driver = target.rpartition("_by_")
            col = _resolve(df, spec, base)
            key_col = _resolve(df, spec, driver)
            keys = df[key_col].astype("string").str.strip()
            for key, new in mapping.items():
                mask = (keys == str(key)).fillna(False)
                n = int(mask.sum())
                if n:
                    df.loc[mask, col] = new
                result.log("value_map", col,
                           key_col + "=" + str(key) + " -> " + repr(new), n)
            continue

        col = _resolve(df, spec, target)
        s = df[col].astype("string")
        for old, new in mapping.items():
            mask = (s == str(old)).fillna(False)
            n = int(mask.sum())
            if n:
                df.loc[mask, col] = new
            result.log("value_map", col, repr(str(old)) + " -> " + repr(new), n)
    return df


# --------------------------------------------------------------------------- #
# Step 4 - typing, with a quarantine instead of a crash
# --------------------------------------------------------------------------- #

def coerce_column(s: pd.Series, rule: ColumnRule, spec: SourceRules,
                  defaults: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    """Return (typed series, mask of values that could not be coerced).

    Every branch is *exact*: no dateutil guessing, no `errors="coerce"` on text
    that might be three different things. A value either matches the declared
    shape or it is rejected by name, because a silent coercion is how "1/2/2015"
    becomes February in one column and January in another.
    """
    text = s.astype("string")
    present = text.notna()
    t = rule.type

    if t == "integer":
        ok = text.str.fullmatch(_INT_RE.pattern).fillna(False)
        out = pd.to_numeric(text.where(ok), errors="coerce").astype("Int64")
    elif t in NUMERIC_TYPES:
        ok = text.str.fullmatch(_NUM_RE.pattern).fillna(False)
        out = pd.to_numeric(text.where(ok), errors="coerce").astype("Float64")
        dp = {"money": defaults.get("money_dp", 2),
              "ratio": defaults.get("ratio_dp", 6),
              "coordinate": defaults.get("coordinate_dp", 6)}.get(t)
        if dp is not None:
            out = out.round(int(dp))
    elif t == "datetime":
        out, ok = _coerce_datetime(text, spec)
    elif t == "boolean":
        folded = text.str.casefold()
        ok = folded.isin(TRUE_TOKENS | FALSE_TOKENS).fillna(False)
        out = folded.isin(TRUE_TOKENS).astype("boolean").where(ok)
    elif t == "category":
        out, ok = text.astype("category"), present
    elif t == "url":
        # A malformed URL is still the string the source gave us: it is reported
        # as a finding by the profiler, not destroyed here.
        out, ok = text, present
    else:
        out, ok = text, present

    # Missing input is not a rejection - only a present value that will not parse.
    rejected = present & ~ok.fillna(False)
    return out, rejected


def _coerce_datetime(text: pd.Series, spec: SourceRules) -> tuple[pd.Series, pd.Series]:
    """Try each configured format in order; a value must match one exactly."""
    formats = spec.date_formats or ["%Y-%m-%d"]
    out = pd.Series(pd.NaT, index=text.index, dtype="datetime64[ns]")
    todo = text.notna()
    for fmt in formats:
        if not todo.any():
            break
        parsed = pd.to_datetime(text.where(todo), format=fmt, errors="coerce")
        hit = parsed.notna()
        out = out.mask(hit, parsed)
        todo &= ~hit
    return out, text.notna() & out.notna()


def _validate_formats(spec: SourceRules) -> None:
    """A typo in a strptime format is a silent 100% rejection rate; fail early."""
    for fmt in spec.date_formats:
        try:
            datetime.strptime(datetime(2015, 1, 31, 13, 45).strftime(fmt), fmt)
        except ValueError as exc:
            raise ValueError(
                "date_formats entry " + repr(fmt) + " for " + spec.key
                + " is not a usable strptime format: " + str(exc)
            ) from exc


# --------------------------------------------------------------------------- #
# Step 4b - per-column transforms
# --------------------------------------------------------------------------- #

TRANSFORMS = frozenset({"zero_pad", "upper", "lower", "title"})


def apply_transform(s: pd.Series, rule: ColumnRule,
                    result: CleanTable) -> pd.Series:
    """Reversible presentation fixes, applied after typing.

    Kept separate from typing because these change values rather than
    interpreting them, and that difference is the one a reviewer cares about.
    """
    name = rule.transform
    if not name:
        return s
    if name not in TRANSFORMS:
        raise ValueError("unknown transform " + repr(name) + " for " + rule.clean)

    before = s.astype("string")
    if name == "zero_pad":
        width = int(rule.width or 0)
        if width <= 0:
            raise ValueError("zero_pad on " + rule.clean + " needs a positive width")
        too_long = before.str.len() > width
        if bool(too_long.any()):
            # Padding must never truncate. If a value is already wider than the
            # target the assumption behind the rule is wrong, and guessing here
            # would corrupt real identifiers.
            raise ValueError(
                rule.clean + ": " + str(int(too_long.sum())) + " value(s) longer than "
                + "zero_pad width " + str(width) + ", e.g. "
                + repr(before[too_long].iloc[0])
            )
        after = before.str.zfill(width)
    elif name == "upper":
        after = before.str.upper()
    elif name == "lower":
        after = before.str.lower()
    else:
        after = before.str.title()

    n = int((before.fillna("\x00") != after.fillna("\x00")).sum())
    result.log("transform:" + name, rule.clean,
               name + (" to width " + str(rule.width) if rule.width else ""),
               n, rule.reason)
    return after


# --------------------------------------------------------------------------- #
# Step 5 - missing values
# --------------------------------------------------------------------------- #

# `keep_null` and `not_applicable` both leave the data alone. They are still
# recorded separately because they mean different things to a reader: the first
# says "we could not know", the second says "there is nothing to know", and the
# roadmap asks for that distinction to be documented rather than inferred.
NULL_STRATEGIES = frozenset(
    {"keep_null", "not_applicable", "impute_constant", "impute_median",
     "impute_mode", "impute_zero", "drop_row"}
)


def apply_null_strategy(df: pd.DataFrame, rule: ColumnRule,
                        result: CleanTable) -> pd.DataFrame:
    col = rule.clean
    strategy = rule.nulls
    if strategy not in NULL_STRATEGIES:
        raise ValueError("unknown nulls strategy " + repr(strategy) + " for " + col)

    missing = int(df[col].isna().sum())
    if not missing:
        return df

    if strategy in ("keep_null", "not_applicable"):
        result.log("nulls:" + strategy, col, "left null deliberately",
                   missing, rule.reason)
        return df

    if strategy == "drop_row":
        df = df.loc[df[col].notna()].copy()
        result.log("nulls:drop_row", col, "rows removed", missing, rule.reason)
        return df

    if strategy == "impute_constant":
        fill = rule.fill
    elif strategy == "impute_zero":
        fill = 0
    elif strategy == "impute_median":
        fill = df[col].median()
    else:
        modes = df[col].mode(dropna=True)
        fill = modes.iloc[0] if len(modes) else pd.NA

    if pd.isna(fill):
        result.log("nulls:" + strategy, col,
                   "no value available to impute; left null", missing, rule.reason)
        return df
    df[col] = df[col].fillna(fill)
    result.log("nulls:" + strategy, col, "filled with " + repr(fill),
               missing, rule.reason)
    return df


# --------------------------------------------------------------------------- #
# Step 6 - derived columns
# --------------------------------------------------------------------------- #

# `expr` in the config is really evaluated, so the config cannot lie about what
# the pipeline computes. It is NOT eval(): the string is parsed to an AST and
# every node is checked against this whitelist, so nothing outside arithmetic
# and membership tests on named columns can execute. Anything else - a call, an
# attribute, a subscript, an import - fails to build.
_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}
_CMP_OPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a.isin(b) if hasattr(a, "isin") else a in b,
    ast.NotIn: lambda a, b: ~a.isin(b) if hasattr(a, "isin") else a not in b,
}
_BOOL_OPS = {ast.And: lambda a, b: a & b, ast.Or: lambda a, b: a | b}

# Names the engine supplies rather than reading from the frame.
ROW_NUMBER = "row_number"


def eval_expr(expr: str, df: pd.DataFrame) -> Any:
    """Evaluate a config expression against a DataFrame's columns."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("cannot parse expression " + repr(expr) + ": " + str(exc)) from exc
    return _eval_node(tree.body, df, expr)


def _eval_node(node: ast.AST, df: pd.DataFrame, expr: str) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == ROW_NUMBER:
            return pd.Series(np.arange(1, len(df) + 1), index=df.index, dtype="Int64")
        if node.id not in df.columns:
            raise ValueError(
                "expression " + repr(expr) + " refers to unknown column "
                + repr(node.id) + "; available: " + ", ".join(map(str, df.columns))
            )
        return df[node.id]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_eval_node(e, df, expr) for e in node.elts]
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, df, expr)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Not):
            return ~operand
        raise ValueError("unsupported unary operator in " + repr(expr))
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("unsupported operator in " + repr(expr))
        return op(_eval_node(node.left, df, expr), _eval_node(node.right, df, expr))
    if isinstance(node, ast.BoolOp):
        op = _BOOL_OPS[type(node.op)]
        values = [_eval_node(v, df, expr) for v in node.values]
        out = values[0]
        for v in values[1:]:
            out = op(out, v)
        return out
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            raise ValueError("chained comparisons are not supported: " + repr(expr))
        op = _CMP_OPS.get(type(node.ops[0]))
        if op is None:
            raise ValueError("unsupported comparison in " + repr(expr))
        return op(_eval_node(node.left, df, expr),
                  _eval_node(node.comparators[0], df, expr))
    raise ValueError(
        type(node).__name__ + " is not allowed in a config expression: " + repr(expr)
    )


def add_derived(df: pd.DataFrame, spec: SourceRules, defaults: dict[str, Any],
                result: CleanTable) -> pd.DataFrame:
    for d in spec.derived:
        values = eval_expr(d.expr, df)
        if not isinstance(values, pd.Series):
            values = pd.Series(values, index=df.index)

        nulled = 0
        if d.null_when:
            guard = eval_expr(d.null_when, df)
            guard = guard.fillna(False) if hasattr(guard, "fillna") else guard
            nulled = int(np.asarray(guard).sum())
            values = values.mask(guard, pd.NA)

        # A division by a legitimately-zero denominator yields inf, not NaN, and
        # inf survives Parquet round-trips to poison whatever averages it later.
        if d.type in NUMERIC_TYPES:
            values = pd.to_numeric(values, errors="coerce")
            values = values.replace([np.inf, -np.inf], pd.NA)
            if d.type == "integer":
                values = values.astype("Int64")
            else:
                values = values.astype("Float64")
                dp = {"money": defaults.get("money_dp", 2),
                      "ratio": defaults.get("ratio_dp", 6),
                      "coordinate": defaults.get("coordinate_dp", 6)}.get(d.type)
                if dp is not None:
                    values = values.round(int(dp))
        elif d.type == "boolean":
            values = values.astype("boolean")
        elif d.type == "category":
            values = values.astype("category")

        df[d.name] = values
        detail = d.expr + (" | null when " + d.null_when if d.null_when else "")
        result.log("derived", d.name, detail, int(values.notna().sum()), d.reason)
        if nulled:
            result.log("derived:guard", d.name,
                       "nulled by " + d.null_when, nulled)
    return df


# --------------------------------------------------------------------------- #
# Step 7 - de-duplication
# --------------------------------------------------------------------------- #

def deduplicate(df: pd.DataFrame, spec: SourceRules,
                result: CleanTable) -> pd.DataFrame:
    """Runs before derived columns so a minted surrogate key cannot itself make
    duplicate rows look distinct."""
    rules = spec.deduplicate
    if not rules:
        # Still measure it. The roadmap's exit criterion is "duplicate count 0
        # on re-profile", and a table with no dedup rule has to prove it needs none.
        n = int(df.duplicated().sum())
        result.log("deduplicate", "<table>", "no rule configured; exact duplicates found", n)
        if n:
            result.warnings.append(
                str(n) + " exact duplicate row(s) left in place: no deduplicate rule "
                "is configured for " + spec.key
            )
        return df

    strategy = rules.get("strategy", "exact_rows")
    reason = _tidy(rules.get("reason"))
    if strategy == "exact_rows":
        subset = None
    elif strategy == "subset":
        subset = [_resolve(df, spec, c) for c in rules.get("columns", [])]
    else:
        raise ValueError("unsupported deduplicate strategy " + repr(strategy))

    dupes = df.duplicated(subset=subset, keep="first")
    n = int(dupes.sum())
    if n:
        df = df.loc[~dupes].copy()
    result.log("deduplicate", "<table>",
               strategy + ": rows removed, first occurrence kept", n, reason)
    return df


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def clean_table(table: io_utils.LoadedTable, spec: SourceRules,
                defaults: dict[str, Any]) -> CleanTable:
    """Run the whole policy for one source, in the one order that works.

    Order matters and is not arbitrary: normalise text before typing (so " 42 "
    parses), repair rows before typing (the repair reads text), map values before
    typing (a mapped value must then be categorised), type before imputing (a
    median needs numbers), de-duplicate before deriving (a surrogate key would
    make duplicates unique), and drop last-known-good columns only after any
    row fix that needed them.
    """
    _validate_formats(spec)
    result = CleanTable(
        name=table.name, table=spec.table, df=pd.DataFrame(),
        rejects=pd.DataFrame(), rows_in=len(table.df),
        cols_in=table.df.shape[1], spec=spec,
    )
    result.warnings.extend(table.warnings)

    if spec.encoding and table.encoding and spec.encoding != table.encoding:
        result.warnings.append(
            "encoding mismatch: config pins " + spec.encoding
            + " but the file was read as " + table.encoding
        )
    if spec.row_count_expected is not None and len(table.df) != spec.row_count_expected:
        result.warnings.append(
            "row count " + f"{len(table.df):,}" + " != expected "
            + f"{spec.row_count_expected:,}" + "; the source file changed shape"
        )

    df = table.df.copy()
    unknown = [c for c in df.columns
               if spec.rule_for(c) is None and c not in spec.dropped_names]
    if unknown:
        # Not fatal, but it must be loud: an unconfigured column is a decision
        # nobody has made yet, and defaulting it to "keep as text" would hide that.
        result.warnings.append(
            "column(s) present in the file but absent from cleaning_rules.yml, "
            "so excluded from the clean table: " + ", ".join(unknown)
        )

    df = normalise_text(df, defaults, result)
    df = apply_row_fixes(df, spec, result)
    df = apply_value_maps(df, spec, result)

    for name in spec.dropped_names:
        if name in df.columns:
            df = df.drop(columns=[name])
            result.log("drop_column", name, "removed from the clean table", 1,
                       _tidy(spec.drop_reasons.get(name)))
        else:
            result.warnings.append("configured drop column not in file: " + name)

    df = deduplicate(df, spec, result)

    # Typing. The key column is captured first so a reject can be traced back to
    # a business identifier rather than to a positional row number that shifts
    # the moment de-duplication removes a row.
    key_source = [r.source for r in spec.columns if r.clean in spec.key_columns]
    key_label = key_source[0] if key_source else None
    key_values = df[key_label].astype("string") if key_label in df.columns else None

    rejects: list[dict[str, Any]] = []
    typed: dict[str, pd.Series] = {}
    for rule in spec.columns:
        if rule.source not in df.columns:
            result.warnings.append("configured column not in file: " + rule.source)
            continue
        series, bad = coerce_column(df[rule.source], rule, spec, defaults)
        series = apply_transform(series, rule, result)
        typed[rule.clean] = series
        n_bad = int(bad.sum())
        if n_bad:
            raw = df.loc[bad, rule.source].astype("string")
            rejects.extend(
                {
                    "table": spec.table,
                    "row_key": (str(key_values[i]) if key_values is not None else str(i)),
                    "column": rule.clean,
                    "source_column": rule.source,
                    "declared_type": rule.type,
                    "raw_value": v,
                    "reason": "does not match declared type " + rule.type,
                }
                for i, v in raw.items()
            )
            result.log("reject_cell", rule.clean,
                       "value(s) did not match type " + rule.type + "; set to null", n_bad)
        result.log("type", rule.clean,
                   rule.source + " -> " + rule.type, int(series.notna().sum()),
                   rule.reason)

    clean = pd.DataFrame(typed, index=df.index)
    for rule in spec.columns:
        if rule.clean in clean.columns:
            clean = apply_null_strategy(clean, rule, result)

    clean = add_derived(clean, spec, defaults, result)

    # Keys and their surrogates lead; everything else keeps config order, which
    # is the order the policy document reads in.
    ordered = [c for c in spec.key_columns if c in clean.columns]
    ordered += [c for c in clean.columns if c not in ordered]
    clean = clean.loc[:, ordered].reset_index(drop=True)

    result.df = clean
    result.rejects = pd.DataFrame(
        rejects,
        columns=["table", "row_key", "column", "source_column",
                 "declared_type", "raw_value", "reason"],
    )
    result.rows_out = len(clean)
    return result


def load_sources(raw_dir: Path, config: CleaningConfig
                 ) -> tuple[list[tuple[io_utils.LoadedTable, SourceRules]], list[str]]:
    """Load only files the config knows about, using the encoding it pins."""
    pairs: list[tuple[io_utils.LoadedTable, SourceRules]] = []
    problems: list[str] = []
    for path in io_utils.discover_sources(raw_dir):
        skipped = config.skip_reason(path.stem)
        if skipped is not None:
            problems.append(path.name + SKIPPED_MARKER + skipped)
            continue
        spec = config.for_table(path.stem)
        if spec is None:
            problems.append(path.name + UNCONFIGURED_MARKER)
            continue
        try:
            for t in io_utils.load_file(path, encoding=spec.encoding):
                pairs.append((t, spec))
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            problems.append(path.name + ": " + type(exc).__name__ + ": " + str(exc))
    return pairs, problems


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(results: list[CleanTable], problems: list[str]) -> str:
    out: list[str] = ["# Cleaning report", ""]
    out.append("Generated by `python -m dtp.cli clean`. Policy: "
               "`config/cleaning_rules.yml`.")
    out.append("")
    out.append("| table | rows in | rows out | cols out | rejected cells | steps |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for r in results:
        out.append("| " + " | ".join([
            r.table, f"{r.rows_in:,}", f"{r.rows_out:,}",
            str(r.df.shape[1]), f"{r.n_rejects:,}", str(len(r.actions)),
        ]) + " |")
    out.append("")

    if problems:
        out += ["## Files not cleaned", ""]
        out += ["- " + p for p in problems] + [""]

    for r in results:
        out += ["## " + r.table, ""]
        if r.spec:
            out.append("- source: `" + r.name + "` (" + str(r.spec.encoding) + ")")
            if r.spec.grain:
                out.append("- grain: " + r.spec.grain)
            if r.spec.key_columns:
                out.append("- key: " + ", ".join("`" + k + "`" for k in r.spec.key_columns))
        out.append("- rows " + f"{r.rows_in:,}" + " -> " + f"{r.rows_out:,}"
                   + ", columns " + str(r.cols_in) + " -> " + str(r.df.shape[1]))
        out.append("")

        if r.warnings:
            out += ["**Warnings**", ""]
            out += ["- " + _md_escape(w) for w in r.warnings] + [""]

        # Steps with no rationale are mechanical; the ones with a `reason:` are
        # the decisions a reviewer actually needs to sign off on, so they go first.
        decided = [a for a in r.actions if a.reason]
        if decided:
            out += ["**Decisions and rationale**", "",
                    "| step | target | n | rationale |", "|---|---|---:|---|"]
            for a in decided:
                out.append("| " + " | ".join([
                    a.step, "`" + a.target + "`", f"{a.n:,}", _md_escape(a.reason or ""),
                ]) + " |")
            out.append("")

        mechanical = [a for a in r.actions if not a.reason and a.n]
        if mechanical:
            out += ["**Mechanical changes**", "",
                    "| step | target | n | detail |", "|---|---|---:|---|"]
            for a in mechanical:
                out.append("| " + " | ".join([
                    a.step, "`" + a.target + "`", f"{a.n:,}", _md_escape(a.detail),
                ]) + " |")
            out.append("")

        out += ["**Output schema**", "", "| column | dtype | non-null | nulls |",
                "|---|---|---:|---:|"]
        for col in r.df.columns:
            s = r.df[col]
            out.append("| " + " | ".join([
                "`" + str(col) + "`", str(s.dtype),
                f"{int(s.notna().sum()):,}", f"{int(s.isna().sum()):,}",
            ]) + " |")
        out.append("")

        if r.n_rejects:
            out += ["**Quarantined cells** (first 20 of " + f"{r.n_rejects:,}" + ")", "",
                    "| row key | column | declared type | raw value |", "|---|---|---|---|"]
            for _, row in r.rejects.head(20).iterrows():
                out.append("| " + " | ".join([
                    str(row["row_key"]), "`" + str(row["column"]) + "`",
                    str(row["declared_type"]), "`" + _md_escape(str(row["raw_value"])) + "`",
                ]) + " |")
            out.append("")
        if r.spec and r.spec.join_notes:
            out += ["**Join notes**", "", r.spec.join_notes, ""]
    return "\n".join(out) + "\n"


def to_payload(results: list[CleanTable], problems: list[str]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "problems": problems,
        "tables": [
            {
                "source": r.name,
                "table": r.table,
                "rows_in": r.rows_in,
                "rows_out": r.rows_out,
                "cols_in": r.cols_in,
                "cols_out": int(r.df.shape[1]),
                "rejected_cells": r.n_rejects,
                "warnings": r.warnings,
                "counts_by_step": r.counts_by_step(),
                "dtypes": {str(c): str(t) for c, t in r.df.dtypes.items()},
                "actions": [
                    {"step": a.step, "target": a.target, "detail": a.detail,
                     "n": a.n, "reason": a.reason}
                    for a in r.actions
                ],
            }
            for r in results
        ],
    }


def run(raw_dir: Path | None = None, out_dir: Path | None = None,
        config_path: Path | None = None, write_parquet: bool = True,
        reports_dir: Path | None = None
        ) -> tuple[list[CleanTable], list[str], dict[str, Path]]:
    """Clean every configured source, write Parquet plus the report."""
    raw_dir = raw_dir or RAW_DIR
    out_dir = out_dir or CLEAN_DIR
    reports_dir = reports_dir or REPORTS_DIR
    config = load_config(config_path)
    pairs, problems = load_sources(raw_dir, config)

    results = [clean_table(t, spec, config.defaults) for t, spec in pairs]

    paths: dict[str, Path] = {}
    if write_parquet and results:
        out_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            p = out_dir / (r.table + ".parquet")
            r.df.to_parquet(p, index=False)
            paths["parquet:" + r.table] = p
            if r.n_rejects:
                q = out_dir / (r.table + "__rejects.parquet")
                r.rejects.to_parquet(q, index=False)
                paths["rejects:" + r.table] = q

    reports_dir.mkdir(parents=True, exist_ok=True)
    md = reports_dir / "cleaning-report.md"
    js = reports_dir / "cleaning-report.json"
    md.write_text(render_markdown(results, problems), encoding="utf-8")
    js.write_text(json.dumps(to_payload(results, problems), indent=2,
                             default=str), encoding="utf-8")
    paths["markdown"], paths["json"] = md, js
    return results, problems, paths
