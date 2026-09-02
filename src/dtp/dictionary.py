"""Data dictionary (Phase 1.3).

The roadmap asks for a dictionary covering 100% of fields. "100%" is only a
claim worth making if something checks it, so coverage here is computed and
`Dictionary.gaps` is the list of fields that fall short. The CLI exits non-zero
when that list is non-empty.

Three sources are merged, in order of authority:

  1. The vendor's own glossary (`DescriptionDataCoSupplyChain.csv`) - what the
     field is supposed to mean. Describes 52 of the 53 raw columns and gets at
     least one of them wrong, so it is a starting point, not the truth.
  2. `config/cleaning_rules.yml` - what we decided to do about it and why. The
     `reason:` fields written during Phase 1.2 are the real documentation.
  3. The clean Parquet - what is actually in there: dtype, nulls, cardinality,
     range, examples. Measured at build time so the numbers cannot go stale
     without the document changing.

Nothing is invented. A field with no vendor description and no `reason:` shows
up as a gap rather than being given a plausible-sounding sentence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from . import CLEAN_DIR, DOCS_DIR, RAW_DIR, REPORTS_DIR
from . import clean as clean_mod
from . import validate as validate_mod

GLOSSARY_STEM = "DescriptionDataCoSupplyChain"
EXAMPLE_COUNT = 3
# Long free-text and identifier columns: listing examples is noise, and for
# names and streets it is needless personal data in a document meant to be read.
NO_EXAMPLES = frozenset({"customer_first_name", "customer_last_name",
                         "customer_street", "product_image_url", "request_url",
                         "client_ip"})


@dataclass
class Field:
    table: str
    name: str
    dtype: str
    source_column: str | None = None
    kind: str = "cleaned"          # cleaned | derived
    declared_type: str | None = None
    role: str | None = None
    business_description: str | None = None
    vendor_described: bool = False  # true when the vendor glossary names it
    description_source: str | None = None   # vendor | config
    decision: str | None = None    # our rationale, from cleaning_rules.yml
    expression: str | None = None  # derived fields only
    transform: str | None = None
    null_policy: str | None = None
    rows: int = 0
    nulls: int = 0
    distinct: int = 0
    minimum: str | None = None
    maximum: str | None = None
    examples: list[str] = field(default_factory=list)
    values: list[str] | None = None      # full vocabulary, when small
    rules: list[str] = field(default_factory=list)

    @property
    def null_pct(self) -> float:
        return (self.nulls / self.rows * 100) if self.rows else 0.0

    @property
    def documented(self) -> bool:
        """A field counts as documented if a human can learn what it is."""
        return bool(self.business_description or self.decision or self.expression)


@dataclass
class Dictionary:
    fields: list[Field] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    grain: dict[str, str] = field(default_factory=dict)
    join_notes: dict[str, str] = field(default_factory=dict)
    glossary_unused: list[str] = field(default_factory=list)
    glossary_echoes: list[str] = field(default_factory=list)

    @property
    def gaps(self) -> list[Field]:
        return [f for f in self.fields if not f.documented]

    @property
    def coverage(self) -> float:
        if not self.fields:
            return 0.0
        return (len(self.fields) - len(self.gaps)) / len(self.fields) * 100

    def tables(self) -> list[str]:
        seen: list[str] = []
        for f in self.fields:
            if f.table not in seen:
                seen.append(f.table)
        return seen

    def for_table(self, table: str) -> list[Field]:
        return [f for f in self.fields if f.table == table]


# --------------------------------------------------------------------------- #
# the vendor glossary
# --------------------------------------------------------------------------- #

def load_glossary(raw_dir: Path | None = None) -> dict[str, str]:
    """Read the vendor's field descriptions, keyed by casefolded source name.

    The file is cp1252 like its sibling, its FIELDS column carries trailing
    padding, and every description starts with ": ". All three are the vendor's
    formatting rather than data, so all three are stripped here.
    """
    raw_dir = raw_dir or RAW_DIR
    path = next((p for p in raw_dir.glob("*") if p.stem == GLOSSARY_STEM), None)
    if path is None:
        return {}
    df = pd.read_csv(path, encoding="cp1252", dtype=str,
                     keep_default_na=False, na_values=[])
    cols = {c.strip().casefold(): c for c in df.columns}
    fcol, dcol = cols.get("fields"), cols.get("description")
    if not fcol or not dcol:
        return {}
    out: dict[str, str] = {}
    for name, desc in zip(df[fcol], df[dcol], strict=False):
        key = str(name).strip().casefold()
        text = re.sub(r"^\s*:\s*", "", str(desc).strip())
        text = re.sub(r"\s+", " ", text).strip()
        if key and text:
            out[key] = text[0].upper() + text[1:]
    return out


# --------------------------------------------------------------------------- #
# measurement
# --------------------------------------------------------------------------- #

def _fmt(value: Any) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, float):
        return format(round(value, 6), "g")
    return str(value)


def measure(df: pd.DataFrame, column: str) -> dict[str, Any]:
    s = df[column]
    stats: dict[str, Any] = {
        "rows": int(len(s)),
        "nulls": int(s.isna().sum()),
        "distinct": int(s.nunique(dropna=True)),
    }
    present = s.dropna()
    if not present.empty:
        # Ordered types get a range; unordered ones would give a meaningless
        # alphabetical min, so they are left out rather than filled in.
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
            stats["minimum"] = _fmt(present.min())
            stats["maximum"] = _fmt(present.max())
        if column not in NO_EXAMPLES:
            top = present.value_counts().head(EXAMPLE_COUNT)
            stats["examples"] = [_fmt(v) for v in top.index]
        # A small vocabulary is more useful printed in full than sampled.
        if stats["distinct"] <= 12 and not pd.api.types.is_float_dtype(s):
            stats["values"] = sorted(_fmt(v) or "" for v in present.unique())
    return stats


def _named_columns(raw: dict[str, Any]) -> set[str]:
    """Columns a rule names structurally, as opposed to inside an expression."""
    out: set[str] = set()
    for key in ("column", "columns", "determinant", "dependent"):
        value = raw.get(key)
        if isinstance(value, list):
            out.update(str(v) for v in value)
        elif value is not None:
            out.add(str(value))
    return out


def _rules_for(column: str, table: str,
               rules: dict[str, dict[str, Any]]) -> list[str]:
    """Which validation rules mention this column - the field's guarantees.

    Expression rules are matched on a word-boundary search of the expression
    text, so `total_equals_sales_minus_discount` shows up under all three of the
    columns it constrains rather than only where it happens to be filed.
    """
    cfg = rules.get(table) or {}
    word = re.compile(r"\b" + re.escape(column) + r"\b")
    hits: list[str] = []
    for raw in cfg.get("rules") or []:
        if not isinstance(raw, dict):
            continue
        text = " ".join(str(raw.get(k)) for k in ("expr", "lhs", "rhs")
                        if raw.get(k) is not None)
        if column in _named_columns(raw) or word.search(text):
            hits.append(str(raw.get("id", "?"))
                        + " (" + str(raw.get("severity", "error")) + ")")
    return hits


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #

def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


def _is_echo(source_column: str, description: str) -> bool:
    """True when the vendor's 'description' just restates the column name.

    Five entries in the glossary do this ("Product Price: Product Price"). They
    are counted as absent rather than as documentation, because a field whose
    only description is its own name is exactly the field a reader is stuck on.
    """
    return _norm(description) == _norm(source_column)


def build(tables: dict[str, pd.DataFrame],
          config: clean_mod.CleaningConfig,
          glossary: dict[str, str] | None = None,
          rules: dict[str, dict[str, Any]] | None = None) -> Dictionary:
    glossary = glossary or {}
    rules = rules or {}
    doc = Dictionary()
    used: set[str] = set()

    for table, df in tables.items():
        spec = config.for_table(table)
        if spec is None:
            # Documented as a gap, not skipped: an unconfigured clean table is a
            # hole in the dictionary and should read as one.
            for col in df.columns:
                doc.fields.append(Field(table=table, name=col,
                                        dtype=str(df[col].dtype),
                                        **measure(df, col)))
            continue

        if spec.grain:
            doc.grain[table] = spec.grain
        if spec.join_notes:
            doc.join_notes[table] = spec.join_notes
        for item in spec.drop:
            doc.dropped.append({"table": table, "source": item["source"],
                                "reason": item.get("reason", "")})

        by_clean = {c.clean: c for c in spec.columns}
        derived = {d.name: d for d in spec.derived}

        for col in df.columns:
            f = Field(table=table, name=col, dtype=str(df[col].dtype))
            if col in by_clean:
                rule = by_clean[col]
                key = rule.source.strip().casefold()
                used.add(key)
                f.source_column = rule.source
                f.declared_type = rule.type
                f.role = rule.role
                # Our own description wins where we wrote one: the vendor's
                # glossary is wrong about at least one column, and it does not
                # cover the access log at all.
                vendor = glossary.get(key)
                if vendor and _is_echo(rule.source, vendor):
                    doc.glossary_echoes.append(rule.source)
                    vendor = None
                f.business_description = rule.description or vendor
                f.vendor_described = vendor is not None
                if f.business_description:
                    f.description_source = "config" if rule.description else "vendor"
                f.decision = rule.reason
                f.transform = rule.transform
                f.null_policy = rule.nulls
            elif col in derived:
                d = derived[col]
                f.kind = "derived"
                f.declared_type = d.type
                f.expression = d.expr
                f.decision = d.reason
                f.null_policy = ("null when " + d.null_when) if d.null_when else None
            for k, v in measure(df, col).items():
                setattr(f, k, v)
            f.rules = _rules_for(col, table, rules)
            doc.fields.append(f)

    # Glossary entries nothing consumed: either a column we dropped (expected,
    # and the drop reason says why) or a name that no longer exists upstream.
    dropped_keys = {d["source"].strip().casefold() for d in doc.dropped}
    doc.glossary_unused = sorted(k for k in glossary
                                 if k not in used and k not in dropped_keys)
    return doc


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _md(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).replace("|", r"\|").strip()


def _summary(f: Field) -> str:
    """One cell that says what the field is, preferring our words to the vendor's."""
    if f.kind == "derived":
        return "Derived: `" + _md(f.expression) + "`"
    return _md(f.business_description) or _md(f.decision) or "**undocumented**"


