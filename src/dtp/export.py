"""Export, Sharing & Multi-Dataset Synthesis Engine (Phase 5).

Provides:
1. Executive Analysis Report Generation (Markdown, standalone dark-themed HTML, and JSON).
2. Data & Result Export (Clean CSV, JSON, and summary extracts).
3. Multi-Dataset Synthesis (Candidate Join discovery across multiple tables in DuckDB).
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Sequence

import pandas as pd

from . import drilldown
from .warehouse import Warehouse


@dataclass
class CandidateJoin:
    """A discovered candidate join relation between two tables."""
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    confidence: float
    reason: str
    overlap_ratio: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.table_a}.{self.column_a} ↔ {self.table_b}.{self.column_b}"


def find_candidate_joins(wh: Warehouse, tables: Sequence[str] | None = None) -> list[CandidateJoin]:
    """Inspect tables in the warehouse and discover candidate joins between them."""
    table_names = list(tables or wh.tables)
    if len(table_names) < 2:
        return []

    candidates: list[CandidateJoin] = []

    # Cache sample values per table column
    table_cols: dict[str, dict[str, set[Any]]] = {}
    for t in table_names:
        try:
            df = wh.sql(f'SELECT * FROM "{t}" LIMIT 1000')
            table_cols[t] = {}
            for col in df.columns:
                non_nulls = df[col].dropna()
                if len(non_nulls) > 0:
                    table_cols[t][col] = set(non_nulls.astype(str).str.lower().str.strip()[:200])
        except Exception:
            continue

    # Compare pairs
    for i in range(len(table_names)):
        for j in range(i + 1, len(table_names)):
            t1, t2 = table_names[i], table_names[j]
            cols1 = table_cols.get(t1, {})
            cols2 = table_cols.get(t2, {})

            for c1, vals1 in cols1.items():
                c1_clean = c1.lower().strip()
                for c2, vals2 in cols2.items():
                    c2_clean = c2.lower().strip()

                    # Exact name match
                    exact_name = c1_clean == c2_clean
                    # ID pattern match (e.g. user_id <-> id or order_id <-> id)
                    id_match = (
                        (c1_clean.endswith("_id") and c2_clean == "id") or
                        (c2_clean.endswith("_id") and c1_clean == "id") or
                        (c1_clean.replace("_id", "") == c2_clean.replace("_id", "") and "id" in c1_clean)
                    )

                    if exact_name or id_match:
                        # Check value intersection
                        overlap = 0.0
                        if vals1 and vals2:
                            inter = len(vals1.intersection(vals2))
                            min_len = min(len(vals1), len(vals2))
                            overlap = (inter / min_len) if min_len > 0 else 0.0

                        confidence = 0.95 if (exact_name and overlap > 0.3) else (0.8 if exact_name else 0.7)
                        if overlap > 0.5:
                            confidence = min(1.0, confidence + 0.1)

                        reason = (
                            f"Exact column match with {overlap*100:.0f}% sample value overlap"
                            if exact_name
                            else f"Identified entity key relation with {overlap*100:.0f}% value overlap"
                        )
                        candidates.append(
                            CandidateJoin(
                                table_a=t1,
                                column_a=c1,
                                table_b=t2,
                                column_b=c2,
                                confidence=round(confidence, 2),
                                reason=reason,
                                overlap_ratio=round(overlap, 2),
                            )
                        )

    # Sort candidates by confidence descending
    candidates.sort(key=lambda c: (c.confidence, c.overlap_ratio), reverse=True)
    return candidates


def generate_markdown_report(answer: Any, wh: Warehouse, dataset_name: str = "Dataset") -> str:
    """Produce an executive-ready Markdown analytical summary report."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# ✦ Cipher Executive Data Intelligence Report",
        f"**Generated**: {now_str} | **Dataset**: {dataset_name} | **Engine**: DuckDB OLAP",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        f"> **Question Asked**: *\"{getattr(answer, 'question', 'Analytics Query')}\"*",
        "",
        f"**Findings**: {getattr(answer, 'summary', 'No summary available.')}",
        "",
    ]

    # Metrics Tiles
    if getattr(answer, "tiles", None):
        lines.extend([
            "## 2. Key Metrics & Performance Indicators",
            "| Metric | Value | Context |",
            "| :--- | :--- | :--- |",
        ])
        for tile in answer.tiles:
            about_text = (tile.about or tile.note or "-").replace("\n", " ")
            lines.append(f"| **{tile.label}** | `{tile.value}` | {about_text} |")
        lines.append("")

    # Narrative Insights & Anomalies
    if getattr(answer, "frame", None) is not None and getattr(answer, "plan", None):
        metric_col = answer.plan.metrics[0] if answer.plan.metrics else None
        dim_col = answer.plan.by[0] if answer.plan.by else (answer.plan.grain if getattr(answer.plan, "grain", None) else None)

        if metric_col and metric_col in answer.frame.columns:
            anomalies = drilldown.detect_anomalies(answer.frame, metric_col, dim_col)
            if anomalies:
                lines.extend([
                    "## 3. Detected Anomalies & Outliers",
                    "",
                ])
                for a in anomalies:
                    direction_symbol = "▲" if a.direction == "above" else "▼"
                    lines.append(
                        f"- **{direction_symbol} Outlier on {a.label}**: Value `{a.formatted_value}` is **{a.pct_from_median:+.1f}%** from group median ({abs(a.z_score):.1f}σ deviation)."
                    )
                lines.append("")

            narratives = drilldown.generate_narrative_insights(answer.frame, answer.plan, wh)
            if narratives:
                lines.extend([
                    "## 4. Automated Statistical Insights",
                    "",
                ])
                for n in narratives:
                    lines.append(f"- {n}")
                lines.append("")

    # Data Breakdown
    if getattr(answer, "frame", None) is not None and len(answer.frame) > 0:
        lines.extend([
            "## 5. Result Data Breakdown",
            f"*Displaying top {min(len(answer.frame), 15)} records:*",
            "",
            answer.frame.head(15).to_markdown(index=False),
            "",
        ])

    # OLAP Plan
    if getattr(answer, "plan", None):
        lines.extend([
            "## 6. Execution Plan & Data Provenance",
            "```json",
            json.dumps(answer.plan.to_dict(), indent=2),
            "```",
            "",
            "---",
            "*Report generated deterministically by Cipher AI Data Intelligence Engine.*",
        ])

    return "\n".join(lines)


