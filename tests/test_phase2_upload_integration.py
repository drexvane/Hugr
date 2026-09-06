"""Tests for Phase 2 — Upload Integration.

Verifies the end-to-end ingestion lifecycle on real tabular data:
- Upload -> Ingestion -> Cleaning -> Profiling -> Schema Discovery -> Catalog -> DuckDB
- Both CSV and Excel (.xlsx) formats
- Sentinel null normalization, whitespace trimming, and numeric coercion
- Quality score and readiness profiling
- Dynamic querying over newly ingested warehouse
- Dashboard backward compatibility
"""

import io
from pathlib import Path
import pandas as pd
import pytest

from dtp import ingest
from dtp.dashboard import style
from dtp.agent import Session
from dtp.agent.client import KeywordModel

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_datasets"


def test_real_csv_upload_lifecycle():
    """Verify complete upload lifecycle for a real CSV dataset."""
    csv_path = SAMPLE_DIR / "ecommerce_orders.csv"
    assert csv_path.exists(), f"Missing real sample dataset: {csv_path}"

    result = ingest.ingest_tabular(csv_path, csv_path.name)

    # 1. Ingestion & dimensions
    assert result.filename == "ecommerce_orders.csv"
    assert result.table_name == "ecommerce_orders"
    assert result.profile.is_ready
    assert result.profile.n_rows == 1500
    assert result.profile.n_cols == 6

    # 2. Cleaning & Profiling
    assert result.profile.quality_score >= 90.0
    assert len(result.profile.columns) == 6

    # 3. Schema Discovery
    assert len(result.schema.measure_columns) > 0
    assert len(result.schema.dimension_columns) > 0

    # 4. Catalog
    cat = result.warehouse.catalog
    assert cat is not None
    assert "row_count" in cat.metrics
    # e.g., quantity or item_price measures
    assert any(k.startswith("sum_") for k in cat.metrics)

    # 5. DuckDB Execution
    rows = result.warehouse.sql(f"SELECT count(*) as cnt FROM {result.table_name}")
    assert rows.iloc[0]["cnt"] == 1500


def test_real_excel_upload_lifecycle():
    """Verify complete upload lifecycle for an Excel (.xlsx) dataset with messy elements."""
    df_raw = pd.DataFrame({
        "order_id": [101, 102, 103, 104, 105],
        "customer": [" Alice ", "Bob", "Charlie", "David", "Eve"],
        "price": ["$12.50", "$25.00", "$9.99", "$15.00", "$100.00"],
        "discount_rate": ["10%", "15%", "5%", "0%", "20%"],
        "status": ["Completed", "Pending", "N/A", "Completed", "?"],
        "quantity": [1, 2, 1, 4, 2],
    })

    excel_buffer = io.BytesIO()
    df_raw.to_excel(excel_buffer, index=False, engine="openpyxl")
    excel_buffer.seek(0)

    result = ingest.ingest_tabular(excel_buffer, "sales_orders.xlsx")

    assert result.table_name == "sales_orders"
    assert result.profile.n_rows == 5
    assert result.profile.n_cols == 6

    # Verify cleaning: price and discount_rate coerced to numeric
    assert pd.api.types.is_numeric_dtype(result.df["price"])
    assert pd.api.types.is_numeric_dtype(result.df["discount_rate"])

    # Verify sentinel nulls ("N/A", "?") replaced with NaN
    assert result.df["status"].isna().sum() == 2
    assert result.cleaning.sentinel_nulls_replaced == 2

    # Verify whitespace trimmed
    assert result.df.loc[0, "customer"] == "Alice"

    # Verify DuckDB queryable
    rows = result.warehouse.sql("SELECT sum(price) as total_rev FROM sales_orders")
    assert round(rows.iloc[0]["total_rev"], 2) == round(12.50 + 25.00 + 9.99 + 15.00 + 100.00, 2)


def test_clean_dataframe_sentinels_and_whitespace():
    """Verify cleaning engine handles various sentinels and messy whitespace without destroying valid values."""
    messy_df = pd.DataFrame({
        "category": ["  Electronics  ", "Furniture", "null", "Toys", "UNKNOWN"],
        "rating": ["4.5", "na", "3.8", "none", "5.0"],
        "sales": ["$1,200.50", "$350.00", "$4,500.00", "$80.00", "$999.99"],
    })

    cleaned, summary = ingest.clean_dataframe(messy_df)

    # Whitespace trimmed
    assert cleaned.loc[0, "category"] == "Electronics"

    # Sentinel nulls replaced
    assert pd.isna(cleaned.loc[2, "category"])
    assert pd.isna(cleaned.loc[4, "category"])
    assert pd.isna(cleaned.loc[1, "rating"])
    assert pd.isna(cleaned.loc[3, "rating"])
    assert summary.sentinel_nulls_replaced >= 4

    # Currency and commas coerced to numeric float
    assert pd.api.types.is_numeric_dtype(cleaned["sales"])
    assert cleaned.loc[0, "sales"] == 1200.50


def test_profiling_and_readiness_metrics():
    """Verify dataset profiling accurately computes quality score, candidate keys, and roles."""
    df = pd.DataFrame({
        "employee_id": [1, 2, 3, 4],
        "department": ["Eng", "Sales", "Eng", "HR"],
        "salary": [100000, 80000, 110000, 75000],
        "notes": ["Good", None, "Senior", None],
    })

    result = ingest.ingest_tabular(df, "employees.csv")
    profile = result.profile

    assert profile.n_rows == 4
    assert profile.n_cols == 4
    assert profile.n_cells == 16
    assert profile.n_missing_cells == 2
    # 2/16 missing -> 87.5% quality score
    assert profile.quality_score == 87.5
    assert "employee_id" in profile.candidate_keys


def test_active_dataset_querying():
    """Verify the active ingested dataset is immediately queryable via AI Agent session."""
    csv_path = SAMPLE_DIR / "education_students.csv"
    result = ingest.ingest_tabular(csv_path, csv_path.name)

    model = KeywordModel(catalog=result.warehouse.catalog)
    session = Session(result.warehouse, model, result.warehouse.version_id)

    # Ask a question matching discovered measures
    answer = session.ask("average age by sex")
    assert answer.refusal is None
    assert answer.plan is not None
    assert len(answer.frame) > 0



def test_render_dataset_readiness_smoke():
    """Verify style.render_dataset_readiness executes cleanly without exceptions."""
    csv_path = SAMPLE_DIR / "healthcare_heart.csv"
    result = ingest.ingest_tabular(csv_path, csv_path.name)

    # Should execute cleanly without error
    style.render_dataset_readiness(result)