def _overview_rows(fields: list[Field]) -> list[str]:
    rows = ["| field | type | source column | null % | distinct | description |",
            "| --- | --- | --- | --- | --- | --- |"]
    for f in fields:
        rows.append("| `" + f.name + "` | " + f.dtype
                    + " | " + ("`" + _md(f.source_column) + "`" if f.source_column
                               else ("_derived_" if f.kind == "derived" else "-"))
                    + " | " + format(f.null_pct, ".1f")
                    + " | " + format(f.distinct, ",")
                    + " | " + _summary(f) + " |")
    return rows


def _detail(f: Field) -> list[str]:
    lines = ["#### `" + f.name + "`", ""]
    facts = [("Type", f.dtype + (" (declared `" + f.declared_type + "`)"
                                 if f.declared_type else ""))]
    if f.source_column:
        facts.append(("Source column", "`" + _md(f.source_column) + "`"))
    if f.expression:
        facts.append(("Computed as", "`" + _md(f.expression) + "`"))
    if f.transform:
        facts.append(("Transform", "`" + f.transform + "`"))
    facts.append(("Rows", format(f.rows, ",")))
    facts.append(("Nulls", format(f.nulls, ",") + " ("
                  + format(f.null_pct, ".2f") + "%)"))
    facts.append(("Distinct", format(f.distinct, ",")))
    if f.minimum is not None or f.maximum is not None:
        facts.append(("Range", str(f.minimum) + " .. " + str(f.maximum)))
    if f.values:
        facts.append(("Values", ", ".join("`" + v + "`" for v in f.values)))
    elif f.examples:
        facts.append(("Most common", ", ".join("`" + str(v) + "`"
                                               for v in f.examples)))
    if f.null_policy:
        facts.append(("Null policy", "`" + _md(f.null_policy) + "`"))
    lines.extend("- " + k + ": " + v for k, v in facts)
    if f.business_description:
        label = ("_Vendor glossary:_ " if f.description_source == "vendor"
                 else "_Description:_ ")
        lines += ["", label + _md(f.business_description)]
    if f.decision:
        lines += ["", "_Our decision:_ " + _md(f.decision)]
    if f.rules:
        lines += ["", "_Guaranteed by:_ "
                  + ", ".join("`" + r + "`" for r in f.rules)]
    lines.append("")
    return lines


