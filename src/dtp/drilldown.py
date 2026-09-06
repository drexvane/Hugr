"""Deep Drilldowns, Anomaly Detection & Automated Narrative Insights (Phase 4).

Provides universal analytical intelligence over query execution frames:
1. Automated outlier / anomaly detection using robust median / MAD z-scores.
2. Segment comparisons & concentration metrics (share of total, top vs median ratio, Pareto share).
3. Data-grounded, zero-hallucination narrative insights generated directly from numbers.
4. Context-aware interactive drilldown action recommendations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import metrics as M


MAD_TO_SIGMA = 0.6745
MEANAD_TO_SIGMA = 1.253314


@dataclass(frozen=True)
class AnomalyDetail:
    """Detailed metadata for a detected outlier data point."""
    label: str
    value: float
    z_score: float
    median: float
    direction: str  # "above" | "below"
    formatted_value: str
    pct_from_median: float


@dataclass(frozen=True)
class SegmentSummary:
    """Summary of segment contribution, concentration, and spread."""
    metric_col: str
    label_col: str
    total_value: float
    top_label: str
    top_value: float
    top_share_pct: float
    bottom_label: str
    bottom_value: float
    bottom_share_pct: float
    median_value: float
    top_vs_median_ratio: float
    top_3_share_pct: float
    n_segments: int


def robust_z_scores(series: pd.Series) -> pd.Series:
    """Calculate robust z-scores using Median Absolute Deviation (MAD)."""
    numeric = pd.to_numeric(series, errors="coerce")
    clean = numeric.dropna()
    out = pd.Series(float("nan"), index=series.index, dtype="float64")
    if len(clean) < 4:
        return out
    median = float(clean.median())
    mad = float((clean - median).abs().median())
    if mad > 0:
        scale = mad / MAD_TO_SIGMA
    else:
        mean_ad = float((clean - median).abs().mean())
        if mean_ad <= 0:
            return out
        scale = mean_ad / MEANAD_TO_SIGMA
    out.loc[clean.index] = (clean - median) / scale
    return out


def detect_anomalies(
    df: pd.DataFrame,
    metric_col: str,
    label_col: str | None = None,
    threshold: float = 2.5,
) -> list[AnomalyDetail]:
    """Identify statistical outliers in a DataFrame given a metric column."""
    if df is None or df.empty or metric_col not in df.columns:
        return []
    if len(df) < 4:
        return []

    values = pd.to_numeric(df[metric_col], errors="coerce")
    z = robust_z_scores(values)
    median = float(values.dropna().median())
    if np.isnan(median):
        return []

    flagged = z.abs() >= threshold
    anomalies: list[AnomalyDetail] = []

    for idx in z[flagged].index:
        val = float(values.loc[idx])
        score = float(z.loc[idx])
        raw_label = str(df.loc[idx, label_col]) if label_col and label_col in df.columns else f"Row {idx + 1}"
        direction = "above" if score > 0 else "below"
        pct_diff = 100.0 * (val - median) / abs(median) if median != 0 else 0.0

        # Formatted string
        if abs(val) >= 1_000_000:
            fmt_val = f"${val/1_000_000:.2f}M"
        elif abs(val) >= 1_000:
            fmt_val = f"${val/1_000:.1f}k"
        elif isinstance(val, float):
            fmt_val = f"{val:,.2f}"
        else:
            fmt_val = f"{val:,}"

        anomalies.append(
            AnomalyDetail(
                label=raw_label,
                value=val,
                z_score=round(score, 2),
                median=round(median, 2),
                direction=direction,
                formatted_value=fmt_val,
                pct_from_median=round(pct_diff, 1),
            )
        )

    return sorted(anomalies, key=lambda a: -abs(a.z_score))


def analyze_segments(
    df: pd.DataFrame,
    metric_col: str,
    label_col: str,
) -> SegmentSummary | None:
    """Compute concentration, spread, and comparative metrics across dimension segments."""
    if df is None or len(df) < 2 or metric_col not in df.columns or label_col not in df.columns:
        return None

    clean_df = df.dropna(subset=[metric_col]).copy()
    clean_df[metric_col] = pd.to_numeric(clean_df[metric_col], errors="coerce")
    clean_df = clean_df.dropna(subset=[metric_col])

    if len(clean_df) < 2:
        return None

    sorted_df = clean_df.sort_values(metric_col, ascending=False).reset_index(drop=True)
    total_val = float(sorted_df[metric_col].sum())
    if total_val <= 0:
        return None

    top_row = sorted_df.iloc[0]
    bottom_row = sorted_df.iloc[-1]
    median_val = float(sorted_df[metric_col].median())

    top_val = float(top_row[metric_col])
    bottom_val = float(bottom_row[metric_col])

    top_share = round((top_val / total_val) * 100, 1)
    bottom_share = round((bottom_val / total_val) * 100, 1)

    top_3_sum = float(sorted_df[metric_col].head(3).sum())
    top_3_share = round((top_3_sum / total_val) * 100, 1)

    top_vs_median = round(top_val / max(median_val, 1e-9), 1) if median_val > 0 else 1.0

    return SegmentSummary(
        metric_col=metric_col,
        label_col=label_col,
        total_value=total_val,
        top_label=str(top_row[label_col]),
        top_value=top_val,
        top_share_pct=top_share,
        bottom_label=str(bottom_row[label_col]),
        bottom_value=bottom_val,
        bottom_share_pct=bottom_share,
        median_value=median_val,
        top_vs_median_ratio=top_vs_median,
        top_3_share_pct=top_3_share,
        n_segments=len(sorted_df),
    )


def generate_narrative_insights(
    df: pd.DataFrame,
    plan: Any,
    wh: Any,
) -> list[str]:
    """Generate deterministic, data-grounded analytical narrative insights from an execution frame."""
    if df is None or df.empty or plan is None:
        return []

    metric_col = plan.metrics[0] if getattr(plan, "metrics", None) else None
    dim_col = plan.by[0] if getattr(plan, "by", None) else (plan.grain if getattr(plan, "grain", None) else None)

    if not metric_col or metric_col not in df.columns:
        return []

    insights: list[str] = []

    # 1. Segment summary analysis
    if dim_col and dim_col in df.columns:
        seg = analyze_segments(df, metric_col, dim_col)
        if seg is not None:
            # Lead finding
            clean_metric = metric_col.replace("sum_", "").replace("avg_", "").replace("_", " ")
            insights.append(
                f"**Leading Segment**: **{seg.top_label}** generates {seg.top_share_pct}% of total {clean_metric}."
            )
            # Concentration / Pareto
            if seg.n_segments >= 3 and seg.top_3_share_pct >= 50.0:
                insights.append(
                    f"**Concentration**: The top 3 {dim_col.replace('_', ' ')}s account for **{seg.top_3_share_pct}%** of the aggregate."
                )
            # Spread
            if seg.top_vs_median_ratio >= 1.5:
                insights.append(
                    f"**Spread**: Leading group outperforms the group median by **{seg.top_vs_median_ratio}x**."
                )

    # 2. Statistical anomaly detection
    anomalies = detect_anomalies(df, metric_col, dim_col)
    if anomalies:
        for a in anomalies[:2]:
            direction_desc = "higher than" if a.direction == "above" else "lower than"
            insights.append(
                f"**Statistical Outlier**: **{a.label}** ({a.formatted_value}) is **{abs(a.pct_from_median)}% {direction_desc}** the median ({a.z_score:+.1f}σ)."
            )

    return insights


def get_drilldown_actions(
    df: pd.DataFrame,
    plan: Any,
    wh: Any,
) -> list[str]:
    """Generate context-aware, actionable drilldown query recommendations."""
    if df is None or df.empty or plan is None:
        return []

    actions: list[str] = []
    cat = getattr(wh, "catalog", None)
    all_dims = list(cat.dimensions.keys()) if cat and getattr(cat, "dimensions", None) else []
    metric_col = plan.metrics[0] if getattr(plan, "metrics", None) else "metric"
    active_by = list(plan.by) if getattr(plan, "by", None) else []
    dim_col = active_by[0] if active_by else None

    # 1. Drill into top segment with next dimension
    if dim_col and dim_col in df.columns:
        values = pd.to_numeric(df[metric_col], errors="coerce")
        if values.notna().any():
            top_idx = values.idxmax()
            top_val = str(df.loc[top_idx, dim_col])
            unused_dims = [d for d in all_dims if d not in active_by]
            if unused_dims:
                next_dim = unused_dims[0].replace("_", " ")
                actions.append(f"where {dim_col} is '{top_val}' by {next_dim}")

    # 2. Filter to top 5
    if getattr(plan, "limit", None) is None and len(df) > 5 and dim_col:
        clean_metric = metric_col.replace("sum_", "").replace("avg_", "").replace("_", " ")
        actions.append(f"top 5 {dim_col.replace('_', ' ')} by {clean_metric}")

    # 3. Temporal trend for segment
    time_grains = getattr(cat, "time_grains", {}) if cat else {}
    if time_grains and dim_col and dim_col in df.columns:
        values = pd.to_numeric(df[metric_col], errors="coerce")
        if values.notna().any():
            top_idx = values.idxmax()
            top_val = str(df.loc[top_idx, dim_col])
            actions.append(f"where {dim_col} is '{top_val}' over time")

    return actions[:3]
