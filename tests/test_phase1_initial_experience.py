"""Tests for Phase 1 — Initial Experience.

Validates:
- Cipher branding, aesthetic tokens, and CSS injection
- Dynamic dataset status pill and hero introduction
- Dynamic starter prompts derived from dataset schema (zero hardcoded assumptions)
- Capability cards for initial empty state
- CSV / tabular ingestion into DuckDB in-memory session
- AppTest harness verification of the Ask screen layout
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from dtp import warehouse, versioning
from dtp.agent import Session, client
from dtp.dashboard import style
from dtp.warehouse import Warehouse

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


@pytest.fixture
def app(snapshot_dir, monkeypatch):
    monkeypatch.setattr(warehouse, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(versioning, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(client, "api_key", lambda: None)
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    return at


@pytest.fixture
def asking(app):
    at = app.sidebar.radio[0].set_value("ask").run()
    return at


def test_custom_css_contains_design_system_tokens():
    css = style.HUGR_CSS
    assert "#090d16" in css  # Canvas background
    assert "Outfit" in css  # Heading font
    assert "Inter" in css  # Body font
    assert "cipher-header" in css
    assert "cipher-glyph" in css
    assert "cipher-wordmark" in css
    assert "cipher-cards-grid" in css
    assert "cipher-dataset-pill" in css


def test_starter_prompts_dynamically_derived_from_schema():
    # Synthetic dataframe with custom domain columns
    df = pd.DataFrame({
        "employee_dept": ["Engineering", "Product", "Design"] * 10,
        "base_salary": [120000.0, 110000.0, 95000.0] * 10,
        "bonus_amt": [15000.0, 12000.0, 8000.0] * 10,
    })
    wh = Warehouse.from_df(df, name="staffing_table")
    prompts = style.get_starter_prompts(wh)

    assert len(prompts) > 0
    # Prompts must refer to real columns in the dataset
    combined = " ".join(prompts).lower()
    assert "base salary" in combined or "bonus amt" in combined
    assert "employee dept" in combined


def test_starter_prompts_fallback_to_retail_examples_when_no_catalog():
    class DummyWarehouse:
        catalog = None

    prompts = style.get_starter_prompts(DummyWarehouse())
    from dtp.agent.session import EXAMPLES
    assert prompts == EXAMPLES[:4]


def test_csv_ingestion_and_dynamic_agent_ask():
    # Test real CSV file ingestion from data/sample_datasets
    df = pd.read_csv("data/sample_datasets/ecommerce_orders.csv")
    wh = Warehouse.from_df(df, name="ecommerce_orders")
    session = Session(wh, client.KeywordModel(), wh.version_id)

    prompts = style.get_starter_prompts(wh)
    assert len(prompts) > 0
    first_q = prompts[0]

    answer = session.ask(first_q)
    assert answer.ok is True
    assert answer.figure is not None or len(answer.tiles) > 0
    assert answer.summary is not None
    assert not answer.withheld  # Zero hallucination


def test_app_ask_screen_initial_experience_layout(asking):
    """Verify via Streamlit AppTest that Ask screen boots with proper layout & controls."""
    assert not asking.exception

    # Exactly 1 question input
    assert len(asking.text_input) == 1

    # Exactly 1 action button before asking ("Ask")
    assert [b.label for b in asking.button] == ["Ask"]

    # At least one caption with "Try:" dynamic starter prompts
    assert any("Try:" in c.value for c in asking.caption)

    # No metrics or warnings before query is asked
    assert asking.metric.values == []
    assert asking.warning.values == []


def test_app_boots_cleanly_without_snapshots(tmp_path, monkeypatch):
    """Verify that Cipher launches gracefully into Universal Ingestion mode even if data/versions is empty."""
    monkeypatch.setattr(warehouse, "VERSIONS_DIR", tmp_path)
    monkeypatch.setattr(versioning, "VERSIONS_DIR", tmp_path)
    monkeypatch.setattr(client, "api_key", lambda: None)
    st.cache_resource.clear()
    st.cache_data.clear()
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    assert not at.exception
    assert len(at.text_input) == 1
    assert [b.label for b in at.button] == ["Ask"]
    assert "Cipher" in at.sidebar.title[0].value

