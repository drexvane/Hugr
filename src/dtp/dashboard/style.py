"""Styling and visual components for the Cipher AI Data Analyst interface.

Implements the modern, minimal, refined visual direction specified in UI_DESIGN_SPEC.md:
- Deep dark slate/zinc background (#090d16) with subtle glowing gradients
- Glassmorphic card surfaces with soft elevation and fine borders
- Distinctive Cipher branding and glowing status indicators
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
.cipher-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1.25rem 0 1.75rem 0;
    margin-bottom: 0.5rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.cipher-logo-container {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}

.cipher-glyph {
    font-size: 1.6rem;
    background: linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #06b6d4 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    filter: drop-shadow(0 0 12px rgba(99, 102, 241, 0.5));
}

.cipher-wordmark {
    font-family: 'Outfit', sans-serif;
    font-size: 1.75rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.cipher-badge {
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
.cipher-dataset-pill {
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

.cipher-status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background-color: #10b981;
    box-shadow: 0 0 8px rgba(16, 185, 129, 0.7);
}

.cipher-dataset-name {
    color: #f1f5f9;
    font-weight: 600;
}

/* Hero Section */
.cipher-hero {
    text-align: center;
    max-width: 680px;
    margin: 1.25rem auto 2rem auto;
}

.cipher-hero-title {
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

.cipher-hero-subtitle {
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
.cipher-cards-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1.25rem;
    margin: 2.2rem 0;
}

@media (max-width: 768px) {
    .cipher-cards-grid {
        grid-template-columns: 1fr;
    }
}

.cipher-card {
    background: rgba(15, 23, 42, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
    padding: 1.4rem;
    transition: transform 0.2s ease, border-color 0.2s ease;
}

.cipher-card:hover {
    transform: translateY(-2px);
    border-color: rgba(99, 102, 241, 0.35);
}

.cipher-card-icon {
    font-size: 1.4rem;
    margin-bottom: 0.75rem;
}

.cipher-card-title {
    font-family: 'Outfit', sans-serif;
    font-size: 1.05rem;
    font-weight: 600;
    color: #f8fafc;
    margin-bottom: 0.35rem;
}

.cipher-card-desc {
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

/* Dataset Readiness Card */
.cipher-readiness-card {
    background: rgba(15, 23, 42, 0.65);
    backdrop-filter: blur(14px);
    border: 1px solid rgba(16, 185, 129, 0.2);
    border-radius: 14px;
    padding: 1.1rem 1.3rem;
    margin: 1rem 0 1.25rem 0;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
}

.cipher-readiness-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.85rem;
    padding-bottom: 0.6rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.cipher-readiness-status {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #34d399;
}

.cipher-quality-badge {
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.2rem 0.6rem;
    border-radius: 9999px;
    background: rgba(16, 185, 129, 0.12);
    color: #6ee7b7;
    border: 1px solid rgba(16, 185, 129, 0.25);
}

.cipher-readiness-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.75rem;
}

@media (max-width: 640px) {
    .cipher-readiness-grid {
        grid-template-columns: repeat(2, 1fr);
    }
}

.cipher-readiness-stat {
    display: flex;
    flex-direction: column;
}

.cipher-stat-num {
    font-family: 'Outfit', sans-serif;
    font-size: 1.35rem;
    font-weight: 600;
    color: #f8fafc;
}

.cipher-stat-label {
    font-size: 0.75rem;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

.cipher-tag-container {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin: 0.4rem 0;
}

.cipher-tag {
    font-size: 0.78rem;
    padding: 0.2rem 0.55rem;
    border-radius: 6px;
    background: rgba(30, 41, 59, 0.7);
    color: #cbd5e1;
    border: 1px solid rgba(255, 255, 255, 0.08);
}

.cipher-tag-measure {
    border-color: rgba(99, 102, 241, 0.3);
    color: #c7d2fe;
}

.cipher-tag-dim {
    border-color: rgba(6, 182, 212, 0.3);
    color: #a5f3fc;
}

/* Phase 3: Conversational Analytics & Exploration */
.cipher-chips-container {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin: 0.8rem 0 1.2rem 0;
}

.cipher-chips-label {
    font-size: 0.78rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8;
    margin-right: 0.25rem;
}

.cipher-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.82rem;
    padding: 0.28rem 0.75rem;
    border-radius: 9999px;
    background: rgba(30, 41, 59, 0.65);
    border: 1px solid rgba(99, 102, 241, 0.25);
    color: #c7d2fe;
    transition: all 0.2s ease;
}

.cipher-chip:hover {
    background: rgba(99, 102, 241, 0.15);
    border-color: rgba(99, 102, 241, 0.5);
    color: #ffffff;
}

.cipher-verification-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.76rem;
    font-weight: 500;
    padding: 0.25rem 0.65rem;
    border-radius: 6px;
    background: rgba(16, 185, 129, 0.1);
    color: #6ee7b7;
    border: 1px solid rgba(16, 185, 129, 0.2);
    margin: 0.5rem 0;
}

.cipher-turn-badge {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    background: rgba(99, 102, 241, 0.15);
    color: #a5b4fc;
    border: 1px solid rgba(99, 102, 241, 0.25);
}

.cipher-insight-card {
    background: rgba(15, 23, 42, 0.5);
    backdrop-filter: blur(12px);
    border-left: 3px solid #6366f1;
    border-radius: 0 10px 10px 0;
    padding: 0.85rem 1.1rem;
    margin: 0.8rem 0;
    color: #f1f5f9;
}

/* Phase 4: Drilldowns & Anomaly Detection */
.cipher-anomaly-alert {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.35);
    border-radius: 10px;
    padding: 0.85rem 1.1rem;
    margin: 0.8rem 0;
    color: #fef3c7;
    font-size: 0.88rem;
}

.cipher-anomaly-icon {
    font-size: 1.25rem;
    color: #f59e0b;
}

.cipher-narrative-card {
    background: rgba(15, 23, 42, 0.65);
    backdrop-filter: blur(14px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    margin: 1rem 0;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
}

.cipher-narrative-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.82rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #a5b4fc;
    margin-bottom: 0.65rem;
    padding-bottom: 0.45rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.cipher-narrative-item {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    font-size: 0.88rem;
    color: #cbd5e1;
    margin: 0.4rem 0;
    line-height: 1.45;
}

.cipher-drilldown-container {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin: 0.6rem 0 1rem 0;
}

.cipher-drilldown-label {
    font-size: 0.78rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #67e8f9;
    margin-right: 0.25rem;
}

.cipher-drilldown-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.82rem;
    padding: 0.3rem 0.8rem;
    border-radius: 9999px;
    background: rgba(6, 182, 212, 0.12);
    border: 1px solid rgba(6, 182, 212, 0.35);
    color: #a5f3fc;
    transition: all 0.2s ease;
}

/* Phase 5: Export, Sharing & Multi-Dataset Synthesis */
.cipher-export-box {
    background: rgba(15, 23, 42, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    padding: 0.85rem 1rem;
    margin: 0.8rem 0;
}

.cipher-join-card {
    background: rgba(15, 23, 42, 0.65);
    backdrop-filter: blur(14px);
    border: 1px solid rgba(99, 102, 241, 0.25);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    margin: 0.9rem 0;
}

.cipher-join-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.82rem;
    font-weight: 600;
    padding: 0.25rem 0.65rem;
    background: rgba(99, 102, 241, 0.15);
    border: 1px solid rgba(99, 102, 241, 0.35);
    border-radius: 6px;
    color: #c7d2fe;
}
</style>
"""


