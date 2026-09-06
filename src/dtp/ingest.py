"""Universal Tabular Ingestion, Cleaning, and Profiling Engine.

Handles arbitrary CSV, TSV, and Excel (XLSX, XLS) tabular datasets:
1. Ingestion: Reads raw text/bytes with robust encoding & delimiter detection.
2. Cleaning: Strips cell whitespace, normalizes sentinel nulls, coerces numeric strings
   (e.g., currency, percentages, formatted numbers), and sanitizes column headers.
3. Profiling: Computes row/column dimensions, completeness/quality score, duplicate rows,
   candidate keys, and per-column value distributions.
4. Schema Discovery: Employs schema_discovery to classify columns into measures, dimensions,
   dates, IDs, and booleans.
5. Semantic Catalog & DuckDB: Builds a typed Catalog and registers an in-memory DuckDB Warehouse.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd

from . import warehouse
from .schema_discovery import DatasetSchema, discover_schema, create_catalog_from_schema

# Values that represent missing data but frequently arrive as text.
SENTINEL_NULLS = frozenset({
    "", "-", "--", "---", "?", "??", ".", "n/a", "n.a.", "na", "nan", "none", "null",
    "nil", "nd", "n/d", "#n/a", "#na", "#null!", "#div/0!", "#ref!", "#value!",
    "unknown", "unspecified", "undefined", "missing", "not available",
    "not applicable", "no data", "blank", "empty",
    "<na>", "<null>", "0000-00-00",
})


# Patterns for formatted numbers (currency, percentage, comma separators)
_CURRENCY_PATTERN = re.compile(r"^\s*[$€£¥₹₩]\s*[+-]?\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?\s*$|^\s*[+-]?\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?\s*[$€£¥₹₩]\s*$")
_PERCENT_PATTERN = re.compile(r"^\s*[+-]?\d+(?:\.\d+)?\s*%\s*$")
_COMMA_NUM_PATTERN = re.compile(r"^\s*[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*$")


@dataclass
class CleaningSummary:
    """Audit of cleaning actions performed on a dataset."""
    sentinel_nulls_replaced: int = 0
    whitespace_cells_trimmed: int = 0
    numeric_coercions: dict[str, str] = field(default_factory=dict)
    duplicate_rows: int = 0
    sanitized_columns: dict[str, str] = field(default_factory=dict)  # old -> new


@dataclass
class ColumnProfile:
    """Summary profile for a single column."""
    name: str
    dtype: str
    role: str
    missing_count: int
    missing_pct: float
    unique_count: int
    sample_values: list[str] = field(default_factory=list)


@dataclass
class DatasetProfile:
    """Comprehensive data quality and readiness profile for a dataset."""
    table_name: str
    filename: str
    n_rows: int
    n_cols: int
    n_cells: int
    n_missing_cells: int
    missing_pct: float
    quality_score: float
    n_duplicate_rows: int
    candidate_keys: list[str] = field(default_factory=list)
    columns: list[ColumnProfile] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return self.n_rows > 0 and self.n_cols > 0


@dataclass
class IngestionResult:
    """Result of end-to-end ingestion, cleaning, profiling, and DuckDB catalog loading."""
    warehouse: warehouse.Warehouse
    df: pd.DataFrame
    table_name: str
    filename: str
    profile: DatasetProfile
    schema: DatasetSchema
    cleaning: CleaningSummary
    warnings: list[str] = field(default_factory=list)


def sanitize_table_name(filename: str) -> str:
    """Derive a clean, safe SQL table identifier from a filename."""
    stem = Path(filename).stem.lower()
    cleaned = re.sub(r"[^a-z0-9_]+", "_", stem).strip("_")
    return cleaned or "dataset"


def detect_encoding_from_bytes(data: bytes) -> tuple[str, list[str]]:
    """Determine the encoding that cleanest decodes byte data."""
    warnings: list[str] = []
    candidates = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
    for enc in candidates:
        try:
            data.decode(enc)
            if enc != "utf-8":
                warnings.append(f"File decoded using {enc}")
            return enc, warnings
        except UnicodeDecodeError:
            continue
    warnings.append("No clean standard encoding found; decoded using latin-1 with character replacement")
    return "latin-1", warnings


def sniff_delimiter_from_text(sample: str, filename: str) -> tuple[str, list[str]]:
    """Sniff CSV delimiter from a text sample with suffix fallback."""
    warnings: list[str] = []
    suffix = Path(filename).suffix.lower()
    fallback = "\t" if suffix == ".tsv" else ("|" if suffix == ".psv" else ",")
    try:
        dialect = csv.Sniffer().sniff(sample[:32768], delimiters=",;\t|")
        return dialect.delimiter, warnings
    except csv.Error:
        warnings.append(f"Could not automatically sniff delimiter; assumed {fallback!r}")
        return fallback, warnings


def sanitize_column_headers(columns: list[Any]) -> tuple[list[str], dict[str, str]]:
    """Deduplicate and clean column header names."""
    sanitized: list[str] = []
    renamed: dict[str, str] = {}
    seen: dict[str, int] = {}

    for idx, c in enumerate(columns):
        raw = str(c).strip() if c is not None else ""
        if not raw or raw.lower().startswith("unnamed:"):
            name = f"column_{idx + 1}"
        else:
            name = re.sub(r"\s+", " ", raw)

        key = name.lower()
        if key in seen:
            seen[key] += 1
            unique_name = f"{name}__{seen[key]}"
            renamed[raw] = unique_name
            sanitized.append(unique_name)
        else:
            seen[key] = 0
            if raw != name:
                renamed[raw] = name
            sanitized.append(name)

    return sanitized, renamed


def clean_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, CleaningSummary]:
    """Clean DataFrame: strip whitespace, replace sentinel nulls, coerce formatted numbers."""
    cleaned = df.copy()
    summary = CleaningSummary()

    # 1. Sanitize column headers
    new_cols, renamed = sanitize_column_headers(list(cleaned.columns))
    cleaned.columns = new_cols
    summary.sanitized_columns = renamed

    # 2. Duplicate rows audit
    summary.duplicate_rows = int(cleaned.duplicated().sum())

    # 3. Clean object / string columns
    for col in cleaned.columns:
        series = cleaned[col]
        if series.dtype == object or pd.api.types.is_string_dtype(series):
            # Trim whitespace and replace sentinel nulls
            def clean_val(v: Any) -> Any:
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    return np.nan
                if isinstance(v, str):
                    s = v.strip()
                    if len(s) != len(v):
                        summary.whitespace_cells_trimmed += 1
                    if s.lower() in SENTINEL_NULLS:
                        summary.sentinel_nulls_replaced += 1
                        return np.nan
                    return s
                return v

            cleaned[col] = series.map(clean_val)

            # Check if this object column is a formatted numeric column
            non_null = cleaned[col].dropna()
            if len(non_null) > 0 and len(non_null) >= min(len(cleaned) * 0.3, 5):
                str_sample = non_null.astype(str)
                # Check currency
                is_currency = str_sample.map(lambda s: bool(_CURRENCY_PATTERN.match(s)))
                if is_currency.sum() / len(str_sample) >= 0.8:
                    coerced = (
                        str_sample
                        .str.replace(r"[$€£¥₹₩,\s]", "", regex=True)
                    )
                    try:
                        num_series = pd.to_numeric(coerced, errors="coerce")
                        if num_series.notna().sum() / len(str_sample) >= 0.8:
                            cleaned[col] = pd.to_numeric(
                                cleaned[col].astype(str).str.replace(r"[$€£¥₹₩,\s]", "", regex=True),
                                errors="coerce"
                            )
                            summary.numeric_coercions[col] = "Cleaned currency notation to float"
                            continue
                    except Exception:
                        pass

                # Check percentage
                is_pct = str_sample.map(lambda s: bool(_PERCENT_PATTERN.match(s)))
                if is_pct.sum() / len(str_sample) >= 0.8:
                    coerced = str_sample.str.replace("%", "", regex=False).str.strip()
                    try:
                        num_series = pd.to_numeric(coerced, errors="coerce")
                        if num_series.notna().sum() / len(str_sample) >= 0.8:
                            cleaned[col] = pd.to_numeric(
                                cleaned[col].astype(str).str.replace("%", "", regex=False).str.strip(),
                                errors="coerce"
                            )
                            summary.numeric_coercions[col] = "Cleaned percentage notation to float"
                            continue
                    except Exception:
                        pass

                # Check comma-separated integers or floats
                is_comma_num = str_sample.map(lambda s: bool(_COMMA_NUM_PATTERN.match(s)))
                if is_comma_num.sum() / len(str_sample) >= 0.8:
                    coerced = str_sample.str.replace(",", "", regex=False).str.strip()
                    try:
                        num_series = pd.to_numeric(coerced, errors="coerce")
                        if num_series.notna().sum() / len(str_sample) >= 0.8:
                            cleaned[col] = pd.to_numeric(
                                cleaned[col].astype(str).str.replace(",", "", regex=False).str.strip(),
                                errors="coerce"
                            )
                            summary.numeric_coercions[col] = "Cleaned comma thousand-separators to numeric"
                            continue
                    except Exception:
                        pass

    return cleaned, summary


def profile_dataset(
    df: pd.DataFrame,
    table_name: str,
    filename: str,
    schema: DatasetSchema,
    cleaning: CleaningSummary,
) -> DatasetProfile:
    """Generate comprehensive quality and semantic profile for the cleaned dataset."""
    n_rows, n_cols = df.shape
    n_cells = n_rows * n_cols
    n_missing_cells = int(df.isna().sum().sum())
    missing_pct = round((n_missing_cells / max(n_cells, 1)) * 100, 2)
    quality_score = round(max(0.0, 100.0 - missing_pct), 1)

    column_profiles: list[ColumnProfile] = []
    candidate_keys: list[str] = []

    for col in df.columns:
        series = df[col]
        missing_cnt = int(series.isna().sum())
        col_missing_pct = round((missing_cnt / max(n_rows, 1)) * 100, 2)
        unique_cnt = int(series.nunique())
        role = schema.column_types.get(col, "dimension")

        # Key candidate check
        if missing_cnt == 0 and unique_cnt == n_rows and n_rows > 1:
            candidate_keys.append(col)

        # Sample values
        non_null_samples = series.dropna().head(3).astype(str).tolist()

        column_profiles.append(
            ColumnProfile(
                name=str(col),
                dtype=str(series.dtype),
                role=role,
                missing_count=missing_cnt,
                missing_pct=col_missing_pct,
                unique_count=unique_cnt,
                sample_values=non_null_samples,
            )
        )

    return DatasetProfile(
        table_name=table_name,
        filename=filename,
        n_rows=n_rows,
        n_cols=n_cols,
        n_cells=n_cells,
        n_missing_cells=n_missing_cells,
        missing_pct=missing_pct,
        quality_score=quality_score,
        n_duplicate_rows=cleaning.duplicate_rows,
        candidate_keys=candidate_keys,
        columns=column_profiles,
    )


def ingest_tabular(
    source: Any,
    filename: str,
    sheet_name: str | None = None,
) -> IngestionResult:
    """End-to-end ingestion pipeline:
    
    1. Reads tabular file (CSV / TSV / Excel) from file path, bytes, buffer, or DataFrame.
    2. Cleans whitespace, sentinel nulls, and formatted numbers.
    3. Performs automated schema discovery.
    4. Profiles quality, missing values, duplicates, and column roles.
    5. Builds semantic catalog and mounts into DuckDB in-memory session.
    """
    warnings: list[str] = []
    table_name = sanitize_table_name(filename)

    # 1. Ingest into raw DataFrame
    if isinstance(source, pd.DataFrame):
        raw_df = source
    else:
        suffix = Path(filename).suffix.lower()
        if suffix in (".xlsx", ".xls"):
            # Excel
            try:
                raw_df = pd.read_excel(source, sheet_name=sheet_name or 0, na_filter=False)
            except Exception as exc:
                raise ValueError(f"Failed to read Excel workbook {filename}: {exc}") from exc
        else:
            # Delimited (CSV / TSV / PSV / TXT)
            if isinstance(source, (str, Path)):
                path = Path(source)
                raw_bytes = path.read_bytes()
            elif hasattr(source, "read"):
                if hasattr(source, "seek"):
                    source.seek(0)
                raw_bytes = source.read()
                if isinstance(raw_bytes, str):
                    raw_bytes = raw_bytes.encode("utf-8")
                if hasattr(source, "seek"):
                    source.seek(0)
            elif isinstance(source, bytes):
                raw_bytes = source
            else:
                raise TypeError(f"Unsupported data source type: {type(source)}")

            encoding, enc_warns = detect_encoding_from_bytes(raw_bytes)
            warnings.extend(enc_warns)

            text_sample = raw_bytes[:65536].decode(encoding, errors="replace")
            delimiter, delim_warns = sniff_delimiter_from_text(text_sample, filename)
            warnings.extend(delim_warns)

            try:
                raw_df = pd.read_csv(
                    io.BytesIO(raw_bytes),
                    sep=delimiter,
                    encoding=encoding,
                    encoding_errors="replace",
                    low_memory=False,
                )
            except Exception as exc:
                raise ValueError(f"Failed to parse delimited file {filename}: {exc}") from exc

    # 2. Clean
    cleaned_df, cleaning = clean_dataframe(raw_df)
    if cleaning.sentinel_nulls_replaced > 0:
        warnings.append(f"Normalized {cleaning.sentinel_nulls_replaced} sentinel null cells")
    if cleaning.whitespace_cells_trimmed > 0:
        warnings.append(f"Trimmed whitespace across {cleaning.whitespace_cells_trimmed} cells")
    if cleaning.numeric_coercions:
        for c, note in cleaning.numeric_coercions.items():
            warnings.append(f"Column '{c}': {note}")
    if cleaning.duplicate_rows > 0:
        warnings.append(f"Dataset contains {cleaning.duplicate_rows} duplicate rows")

    # 3. Discover Schema
    schema = discover_schema(cleaned_df, table_name=table_name)

    # 4. Profile Dataset
    profile = profile_dataset(
        cleaned_df,
        table_name=table_name,
        filename=filename,
        schema=schema,
        cleaning=cleaning,
    )

    # 5. Build Semantic Catalog
    catalog = create_catalog_from_schema(schema)

    # 6. Mount into DuckDB Warehouse
    wh = warehouse.Warehouse.from_df(cleaned_df, name=table_name)
    # Ensure catalog is explicitly assigned
    wh.catalog = catalog
    # Attach profile and schema to warehouse instance
    wh.profile = profile  # type: ignore[attr-defined]
    wh.schema = schema    # type: ignore[attr-defined]

    return IngestionResult(
        warehouse=wh,
        df=cleaned_df,
        table_name=table_name,
        filename=filename,
        profile=profile,
        schema=schema,
        cleaning=cleaning,
        warnings=warnings,
    )
