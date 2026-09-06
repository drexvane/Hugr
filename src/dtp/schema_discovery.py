"""Dynamic schema discovery: universal detection of measures, dimensions, dates, IDs and booleans.

Transforms arbitrary tabular datasets into a typed semantic Catalog without requiring
any hardcoded column names or domain assumptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .metrics import Dimension, Metric, Catalog, COUNT, DAYS, MONEY, PERCENT, RATIO


# Heuristics for unit inference from column names
UNIT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (MONEY, re.compile(
        r"(sales|revenue|profit|price|cost|salary|wage|income|fee|amount|balance|"
        r"budget|gdp|spending|charge|fare|bonus|val|dollar|usd|eur|gbp)",
        re.IGNORECASE,
    )),
    (PERCENT, re.compile(
        r"(pct|percent|percentage|rate|ratio|margin|share|yield)",
        re.IGNORECASE,
    )),
    (DAYS, re.compile(
        r"(days|delay|duration|hours|minutes|seconds|time_spent|tenure|age)",
        re.IGNORECASE,
    )),
    (COUNT, re.compile(
        r"(qty|quantity|units|items|count|num_|number|absences|failures|calls|visits)",
        re.IGNORECASE,
    )),
]

ID_PATTERN = re.compile(r"(_id|^id$|uuid|code|_key|^key$|_ref|^ref$|ssn|invoice)", re.IGNORECASE)
DATE_PATTERN = re.compile(r"(date|time|timestamp|year|month|day|period|dt)", re.IGNORECASE)
BOOL_VOCABULARIES = (
    {"true", "false"},
    {"yes", "no"},
    {"y", "n"},
    {"t", "f"},
    {"0", "1"},
    {0, 1},
)


@dataclass
class DatasetSchema:
    """Discovered structural and semantic classification of a dataset."""

    table_name: str
    id_columns: list[str] = field(default_factory=list)
    time_columns: list[str] = field(default_factory=list)
    bool_columns: list[str] = field(default_factory=list)
    measure_columns: dict[str, str] = field(default_factory=dict)   # col -> unit
    dimension_columns: list[str] = field(default_factory=list)
    column_types: dict[str, str] = field(default_factory=dict)      # col -> inferred role

    def summary(self) -> dict[str, Any]:
        return {
            "table": self.table_name,
            "ids": self.id_columns,
            "time": self.time_columns,
            "booleans": self.bool_columns,
            "measures": self.measure_columns,
            "dimensions": self.dimension_columns,
        }


def infer_unit(col_name: str) -> str:
    """Infer the metric unit (money, percent, days, count, or measure) from column name."""
    for unit, pattern in UNIT_PATTERNS:
        if pattern.search(col_name):
            return unit
    return "measure"


def is_id_column(col_name: str, series: pd.Series) -> bool:
    """Check whether a column acts as an identifier rather than a groupable dimension."""
    clean_series = series.dropna()
    n_rows = len(clean_series)
    if n_rows == 0:
        return False
    n_unique = clean_series.nunique()
    
    # Explicit name pattern
    if ID_PATTERN.search(col_name):
        return True
    
    # Very high cardinality for string/int columns
    if n_rows > 30 and (n_unique / n_rows) > 0.95:
        return True
        
    return False


def is_boolean_column(series: pd.Series) -> bool:
    """Check whether a column represents boolean indicators."""
    if pd.api.types.is_bool_dtype(series):
        return True
    clean = series.dropna()
    if clean.empty:
        return False
    unique_vals = set(clean.unique())
    if len(unique_vals) <= 2:
        for vocab in BOOL_VOCABULARIES:
            if unique_vals.issubset(vocab):
                return True
            # Case-insensitive check for string sets
            str_vals = {str(v).lower() for v in unique_vals}
            if str_vals.issubset({str(w).lower() for w in vocab}):
                return True
    return False


def is_date_column(col_name: str, series: pd.Series) -> bool:
    """Detect if a column contains temporal/date data."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
        
    # Check column name heuristic
    if DATE_PATTERN.search(col_name):
        clean = series.dropna()
        if not clean.empty:
            # Try parsing a small sample
            sample = clean.head(20).astype(str)
            try:
                pd.to_datetime(sample, format="mixed", errors="raise")
                return True
            except Exception:
                pass

    return False


