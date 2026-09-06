"""Tests for Phase 3 — Conversational Analytics & Exploration.

Verifies:
- Dark theme styling applied to Plotly figures with high-contrast typography
- Dynamic follow-up query suggestions derived from active plan & catalog
- Multi-turn conversational memory and plan patching on real uploaded datasets
- Verification badge & hallucination guard UI components
- Seamless rendering through Streamlit AppTest harness
"""

from pathlib import Path
import pytest
import plotly.graph_objects as go

from dtp import ingest
from dtp.dashboard import style
from dtp.agent import Session, Plan
from dtp.agent.client import KeywordModel

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_datasets"


def test_dark_theme_figure_styling():
    """Verify that apply_dark_theme_to_figure applies dark canvas, muted grids, and custom typography."""
    fig = go.Figure(data=[go.Bar(x=["A", "B"], y=[10, 20])])
    fig = style.apply_dark_theme_to_figure(fig)

    layout = fig.layout
    assert layout.paper_bgcolor == "rgba(0,0,0,0)"
    assert layout.plot_bgcolor == "rgba(15, 23, 42, 0.4)"
    assert "Inter" in layout.font.family
    assert "Outfit" in layout.title.font.family
    assert layout.xaxis.gridcolor == "rgba(255, 255, 255, 0.08)"
    assert layout.yaxis.gridcolor == "rgba(255, 255, 255, 0.08)"


def test_dynamic_follow_up_suggestions():
    """Verify dynamic follow-up suggestions derived from active query plan and dataset catalog."""
    csv_path = SAMPLE_DIR / "education_students.csv"
    result = ingest.ingest_tabular(csv_path, csv_path.name)
    wh = result.warehouse

    plan = Plan(
        metrics=("avg_age",),
        by=("sex",),
        grain=None,
        where=(),
        order_by=None,
        limit=None,
    )

    suggestions = style.get_follow_up_suggestions(plan, wh)
    assert len(suggestions) > 0
    # Should suggest alternative dimension or ranking
    assert any("break down by" in s or "by" in s or "top 5" in s for s in suggestions)


def test_multi_turn_conversational_memory_on_uploaded_dataset():
    """Verify multi-turn session memory correctly patches plans and preserves history on an uploaded dataset."""
    csv_path = SAMPLE_DIR / "ecommerce_orders.csv"
    result = ingest.ingest_tabular(csv_path, csv_path.name)
    wh = result.warehouse

    model = KeywordModel(catalog=wh.catalog)
    session = Session(wh, model, wh.version_id)

    # Turn 1: Initial query
    ans1 = session.ask("total quantity by item name")
    assert ans1.ok
    assert len(session.log) == 1
    assert "sum_quantity" in ans1.plan.metrics
    assert "item_name" in ans1.plan.by

    # Turn 2: Follow-up patch
    ans2 = session.ask("by choice description")
    assert ans2.ok
    assert len(session.log) == 2
    # Metric preserved from Turn 1
    assert "sum_quantity" in ans2.plan.metrics
    # Dimension updated to choice_description
    assert "choice_description" in ans2.plan.by
    # Both turns remain in session history
    assert session.log[0].question == "total quantity by item name"
    assert session.log[1].question == "by choice description"


def test_verification_badge_and_chips_smoke():
    """Verify verification badge and follow-up chip renderers execute cleanly."""
    style.render_verification_badge("claude-sonnet-5")
    style.render_follow_up_chips(["break down by category", "top 5", "over time"])


def test_apptest_multi_turn_experience(snapshot_dir: Path, monkeypatch):
    """Verify full interactive dashboard multi-turn flow with dark theme and follow-up suggestions."""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    import streamlit as st
    from dtp import versioning, warehouse
    from dtp.agent import client

    APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"

    monkeypatch.setattr(warehouse, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(versioning, "VERSIONS_DIR", snapshot_dir)
    monkeypatch.setattr(client, "api_key", lambda: None)

    st.cache_resource.clear()
    st.cache_data.clear()

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()

    # Navigate to Ask screen
    at.sidebar.radio[0].set_value("ask").run()
    assert at.sidebar.radio[0].value == "ask"

    # Turn 1
    at.text_input[0].set_value("revenue by market")
    at.button[0].click().run()

    assert [m.label for m in at.metric] == ["Revenue"]
    assert len(at.json) == 1

    # Turn 2: Follow-up
    at.text_input[0].set_value("by category")
    at.button[0].click().run()

    # Two turns rendered
    assert len(at.json) == 2
    # Start over button visible
    assert any(b.label == "Start over" for b in at.button)