def render_markdown(doc: Dictionary) -> str:
    lines = [
        "# Data dictionary",
        "",
        "Generated by `dtp dict` from the clean Parquet, "
        "`config/cleaning_rules.yml` and the vendor glossary. Every number below "
        "is measured at build time - do not edit this file by hand.",
        "",
        "- Fields documented: " + str(len(doc.fields) - len(doc.gaps))
        + " of " + str(len(doc.fields))
        + " (" + format(doc.coverage, ".1f") + "%)",
        "- Descriptions: "
        + str(sum(1 for f in doc.fields if f.description_source == "vendor"))
        + " from the vendor glossary, "
        + str(sum(1 for f in doc.fields if f.description_source == "config"))
        + " written here, "
        + str(sum(1 for f in doc.fields if f.kind == "derived"))
        + " derived (defined by their expression)",
    ]
    if doc.gaps:
        lines.append("- **Undocumented: "
                     + ", ".join("`" + g.table + "." + g.name + "`"
                                 for g in doc.gaps) + "**")
    lines.append("")

    for table in doc.tables():
        fields = doc.for_table(table)
        lines += ["## " + table, ""]
        if table in doc.grain:
            lines += ["Grain: " + _md(doc.grain[table]), ""]
        lines += ["Rows: " + format(fields[0].rows if fields else 0, ",")
                  + " | Fields: " + str(len(fields)), ""]
        lines += _overview_rows(fields)
        lines.append("")
        if table in doc.join_notes:
            lines += ["### Joining " + table, "", _md(doc.join_notes[table]), ""]
        lines += ["### Field detail", ""]
        for f in fields:
            lines += _detail(f)

    dropped = [d for d in doc.dropped]
    if dropped:
        lines += ["## Columns deliberately not carried forward", "",
                  "| table | source column | reason |", "| --- | --- | --- |"]
        lines += ["| " + d["table"] + " | `" + _md(d["source"]) + "` | "
                  + _md(d["reason"]) + " |" for d in dropped]
        lines.append("")

    if doc.glossary_echoes:
        lines += ["## Glossary entries that describe nothing", "",
                  "The vendor's description for these columns restates the column "
                  "name, so it was treated as absent and a description written "
                  "here instead:", ""]
        lines += ["- `" + _md(k) + "`" for k in doc.glossary_echoes]
        lines.append("")

    if doc.glossary_unused:
        lines += ["## Glossary entries with no clean field", "",
                  "Present in the vendor's description file but not carried "
                  "forward and not listed as a drop - i.e. names that no longer "
                  "correspond to anything:", ""]
        lines += ["- `" + k + "`" for k in doc.glossary_unused]
        lines.append("")

    return "\n".join(lines) + "\n"