def discover_schema(df: pd.DataFrame, table_name: str = "dataset") -> DatasetSchema:
    """Classify every column in df into measures, dimensions, dates, IDs, and booleans."""
    schema = DatasetSchema(table_name=table_name)
    n_rows = len(df)

    for col in df.columns:
        col_name = str(col).strip()
        series = df[col]

        # 1. Date/Time Check
        if is_date_column(col_name, series):
            schema.time_columns.append(col_name)
            schema.column_types[col_name] = "time"
            continue

        # 2. Boolean Check
        if is_boolean_column(series):
            schema.bool_columns.append(col_name)
            schema.column_types[col_name] = "boolean"
            # Booleans can also be grouped by as dimensions
            schema.dimension_columns.append(col_name)
            continue

        # 3. ID Check
        if is_id_column(col_name, series):
            schema.id_columns.append(col_name)
            schema.column_types[col_name] = "id"
            continue

        # 4. Numeric Measures Check
        if pd.api.types.is_numeric_dtype(series):
            clean = series.dropna()
            n_unique = clean.nunique()
            # If low cardinality discrete integers (e.g. status code 1-4 or grade 1-5),
            # check if it makes more sense as a dimension or a measure
            if n_unique <= 6 and not any(p.search(col_name) for _, p in UNIT_PATTERNS):
                schema.dimension_columns.append(col_name)
                schema.column_types[col_name] = "dimension"
            else:
                unit = infer_unit(col_name)
                schema.measure_columns[col_name] = unit
                schema.column_types[col_name] = "measure"
            continue

        # 5. Categorical Dimensions (strings/objects)
        schema.dimension_columns.append(col_name)
        schema.column_types[col_name] = "dimension"

    return schema


def clean_label(col_name: str) -> str:
    """Convert snake_case or messy_column_name into Title Case label."""
    words = re.sub(r"[_\-]+", " ", col_name).split()
    return " ".join(w.capitalize() for w in words)


def create_catalog_from_schema(schema: DatasetSchema) -> Catalog:
    """Build a dynamic semantic Catalog from a discovered DatasetSchema."""
    metrics: dict[str, Metric] = {}
    dimensions: dict[str, Dimension] = {}
    drill_paths: dict[str, tuple[str, ...]] = {}

    table = schema.table_name

    # Baseline table metric
    metrics["row_count"] = Metric(
        key="row_count",
        label="Total Rows",
        expr="count(*)",
        unit=COUNT,
        about=f"Total record count in {table}.",
    )

    # Generate metrics for each measure
    for col, unit in schema.measure_columns.items():
        label = clean_label(col)
        # Sum
        metrics[f"sum_{col}"] = Metric(
            key=f"sum_{col}",
            label=f"Total {label}",
            expr=f'sum("{col}")',
            unit=unit,
            about=f"Sum of {label} across {table}.",
        )
        # Average
        metrics[f"avg_{col}"] = Metric(
            key=f"avg_{col}",
            label=f"Average {label}",
            expr=f'avg("{col}")',
            unit=unit if unit != COUNT else RATIO,
            about=f"Average of {label} across {table}.",
        )
        # Min & Max
        metrics[f"min_{col}"] = Metric(
            key=f"min_{col}",
            label=f"Min {label}",
            expr=f'min("{col}")',
            unit=unit,
            about=f"Minimum {label} across {table}.",
        )
        metrics[f"max_{col}"] = Metric(
            key=f"max_{col}",
            label=f"Max {label}",
            expr=f'max("{col}")',
            unit=unit,
            about=f"Maximum {label} across {table}.",
        )

    # Distinct counts for ID columns
    for col in schema.id_columns:
        label = clean_label(col)
        metrics[f"distinct_{col}"] = Metric(
            key=f"distinct_{col}",
            label=f"Distinct {label}",
            expr=f'count(DISTINCT "{col}")',
            unit=COUNT,
            about=f"Number of distinct {label} in {table}.",
        )

    # Generate Dimensions
    for col in schema.dimension_columns:
        label = clean_label(col)
        dimensions[col] = Dimension(
            key=col,
            label=label,
            expr=f'"{col}"',
            table=table,
        )

    # Generate Time Dimensions if present
    time_grains: list[str] = []
    for col in schema.time_columns:
        label = clean_label(col)
        for grain in ("year", "quarter", "month", "day"):
            key = f"{col}_{grain}" if len(schema.time_columns) > 1 else grain
            dimensions[key] = Dimension(
                key=key,
                label=f"{label} ({grain.capitalize()})",
                expr=f"date_trunc('{grain}', cast(\"{col}\" as TIMESTAMP))",
                table=table,
            )
            time_grains.append(key)

    if time_grains:
        drill_paths["time"] = tuple(time_grains)

    return Catalog(
        name=schema.table_name,
        metrics=metrics,
        dimensions=dimensions,
        drill_paths=drill_paths,
        schema=schema,
        primary_date_col=schema.time_columns[0] if schema.time_columns else None,
    )