def inject_custom_css() -> None:
    """Inject Cipher modern aesthetic stylesheet into Streamlit page."""
    st.markdown(HUGR_CSS, unsafe_allow_html=True)


def render_brand_header() -> None:
    """Render top brand navigation bar."""
    st.markdown(
        """
        <div class="cipher-header">
            <div class="cipher-logo-container">
                <span class="cipher-glyph">✦</span>
                <span class="cipher-wordmark">Cipher</span>
                <span class="cipher-badge">Universal AI Data Analyst</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_hero_intro() -> None:
    """Render clean central hero introducing the interaction."""
    st.markdown(
        """
        <div class="cipher-hero">
            <div class="cipher-hero-title">Ask anything about your data</div>
            <div class="cipher-hero-subtitle">
                Upload any CSV or Excel file. Cipher automatically discovers columns, 
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
        <div class="cipher-dataset-pill">
            <span class="cipher-status-dot"></span>
            <span>Active Dataset:</span>
            <span class="cipher-dataset-name">{dataset_name}</span>
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
        <div class="cipher-cards-grid">
            <div class="cipher-card">
                <div class="cipher-card-icon">💬</div>
                <div class="cipher-card-title">Ask Naturally</div>
                <div class="cipher-card-desc">
                    Inquire about totals, averages, rankings, or distributions without writing SQL or building manual charts.
                </div>
            </div>
            <div class="cipher-card">
                <div class="cipher-card-icon">⚡</div>
                <div class="cipher-card-title">Dynamic Discovery</div>
                <div class="cipher-card-desc">
                    Automatic column classification detects numeric measures, categorical dimensions, and temporal grains in seconds.
                </div>
            </div>
            <div class="cipher-card">
                <div class="cipher-card-icon">🛡️</div>
                <div class="cipher-card-title">Hallucination Guard</div>
                <div class="cipher-card-desc">
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


def render_dataset_readiness(result: Any) -> None:
    """Render a compact, elegant dataset readiness card communicating readiness without clutter."""
    profile = getattr(result, "profile", None)
    schema = getattr(result, "schema", None)
    cleaning = getattr(result, "cleaning", None)

    if profile is None or schema is None:
        return

    n_rows = profile.n_rows
    n_cols = profile.n_cols
    quality = profile.quality_score
    n_measures = len(schema.measure_columns)
    n_dims = len(schema.dimension_columns)
    table_name = profile.table_name

    st.markdown(
        f"""
        <div class="cipher-readiness-card">
            <div class="cipher-readiness-header">
                <div class="cipher-readiness-status">
                    <span class="cipher-status-dot"></span>
                    <span>Ready for Analysis — Table: <code>{table_name}</code></span>
                </div>
                <div class="cipher-quality-badge">{quality}% Quality Score</div>
            </div>
            <div class="cipher-readiness-grid">
                <div class="cipher-readiness-stat">
                    <span class="cipher-stat-num">{n_rows:,}</span>
                    <span class="cipher-stat-label">Records</span>
                </div>
                <div class="cipher-readiness-stat">
                    <span class="cipher-stat-num">{n_cols}</span>
                    <span class="cipher-stat-label">Columns</span>
                </div>
                <div class="cipher-readiness-stat">
                    <span class="cipher-stat-num">{n_measures}</span>
                    <span class="cipher-stat-label">Measures</span>
                </div>
                <div class="cipher-readiness-stat">
                    <span class="cipher-stat-num">{n_dims}</span>
                    <span class="cipher-stat-label">Dimensions</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Inspect Discovered Schema & Data Quality", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Discovered Measures**")
            if schema.measure_columns:
                tags = " ".join([f'<span class="cipher-tag cipher-tag-measure">{col} ({unit})</span>' for col, unit in schema.measure_columns.items()])
                st.markdown(f'<div class="cipher-tag-container">{tags}</div>', unsafe_allow_html=True)
            else:
                st.caption("No numeric measures detected.")

            if schema.time_columns:
                st.markdown("**Temporal Grains**")
                time_tags = " ".join([f'<span class="cipher-tag">{col}</span>' for col in schema.time_columns])
                st.markdown(f'<div class="cipher-tag-container">{time_tags}</div>', unsafe_allow_html=True)

        with c2:
            st.markdown("**Discovered Dimensions**")
            if schema.dimension_columns:
                tags = " ".join([f'<span class="cipher-tag cipher-tag-dim">{col}</span>' for col in schema.dimension_columns[:15]])
                st.markdown(f'<div class="cipher-tag-container">{tags}</div>', unsafe_allow_html=True)
            else:
                st.caption("No categorical dimensions detected.")

            if profile.candidate_keys:
                st.markdown("**Candidate Keys**")
                key_tags = " ".join([f'<span class="cipher-tag">{k}</span>' for k in profile.candidate_keys])
                st.markdown(f'<div class="cipher-tag-container">{key_tags}</div>', unsafe_allow_html=True)

        if cleaning:
            notes = []
            if cleaning.sentinel_nulls_replaced > 0:
                notes.append(f"Cleaned {cleaning.sentinel_nulls_replaced} sentinel null cells")
            if cleaning.whitespace_cells_trimmed > 0:
                notes.append(f"Trimmed whitespace in {cleaning.whitespace_cells_trimmed} text cells")
            if cleaning.duplicate_rows > 0:
                notes.append(f"Flagged {cleaning.duplicate_rows} duplicate rows")
            if cleaning.numeric_coercions:
                notes.extend(list(cleaning.numeric_coercions.values()))
            if notes:
                st.caption("Cleaning applied: " + " · ".join(notes))


def apply_dark_theme_to_figure(fig: Any) -> Any:
    """Apply modern dark glass theme and refined typography to Plotly figure."""
    if fig is None:
        return None
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15, 23, 42, 0.4)",
        font=dict(family="Inter, -apple-system, sans-serif", color="#f8fafc", size=12),
        title_font=dict(family="Outfit, -apple-system, sans-serif", color="#f8fafc", size=15),
        hoverlabel=dict(
            bgcolor="#1e293b",
            bordercolor="rgba(99, 102, 241, 0.4)",
            font=dict(family="Inter, -apple-system, sans-serif", color="#f8fafc", size=12),
        ),
    )
    fig.update_xaxes(
        gridcolor="rgba(255, 255, 255, 0.08)",
        zerolinecolor="rgba(255, 255, 255, 0.12)",
        tickfont=dict(color="#94a3b8"),
        title_font=dict(color="#cbd5e1"),
    )
    fig.update_yaxes(
        gridcolor="rgba(255, 255, 255, 0.08)",
        zerolinecolor="rgba(255, 255, 255, 0.12)",
        tickfont=dict(color="#94a3b8"),
        title_font=dict(color="#cbd5e1"),
    )
    return fig


def get_follow_up_suggestions(plan: Any, wh: Any) -> list[str]:
    """Derive context-aware follow-up queries from the active plan and catalog."""
    if plan is None:
        return []

    cat = getattr(wh, "catalog", None)
    all_dims = list(cat.dimensions.keys()) if cat and getattr(cat, "dimensions", None) else []
    all_metrics = list(cat.metrics.keys()) if cat and getattr(cat, "metrics", None) else []
    active_by = list(plan.by) if getattr(plan, "by", None) else []
    active_metrics = list(plan.metrics) if getattr(plan, "metrics", None) else []

    suggestions: list[str] = []

    # 1. Alternative breakdown by another dimension
    unused_dims = [d for d in all_dims if d not in active_by]
    if unused_dims:
        clean_dim = unused_dims[0].replace("_", " ")
        suggestions.append(f"break down by {clean_dim}")
        if len(unused_dims) > 1:
            clean_dim2 = unused_dims[1].replace("_", " ")
            suggestions.append(f"by {clean_dim2}")

    # 2. Ranking limit
    if getattr(plan, "limit", None) is None:
        suggestions.append("top 5")

    # 3. Temporal trend
    time_grains = getattr(cat, "time_grains", {}) if cat else {}
    if time_grains and not getattr(plan, "grain", None):
        suggestions.append("over time")

    # 4. Compare with secondary metric
    unused_metrics = [m for m in all_metrics if m not in active_metrics and (m.startswith("sum_") or m.startswith("avg_"))]
    if unused_metrics:
        clean_metric = unused_metrics[0].replace("sum_", "").replace("avg_", "").replace("_", " ")
        suggestions.append(f"and {clean_metric}")

    return suggestions[:4]


def render_follow_up_chips(suggestions: list[str]) -> None:
    """Render sleek pill chips suggesting logical next turns."""
    if not suggestions:
        return
    chips_html = "".join([f'<span class="cipher-chip">↳ {s}</span>' for s in suggestions])
    st.markdown(
        f"""
        <div class="cipher-chips-container">
            <span class="cipher-chips-label">Suggested follow-ups:</span>
            {chips_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_verification_badge(model_name: str) -> None:
    """Render zero-hallucination verification badge."""
    st.markdown(
        f"""
        <div class="cipher-verification-badge">
            <span>🛡️ Verified Result</span>
            <span>·</span>
            <span>Computed by DuckDB OLAP Engine</span>
            <span>·</span>
            <span>Planned by {model_name}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_anomaly_alert(anomalies: list[Any]) -> None:
    """Render high-priority anomaly / outlier alert banner."""
    if not anomalies:
        return
    for a in anomalies:
        label = getattr(a, "label", "Outlier")
        fmt_val = getattr(a, "formatted_value", str(getattr(a, "value", "")))
        z = getattr(a, "z_score", 0.0)
        direction = getattr(a, "direction", "above")
        pct = abs(getattr(a, "pct_from_median", 0.0))
        st.markdown(
            f"""
            <div class="cipher-anomaly-alert">
                <span class="cipher-anomaly-icon">⚠️</span>
                <div>
                    <strong>Statistical Outlier Detected:</strong> <code>{label}</code> with <strong>{fmt_val}</strong>
                    is <strong>{pct}% {direction}</strong> the median ({z:+.1f}σ robust z-score).
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_narrative_insights(insights: list[str]) -> None:
    """Render automated, data-grounded narrative insight bullets."""
    if not insights:
        return
    items_html = "".join([f'<div class="cipher-narrative-item"><span>✦</span><span>{item}</span></div>' for item in insights])
    st.markdown(
        f"""
        <div class="cipher-narrative-card">
            <div class="cipher-narrative-header">
                <span>⚡ Automated Narrative Insights</span>
            </div>
            {items_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_drilldown_actions(drilldowns: list[str]) -> None:
    """Render interactive drilldown action recommendations."""
    if not drilldowns:
        return
    chips_html = "".join([f'<span class="cipher-drilldown-chip">🔍 {d}</span>' for d in drilldowns])
    st.markdown(
        f"""
        <div class="cipher-drilldown-container">
            <span class="cipher-drilldown-label">Recommended Drilldowns:</span>
            {chips_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_candidate_joins(joins: list[Any]) -> None:
    """Render discovered cross-dataset candidate joins."""
    if not joins:
        return
    items = []
    for j in joins[:5]:
        label = getattr(j, "label", f"{j.table_a}.{j.column_a} ↔ {j.table_b}.{j.column_b}")
        reason = getattr(j, "reason", "")
        conf = getattr(j, "confidence", 1.0)
        items.append(
            f'<div style="margin: 0.45rem 0;">'
            f'<span class="cipher-join-badge">🔗 {label}</span> '
            f'<span style="font-size: 0.82rem; color: #94a3b8; margin-left: 0.5rem;">{reason} ({int(conf*100)}% confidence)</span>'
            f'</div>'
        )
    st.markdown(
        f"""
        <div class="cipher-join-card">
            <div style="font-size: 0.82rem; font-weight: 600; text-transform: uppercase; color: #a5b4fc; margin-bottom: 0.6rem; letter-spacing: 0.04em;">
                ✦ Discovered Cross-Dataset Relationships
            </div>
            {''.join(items)}
        </div>
        """,
        unsafe_allow_html=True,
    )





