"""Styling and visual components for the Hugr AI Data Analyst interface.

Implements the modern, minimal, refined visual direction specified in UI_DESIGN_SPEC.md:
- Deep dark slate/zinc background (#090d16) with subtle glowing gradients
- Glassmorphic card surfaces with soft elevation and fine borders
- Distinctive Hugr branding and glowing status indicators
- Intentional initial composition that avoids emptiness while maintaining restraint
- Dynamic starter prompts derived from the active dataset's schema
"""

from __future__ import annotations

import re
from typing import Any
import streamlit as st


HUGR_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700&display=swap');

/* Global resets & typography */
html, body, [class*="css"], [class*="st-"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Background canvas */
.stApp {
    background-color: #090d16;
    background-image: 
        radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.08) 0px, transparent 50%),
        radial-gradient(at 100% 0%, rgba(139, 92, 246, 0.06) 0px, transparent 50%),
        radial-gradient(at 50% 100%, rgba(6, 182, 212, 0.05) 0px, transparent 50%);
    background-attachment: fixed;
    color: #f1f5f9;
}

/* Top brand navigation bar */
.hugr-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1.25rem 0 1.75rem 0;
    margin-bottom: 0.5rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.hugr-logo-container {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}

.hugr-glyph {
    font-size: 1.6rem;
    background: linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #06b6d4 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    filter: drop-shadow(0 0 12px rgba(99, 102, 241, 0.5));
}

.hugr-wordmark {
    font-family: 'Outfit', sans-serif;
    font-size: 1.75rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.hugr-badge {
    font-size: 0.72rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 0.25rem 0.65rem;
    border-radius: 9999px;
    background: rgba(99, 102, 241, 0.12);
    color: #a5b4fc;
    border: 1px solid rgba(99, 102, 241, 0.25);
}

/* Active Dataset Banner / Pill */
.hugr-dataset-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.6rem;
    background: rgba(15, 23, 42, 0.65);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 9999px;
    padding: 0.35rem 0.9rem;
    font-size: 0.82rem;
    color: #94a3b8;
    margin-bottom: 1.25rem;
}

.hugr-status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background-color: #10b981;
    box-shadow: 0 0 8px rgba(16, 185, 129, 0.7);
}

.hugr-dataset-name {
    color: #f1f5f9;
    font-weight: 600;
}

/* Hero Section */
.hugr-hero {
    text-align: center;
    max-width: 680px;
    margin: 1.25rem auto 2rem auto;
}

.hugr-hero-title {
    font-family: 'Outfit', sans-serif;
    font-size: 2.25rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1.2;
    margin-bottom: 0.6rem;
    background: linear-gradient(135deg, #ffffff 0%, #94a3b8 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.hugr-hero-subtitle {
    font-size: 1.05rem;
    color: #64748b;
    line-height: 1.5;
}

/* Refined Question Input styling */
div[data-testid="stForm"] {
    background: rgba(15, 23, 42, 0.75);
    backdrop-filter: blur(16px);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 16px;
    padding: 0.85rem 1rem;
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    transition: all 0.2s ease;
}

div[data-testid="stForm"]:focus-within {
    border-color: rgba(99, 102, 241, 0.5);
    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15), 0 8px 32px 0 rgba(0, 0, 0, 0.45);
}

div[data-testid="stTextInput"] input {
    background: transparent !important;
    border: none !important;
    color: #f8fafc !important;
    font-size: 1.1rem !important;
    padding: 0.6rem 0.2rem !important;
}

div[data-testid="stTextInput"] input::placeholder {
    color: #64748b !important;
}

/* File Uploader styling */
div[data-testid="stFileUploader"] {
    background: rgba(15, 23, 42, 0.45);
    backdrop-filter: blur(8px);
    border: 1px dashed rgba(255, 255, 255, 0.12);
    border-radius: 12px;
    padding: 0.5rem 0.75rem;
    margin: 1rem 0;
    transition: all 0.2s ease;
}

div[data-testid="stFileUploader"]:hover {
    border-color: rgba(99, 102, 241, 0.4);
    background: rgba(15, 23, 42, 0.65);
}

/* Welcome Cards Grid */
.hugr-cards-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1.25rem;
    margin: 2.2rem 0;
}

@media (max-width: 768px) {
    .hugr-cards-grid {
        grid-template-columns: 1fr;
    }
}