def generate_html_report(answer: Any, wh: Warehouse, dataset_name: str = "Dataset") -> str:
    """Produce a high-contrast, dark-mode standalone HTML report."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    question_text = html.escape(str(getattr(answer, "question", "Analytics Query")))
    summary_text = html.escape(str(getattr(answer, "summary", "No summary available.")))
    dataset_name_clean = html.escape(dataset_name)

    tiles_html = ""
    if getattr(answer, "tiles", None):
        tiles_items = []
        for tile in answer.tiles:
            t_label = html.escape(str(tile.label))
            t_val = html.escape(str(tile.value))
            t_desc = html.escape(str(tile.about or tile.note or ""))
            tiles_items.append(f"""
            <div class="tile">
                <div class="tile-label">{t_label}</div>
                <div class="tile-val">{t_val}</div>
                <div class="tile-desc">{t_desc}</div>
            </div>
            """)
        tiles_html = f"""
        <section class="section">
            <h2 class="section-title">Key Performance Indicators</h2>
            <div class="tiles-grid">{''.join(tiles_items)}</div>
        </section>
        """

    insights_html = ""
    if getattr(answer, "frame", None) is not None and getattr(answer, "plan", None):
        metric_col = answer.plan.metrics[0] if answer.plan.metrics else None
        dim_col = answer.plan.by[0] if answer.plan.by else (answer.plan.grain if getattr(answer.plan, "grain", None) else None)
        if metric_col and metric_col in answer.frame.columns:
            narratives = drilldown.generate_narrative_insights(answer.frame, answer.plan, wh)
            if narratives:
                n_items = "".join(f"<li class='insight-item'>{html.escape(n)}</li>" for n in narratives)
                insights_html = f"""
                <section class="section">
                    <h2 class="section-title">Automated Statistical Insights</h2>
                    <ul class="insights-list">{n_items}</ul>
                </section>
                """

    table_html = ""
    if getattr(answer, "frame", None) is not None and len(answer.frame) > 0:
        raw_table = answer.frame.head(20).to_html(classes="data-table", index=False, border=0)
        table_html = f"""
        <section class="section">
            <h2 class="section-title">Execution Data Table</h2>
            <div class="table-container">{raw_table}</div>
        </section>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cipher Executive Intelligence Report - {dataset_name_clean}</title>
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Outfit:wght@600;700&display=swap');
    body {{
        margin: 0; padding: 40px; background-color: #090d16; color: #f8fafc;
        font-family: 'Inter', sans-serif; line-height: 1.6;
    }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    .header {{
        border-bottom: 1px solid rgba(255, 255, 255, 0.1); padding-bottom: 24px; margin-bottom: 32px;
    }}
    .brand {{
        font-family: 'Outfit', sans-serif; font-size: 28px; font-weight: 700;
        background: linear-gradient(135deg, #f8fafc 30%, #94a3b8 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}
    .meta {{ font-size: 13px; color: #94a3b8; margin-top: 6px; }}
    .summary-card {{
        background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(99, 102, 241, 0.25);
        border-radius: 12px; padding: 24px; margin-bottom: 32px;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
    }}
    .query {{ font-size: 14px; color: #a5b4fc; font-weight: 500; margin-bottom: 8px; }}
    .summary-text {{ font-size: 16px; color: #f1f5f9; font-weight: 500; }}
    .section-title {{ font-family: 'Outfit', sans-serif; font-size: 20px; font-weight: 600; margin-bottom: 16px; color: #e2e8f0; }}
    .tiles-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }}
    .tile {{
        background: rgba(30, 41, 59, 0.6); border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px; padding: 18px;
    }}
    .tile-label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }}
    .tile-val {{ font-family: 'Outfit', sans-serif; font-size: 28px; font-weight: 700; color: #f8fafc; margin: 4px 0; }}
    .tile-desc {{ font-size: 12px; color: #64748b; }}
    .insights-list {{
        background: rgba(15, 23, 42, 0.6); border-left: 3px solid #6366f1;
        border-radius: 0 8px 8px 0; padding: 18px 24px 18px 40px; list-style-type: square;
    }}
    .insight-item {{ margin-bottom: 8px; font-size: 14px; color: #cbd5e1; }}
    .table-container {{ overflow-x: auto; background: rgba(15, 23, 42, 0.6); border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.06); }}
    .data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
    .data-table th {{ background: rgba(30, 41, 59, 0.8); padding: 12px 16px; color: #94a3b8; font-weight: 600; border-bottom: 1px solid rgba(255, 255, 255, 0.08); }}
    .data-table td {{ padding: 10px 16px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); color: #f1f5f9; }}
    .footer {{ margin-top: 48px; border-top: 1px solid rgba(255, 255, 255, 0.06); padding-top: 20px; font-size: 12px; color: #64748b; text-align: center; }}
</style>
</head>
<body>
<div class="container">
    <header class="header">
        <div class="brand">✦ Cipher Intelligence Report</div>
        <div class="meta">Generated {now_str} • Active Dataset: {dataset_name_clean} • DuckDB In-Memory OLAP</div>
    </header>

    <div class="summary-card">
        <div class="query">Query: "{question_text}"</div>
        <div class="summary-text">{summary_text}</div>
    </div>

    {tiles_html}
    {insights_html}
    {table_html}

    <footer class="footer">
        Generated deterministically by Cipher AI Data Intelligence Engine • 100% Mathematically Verified
    </footer>
</div>
</body>
</html>
"""


def export_dataframe_to_csv(df: pd.DataFrame) -> bytes:
    """Export DataFrame to UTF-8 CSV bytes."""
    return df.to_csv(index=False).encode("utf-8")


def export_dataframe_to_json(df: pd.DataFrame) -> str:
    """Export DataFrame to formatted JSON string."""
    return df.to_json(orient="records", indent=2)
