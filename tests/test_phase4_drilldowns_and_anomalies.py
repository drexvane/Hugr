"""Tests for Phase 4 — Deep Drilldowns & Anomaly Detection.

Verifies:
- Automated outlier / anomaly detection using robust z-scores on tabular frames
- Segment comparisons, Pareto concentration, and spread ratios
- Deterministic, data-grounded narrative insight generation
- Context-aware interactive drilldown recommendations
- UI component renderers and AppTest harness execution
"""

from pathlib import Path
import pandas as pd
import pytest

from dtp import ingest, drilldown
from dtp.dashboard import style
from dtp.agent import Plan

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_datasets"


def test_detect_anomalies_outlier_detection():
    """Verify statistical outlier detection accurately flags extreme points."""
    # 5 standard points and 1 extreme outlier
    df = pd.DataFrame({
        "category": ["A", "B", "C", "D", "E", "Outlier_Hero"],
        "revenue": [100.0, 105.0, 98.0, 102.0, 99.0, 1500.0],
    })

    anomalies = drilldown.detect_anomalies(df, metric_col="revenue", label_col="category")
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.label == "Outlier_Hero"
    assert a.value == 1500.0
    assert a.direction == "above"
    assert a.z_score > 3.0
    assert a.pct_from_median > 1000.0


def test_detect_anomalies_stable_data():
    """Verify well-behaved, homogeneous distributions produce zero false-positive anomalies."""
    df = pd.DataFrame({
        "category": ["A", "B", "C", "D", "E"],
        "revenue": [100.0, 102.0, 99.0, 101.0, 100.0],
    })

    anomalies = drilldown.detect_anomalies(df, metric_col="revenue", label_col="category")
    assert anomalies == []


def test_analyze_segments_concentration_and_spread():
    """Verify segment analysis computes top share, Pareto top-3 share, and top-to-median ratio."""
    df = pd.DataFrame({
        "dept": ["Tech", "Sales", "Support", "Marketing", "Admin"],
        "budget": [500000, 250000, 150000, 75000, 25000],
    })

    seg = drilldown.analyze_segments(df, metric_col="budget", label_col="dept")
    assert seg is not None
    assert seg.top_label == "Tech"
    assert seg.top_value == 500000
    # Total is 1,000,000 -> Tech is 50%
    assert seg.top_share_pct == 50.0
    # Top 3: 500k + 250k + 150k = 900k (90%)
    assert seg.top_3_share_pct == 90.0
    assert seg.bottom_label == "Admin"
    # Median is 150,000 -> 500,000 / 150,000 = 3.3x
    assert seg.top_vs_median_ratio == 3.3


def test_generate_narrative_insights_on_real_data():
    """Verify narrative insights generation on a real dataset execution frame."""
    csv_path = SAMPLE_DIR / "ecommerce_orders.csv"
    result = ingest.ingest_tabular(csv_path, csv_path.name)
    wh = result.warehouse

    # Aggregate item_name by sum_quantity
    df = wh.sql("SELECT item_name, sum(quantity) as sum_quantity FROM ecommerce_orders GROUP BY item_name")

    plan = Plan(
        metrics=("sum_quantity",),
        by=("item_name",),
        grain=None,
        where=(),
        order_by=None,
        limit=None,
    )

    insights = drilldown.generate_narrative_insights(df, plan, wh)
    assert len(insights) >= 1
    # Check that lead finding is generated
    assert any("Leading Segment" in item for item in insights)


def test_get_drilldown_actions_recommendations():
    """Verify context-aware drilldown query recommendations."""
    csv_path = SAMPLE_DIR / "education_students.csv"
    result = ingest.ingest_tabular(csv_path, csv_path.name)
    wh = result.warehouse

    df = wh.sql("SELECT sex, avg(age) as avg_age FROM education_students GROUP BY sex")

    plan = Plan(
        metrics=("avg_age",),
        by=("sex",),
        grain=None,
        where=(),
        order_by=None,
        limit=None,
    )

    actions = drilldown.get_drilldown_actions(df, plan, wh)
    assert len(actions) > 0
    # Should suggest drilling into top sex with another dimension
    assert any("where sex is" in a for a in actions)


def test_phase4_renderers_smoke():
    """Verify Phase 4 UI renderers execute cleanly."""
    anomalies = [
        drilldown.AnomalyDetail(
            label="Spike Corp",
            value=999999.0,
            z_score=4.2,
            median=10000.0,
            direction="above",
            formatted_value="$1.0M",
            pct_from_median=9899.9,
        )
    ]
    style.render_anomaly_alert(anomalies)
    style.render_narrative_insights(["**Leading Segment**: Alpha leads with 45%", "**Concentration**: Top 3 hold 85%"])
    style.render_drilldown_actions(["where market is 'Europe' by region", "top 5 market by revenue"])