.hugr-card {
    background: rgba(15, 23, 42, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
    padding: 1.4rem;
    transition: transform 0.2s ease, border-color 0.2s ease;
}

.hugr-card:hover {
    transform: translateY(-2px);
    border-color: rgba(99, 102, 241, 0.35);
}

.hugr-card-icon {
    font-size: 1.4rem;
    margin-bottom: 0.75rem;
}

.hugr-card-title {
    font-family: 'Outfit', sans-serif;
    font-size: 1.05rem;
    font-weight: 600;
    color: #f8fafc;
    margin-bottom: 0.35rem;
}

.hugr-card-desc {
    font-size: 0.85rem;
    color: #64748b;
    line-height: 1.45;
}

/* Metric Tiles card styling */
div[data-testid="stMetric"] {
    background: rgba(15, 23, 42, 0.55);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 1rem 1.2rem;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
}

div[data-testid="stMetricLabel"] {
    font-size: 0.8rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8 !important;
}

div[data-testid="stMetricValue"] {
    font-family: 'Outfit', sans-serif !important;
    font-size: 1.7rem !important;
    font-weight: 600 !important;
    color: #f8fafc !important;
}
</style>
"""


def inject_custom_css() -> None:
    """Inject Hugr modern aesthetic stylesheet into Streamlit page."""
    st.markdown(HUGR_CSS, unsafe_allow_html=True)


def render_brand_header() -> None:
    """Render top brand navigation bar."""
    st.markdown(
        """
        <div class="hugr-header">
            <div class="hugr-logo-container">
                <span class="hugr-glyph">✦</span>
                <span class="hugr-wordmark">Hugr</span>
                <span class="hugr-badge">Universal AI Data Analyst</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_hero_intro() -> None:
    """Render clean central hero introducing the interaction."""
    st.markdown(
        """
        <div class="hugr-hero">
            <div class="hugr-hero-title">Ask anything about your data</div>
            <div class="hugr-hero-subtitle">
                Upload any CSV or Excel file. Hugr automatically discovers columns, 
                measures, and trends, returning verified visual insights.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_dataset_pill(dataset_name: str, row_count: int, col_count: int) -> None:
    """Render clean status pill indicating the active dataset."""
    st.markdown(
        f"""
        <div class="hugr-dataset-pill">
            <span class="hugr-status-dot"></span>
            <span>Active Dataset:</span>
            <span class="hugr-dataset-name">{dataset_name}</span>
            <span>·</span>
            <span>{row_count:,} rows</span>
            <span>·</span>
            <span>{col_count} columns</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_initial_cards() -> None:
    """Render the 3 intentional capability cards when no questions have been asked yet."""
    st.markdown(
        """
        <div class="hugr-cards-grid">
            <div class="hugr-card">
                <div class="hugr-card-icon">💬</div>
                <div class="hugr-card-title">Ask Naturally</div>
                <div class="hugr-card-desc">
                    Inquire about totals, averages, rankings, or distributions without writing SQL or building manual charts.
                </div>
            </div>
            <div class="hugr-card">
                <div class="hugr-card-icon">⚡</div>
                <div class="hugr-card-title">Dynamic Discovery</div>
                <div class="hugr-card-desc">
                    Automatic column classification detects numeric measures, categorical dimensions, and temporal grains in seconds.
                </div>
            </div>
            <div class="hugr-card">
                <div class="hugr-card-icon">🛡️</div>
                <div class="hugr-card-title">Hallucination Guard</div>
                <div class="hugr-card-desc">
                    Every number and trend in the generated answer is strictly cross-verified against actual DuckDB execution data.
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_starter_prompts(wh: Any) -> list[str]:
    """Generate dynamic starter questions grounded directly in the active dataset schema."""
    cat = getattr(wh, "catalog", None)
    if cat is not None and getattr(cat, "metrics", None):
        prompts: list[str] = []
        measures = [k for k in cat.metrics.keys() if k.startswith("sum_")]
        dims = list(cat.dimensions.keys()) if getattr(cat, "dimensions", None) else []
        time_grains = getattr(cat, "time_grains", {})

        # 1. Total <measure> by <dim>
        if measures and dims:
            m_name = measures[0].replace("sum_", "").replace("_", " ")
            d_name = dims[0].replace("_", " ")
            prompts.append(f"total {m_name} by {d_name}")

        # 2. Average <measure> by <dim>
        avg_measures = [k for k in cat.metrics.keys() if k.startswith("avg_")]
        if avg_measures and len(dims) > 1:
            m_name = avg_measures[0].replace("avg_", "").replace("_", " ")
            d_name = dims[1].replace("_", " ")
            prompts.append(f"average {m_name} by {d_name}")
        elif avg_measures and dims:
            m_name = avg_measures[0].replace("avg_", "").replace("_", " ")
            d_name = dims[0].replace("_", " ")
            prompts.append(f"average {m_name} by {d_name}")

        # 3. Top ranking
        if measures and dims:
            m_name = measures[0].replace("sum_", "").replace("_", " ")
            d_name = dims[0].replace("_", " ")
            prompts.append(f"top 5 {d_name} by {m_name}")

        # 4. Over time if available
        if measures and time_grains:
            m_name = measures[0].replace("sum_", "").replace("_", " ")
            prompts.append(f"{m_name} over time")

        if prompts:
            return prompts[:4]

    from dtp.agent.session import EXAMPLES
    return EXAMPLES[:4]
