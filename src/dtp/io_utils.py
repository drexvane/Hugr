"""Format-agnostic source loading (Phase 1.1).

Design rule: raw files are loaded as TEXT wherever the format allows it.
Letting pandas infer types on ingest silently destroys the evidence this
project exists to find - "01/02/2024" vs "2024-02-01", "1,200" vs "1200",
"N/A" vs "" vs "-". Type inference is an explicit, logged cleaning step
(see clean.py), never a side effect of reading a file.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

TEXTUAL_SUFFIXES = {".csv", ".tsv", ".txt", ".psv"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
PARQUET_SUFFIXES = {".parquet", ".pq"}
SUPPORTED = TEXTUAL_SUFFIXES | EXCEL_SUFFIXES | JSON_SUFFIXES | PARQUET_SUFFIXES

ENCODING_CANDIDATES = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


@dataclass
class LoadedTable:
    """One logical table plus everything we learned while reading it."""

    name: str
    df: pd.DataFrame
    source_path: Path
    source_format: str
    sheet: str | None = None
    encoding: str | None = None
    delimiter: str | None = None
    loaded_as_text: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def shape(self) -> tuple[int, int]:
        return self.df.shape


def detect_encoding(path: Path) -> tuple[str, list[str]]:
    """First candidate encoding that decodes the whole file. Never raises."""
    warnings: list[str] = []
    raw = path.read_bytes()
    for enc in ENCODING_CANDIDATES:
        try:
            raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc != "utf-8":
            warnings.append("file is not utf-8; decoded as " + enc)
        return enc, warnings
    warnings.append("no clean encoding found; decoded as latin-1 with replacement")
    return "latin-1", warnings


def sniff_delimiter(path: Path, encoding: str) -> tuple[str, list[str]]:
    """csv.Sniffer on a sample, with a suffix-based fallback."""
    warnings: list[str] = []
    fallback = {".tsv": "\t", ".psv": "|"}.get(path.suffix.lower(), ",")
    try:
        with path.open("r", encoding=encoding, errors="replace", newline="") as fh:
            sample = fh.read(64 * 1024)
        if not sample.strip():
            return fallback, ["file appears empty"]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter, warnings
    except csv.Error:
        warnings.append("could not sniff delimiter; assumed " + repr(fallback))
        return fallback, warnings


def _dedupe_columns(cols: list[str]) -> tuple[list[str], list[str]]:
    """pandas mangles duplicate headers silently; surface it instead."""
    seen: dict[str, int] = {}
    out: list[str] = []
    warnings: list[str] = []
    for c in cols:
        key = str(c)
        if key in seen:
            seen[key] += 1
            new = key + "__dup" + str(seen[key])
            warnings.append("duplicate header " + repr(key) + " renamed to " + repr(new))
            out.append(new)
        else:
            seen[key] = 0
            out.append(key)
    return out, warnings


def _finalise(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    cols, warnings = _dedupe_columns(list(df.columns))
    df.columns = cols
    return df, warnings


def load_delimited(path: Path) -> list[LoadedTable]:
    encoding, warns = detect_encoding(path)
    delimiter, dwarns = sniff_delimiter(path, encoding)
    warns = warns + dwarns
    df = pd.read_csv(
        path,
        sep=delimiter,
        encoding=encoding,
        encoding_errors="replace",
        dtype=str,              # keep raw text - see module docstring
        keep_default_na=False,  # "NA"/"NULL"/"" stay verbatim for profiling
        na_values=[],
        skip_blank_lines=False,
        low_memory=False,
    )
    df, cwarns = _finalise(df)
    return [
        LoadedTable(
            name=path.stem,
            df=df,
            source_path=path,
            source_format=path.suffix.lstrip("."),
            encoding=encoding,
            delimiter=delimiter,
            warnings=warns + cwarns,
        )
    ]


def load_excel(path: Path) -> list[LoadedTable]:
    """Every sheet becomes its own table - workbooks routinely hide extras."""
    book = pd.read_excel(path, sheet_name=None, dtype=str, na_filter=False)
    tables: list[LoadedTable] = []
    for sheet, df in book.items():
        df, cwarns = _finalise(df)
        tables.append(
            LoadedTable(
                name=(path.stem + "__" + str(sheet)) if len(book) > 1 else path.stem,
                df=df,
                source_path=path,
                source_format=path.suffix.lstrip("."),
                sheet=str(sheet),
                warnings=cwarns,
            )
        )
    if len(book) > 1:
        note = "workbook contains " + str(len(book)) + " sheets: " + str(list(book))
        for t in tables:
            t.warnings.append(note)
    return tables


def load_json(path: Path) -> list[LoadedTable]:
    encoding, warns = detect_encoding(path)
    text = path.read_text(encoding=encoding, errors="replace")
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    else:
        payload = json.loads(text) if text.strip() else []
        if isinstance(payload, dict):
            # Single object, or a wrapper like {"records": [...]}.
            lists = {k: v for k, v in payload.items() if isinstance(v, list)}
            if lists:
                key = max(lists, key=lambda k: len(lists[k]))
                rows = lists[key]
                warns.append("took records from top-level key " + repr(key))
            else:
                rows = [payload]
        else:
            rows = payload
    df = pd.json_normalize(rows, sep=".")  # flatten nested objects
    if not df.empty:
        df = df.astype("string")           # match the text-first rule
    df, cwarns = _finalise(df)
    return [
        LoadedTable(
            name=path.stem,
            df=df,
            source_path=path,
            source_format=path.suffix.lstrip("."),
            encoding=encoding,
            warnings=warns + cwarns,
        )
    ]


def load_parquet(path: Path) -> list[LoadedTable]:
    df = pd.read_parquet(path)
    df, cwarns = _finalise(df)
    return [
        LoadedTable(
            name=path.stem,
            df=df,
            source_path=path,
            source_format="parquet",
            loaded_as_text=False,
            warnings=cwarns + ["parquet carries its own dtypes; not loaded as text"],
        )
    ]


def load_file(path: Path) -> list[LoadedTable]:
    suffix = path.suffix.lower()
    if suffix in TEXTUAL_SUFFIXES:
        return load_delimited(path)
    if suffix in EXCEL_SUFFIXES:
        return load_excel(path)
    if suffix in JSON_SUFFIXES:
        return load_json(path)
    if suffix in PARQUET_SUFFIXES:
        return load_parquet(path)
    raise ValueError("unsupported format " + repr(suffix) + " for " + path.name)


def discover_sources(raw_dir: Path) -> list[Path]:
    """Supported files under raw_dir, sorted; dotfiles and Office temp files skipped."""
    if not raw_dir.exists():
        return []
    return sorted(
        p
        for p in raw_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED
        and not p.name.startswith((".", "~$"))
    )


def load_all(raw_dir: Path) -> tuple[list[LoadedTable], list[str]]:
    """Load every discoverable source. A bad file is recorded, not fatal."""
    tables: list[LoadedTable] = []
    errors: list[str] = []
    for path in discover_sources(raw_dir):
        try:
            tables.extend(load_file(path))
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            errors.append(path.name + ": " + type(exc).__name__ + ": " + str(exc))
    return tables, errors