def to_payload(doc: Dictionary) -> dict[str, Any]:
    return {
        "fields_total": len(doc.fields),
        "fields_documented": len(doc.fields) - len(doc.gaps),
        "coverage_pct": round(doc.coverage, 2),
        "gaps": [g.table + "." + g.name for g in doc.gaps],
        "grain": doc.grain,
        "join_notes": doc.join_notes,
        "dropped": doc.dropped,
        "glossary_unused": doc.glossary_unused,
        "glossary_echoes": doc.glossary_echoes,
        "fields": [
            {
                "table": f.table,
                "name": f.name,
                "dtype": f.dtype,
                "kind": f.kind,
                "source_column": f.source_column,
                "declared_type": f.declared_type,
                "role": f.role,
                "business_description": f.business_description,
                "description_source": f.description_source,
                "decision": _md(f.decision) or None,
                "expression": f.expression,
                "transform": f.transform,
                "null_policy": f.null_policy,
                "rows": f.rows,
                "nulls": f.nulls,
                "null_pct": round(f.null_pct, 4),
                "distinct": f.distinct,
                "minimum": f.minimum,
                "maximum": f.maximum,
                "examples": f.examples,
                "values": f.values,
                "rules": f.rules,
                "documented": f.documented,
            }
            for f in doc.fields
        ],
    }


def run(clean_dir: Path | None = None, raw_dir: Path | None = None,
        config_path: Path | None = None, rules_path: Path | None = None,
        out_dir: Path | None = None,
        tables: dict[str, pd.DataFrame] | None = None,
        reports_dir: Path | None = None
        ) -> tuple[Dictionary, dict[str, Path]]:
    """Build the dictionary and write it to docs/ and reports/."""
    if tables is None:
        tables = validate_mod.load_clean_tables(clean_dir or CLEAN_DIR)
    doc = build(tables,
                clean_mod.load_config(config_path),
                load_glossary(raw_dir),
                validate_mod.load_rules(rules_path))

    out_dir = out_dir or DOCS_DIR
    reports_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "data-dictionary.md"
    js = reports_dir / "data-dictionary.json"
    md.write_text(render_markdown(doc), encoding="utf-8")
    js.write_text(json.dumps(to_payload(doc), indent=2, default=str),
                  encoding="utf-8")
    return doc, {"markdown": md, "json": js}





