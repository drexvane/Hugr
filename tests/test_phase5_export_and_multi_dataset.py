"""Tests for Phase 5: Export, Sharing & Multi-Dataset Synthesis.

Covers:
- Executive Analysis Report generation (Markdown & standalone HTML)
- Data extraction & CSV/JSON export
- Multi-dataset ingestion into unified DuckDB sessions
- Cross-dataset candidate join discovery
- Streamlit AppTest harness verification with export drawer
"""

import io
import json
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dtp import export, ingest, versioning, warehouse
from dtp.agent import Session, client
from dtp.dashboard import style
from dtp.warehouse import Warehouse

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


@pytest.fixture
def sample_answer():
    """Create a real answer from ecommerce_orders.csv for report export testing."""
    df = pd.read_csv("data/sample_datasets/ecommerce_orders.csv")
    wh = Warehouse.from_df(df, name="ecommerce_orders")
    session = Session(wh, client.KeywordModel(), wh.version_id)
    prompts = style.get_starter_prompts(wh)
    ans = session.ask(prompts[0])
    return ans, wh


def test_markdown_report_generation(sample_answer):
    ans, wh = sample_answer
    report_md = export.generate_markdown_report(ans, wh, dataset_name="E-Commerce Orders")

    assert "# ✦ Hugr Executive Data Intelligence Report" in report_md
    assert "E-Commerce Orders" in report_md
    assert "## 1. Executive Summary" in report_md
    assert "## 2. Key Metrics & Performance Indicators" in report_md
    assert "## 6. Execution Plan & Data Provenance" in report_md
    assert "```json" in report_md
    # Summary text from answer is included
    assert ans.summary in report_md


def test_html_report_generation(sample_answer):
    ans, wh = sample_answer
    report_html = export.generate_html_report(ans, wh, dataset_name="E-Commerce Orders")

    assert "<!DOCTYPE html>" in report_html
    assert "Hugr Intelligence Report" in report_html
    assert "E-Commerce Orders" in report_html
    assert "Key Performance Indicators" in report_html
    assert "data-table" in report_html
    assert "100% Mathematically Verified" in report_html


def test_dataframe_export_csv_and_json():
    df = pd.DataFrame({
        "product": ["Widget A", "Widget B", "Widget C"],
        "revenue": [1200.50, 850.00, 430.25],
        "units": [12, 8, 4],
    })

    csv_bytes = export.export_dataframe_to_csv(df)
    assert isinstance(csv_bytes, bytes)
    roundtrip_df = pd.read_csv(io.BytesIO(csv_bytes))
    assert len(roundtrip_df) == 3
    assert list(roundtrip_df.columns) == ["product", "revenue", "units"]

    json_str = export.export_dataframe_to_json(df)
    assert isinstance(json_str, str)
    records = json.loads(json_str)
    assert len(records) == 3
    assert records[0]["product"] == "Widget A"
    assert records[0]["revenue"] == 1200.50


def test_multi_table_ingestion_and_duckdb_mount():
    # Ingest two real tabular datasets
    csv1_path = Path("data/sample_datasets/ecommerce_orders.csv")
    csv2_path = Path("data/_synthetic/sales_orders.csv")

    sources = [
        (csv1_path.read_bytes(), "ecommerce_orders.csv"),
        (csv2_path.read_bytes(), "sales_orders.csv"),
    ]

    multi_res = ingest.ingest_multiple_tabular(sources)

    assert len(multi_res.tables) == 2
    assert "ecommerce_orders" in multi_res.tables
    assert "sales_orders" in multi_res.tables

    # Both tables must be queryable in DuckDB
    wh = multi_res.warehouse
    df1 = wh.sql("SELECT count(*) AS n FROM ecommerce_orders")
    assert df1.iat[0, 0] > 0

    df2 = wh.sql("SELECT count(*) AS n FROM sales_orders")
    assert df2.iat[0, 0] > 0


def test_candidate_join_discovery_between_tables():
    # Two tables with shared entity key
    df_orders = pd.DataFrame({
        "order_id": [101, 102, 103, 104],
        "customer_id": ["C1", "C2", "C3", "C4"],
        "amount": [50.0, 80.0, 120.0, 95.0],
    })
    df_customers = pd.DataFrame({
        "customer_id": ["C1", "C2", "C3", "C5"],
        "customer_name": ["Alice", "Bob", "Charlie", "David"],
        "city": ["New York", "Chicago", "Seattle", "Austin"],
    })

    wh = Warehouse.from_tables({
        "orders": df_orders,
        "customers": df_customers,
    })

    joins = export.find_candidate_joins(wh)
    assert len(joins) > 0
    top_join = joins[0]

    # Must identify customer_id join with high confidence
    assert "customer_id" in top_join.column_a
    assert "customer_id" in top_join.column_b
    assert top_join.confidence >= 0.8
    assert top_join.overlap_ratio >= 0.7


def test_app_test_harness_phase5_export_drawer(snapshot_dir, monkeypatch):
    """Verify via AppTest that asking a question renders the export report drawer with download buttons."""
    monkeypatch.setattr(warehouse, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(versioning, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(client, "api_key", lambda: None)
    st.cache_resource.clear()
    st.cache_data.clear()

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    assert not at.exception

    # Select Ask screen
    at = at.sidebar.radio[0].set_value("ask").run()
    assert not at.exception

    # Ask query
    at.text_input[0].set_value("revenue by market")
    at.button[0].click().run()
    assert not at.exception

    # Verify download buttons exist for report exports in at.main
    dl_buttons = [e for e in at.main if e.type == "download_button"]
    assert len(dl_buttons) >= 2
    dl_labels = [b.label for b in dl_buttons]
    assert any("Markdown" in l for l in dl_labels)
    assert any("HTML" in l for l in dl_labels)
