"""Universal Dataset-Agnostic Backend Test Suite.

Tests dynamic schema discovery, dynamic semantic catalog creation, DuckDB OLAP queries,
adaptive chart selection, and AI agent plan validation across 6 distinct domains:
1. HR / Employees (Adult Census Income)
2. E-commerce / Sales (Chipotle Order Transactions)
3. Education (Student Performance)
4. Healthcare (Heart Disease Clinical Data)
5. Urban Planning / Smart Cities (Capital Bikeshare Mobility)
6. Finance / Banking (Bank Direct Marketing)
"""

from pathlib import Path
import pandas as pd
import pytest

from dtp.schema_discovery import discover_schema, create_catalog_from_schema
from dtp.warehouse import Warehouse
from dtp import metrics as M
from dtp import charts as C
from dtp.agent import plan as P
from dtp.agent import guard as G

DATASETS_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_datasets"


@pytest.fixture(scope="session")
def datasets():
    """Load all 6 domain datasets."""
    files = {
        "hr": "hr_employees.csv",
        "ecommerce": "ecommerce_orders.csv",
        "education": "education_students.csv",
        "healthcare": "healthcare_heart.csv",
        "urban": "urban_mobility.csv",
        "finance": "finance_banking.csv",
    }
    loaded = {}
    for domain, filename in files.items():
        filepath = DATASETS_DIR / filename
        assert filepath.exists(), f"Missing test dataset: {filepath}"
        loaded[domain] = pd.read_csv(filepath)
    return loaded


# --------------------------------------------------------------------------- #
# 1. Dynamic Schema Discovery Tests
# --------------------------------------------------------------------------- #

def test_dynamic_schema_discovery_hr(datasets):
    df = datasets["hr"]
    schema = discover_schema(df, table_name="hr_employees")

    assert "age" in schema.measure_columns
    assert "hours_per_week" in schema.measure_columns
    assert "education" in schema.dimension_columns
    assert "workclass" in schema.dimension_columns
    assert len(schema.measure_columns) >= 2
    assert len(schema.dimension_columns) >= 3


def test_dynamic_schema_discovery_ecommerce(datasets):
    df = datasets["ecommerce"]
    schema = discover_schema(df, table_name="ecommerce_orders")

    assert "price" in schema.measure_columns
    assert schema.measure_columns["price"] == M.MONEY
    assert "order_id" in schema.id_columns
    assert "item_name" in schema.dimension_columns


def test_dynamic_schema_discovery_education(datasets):
    df = datasets["education"]
    schema = discover_schema(df, table_name="education_students")

    assert "absences" in schema.measure_columns
    assert schema.measure_columns["absences"] == M.COUNT
    assert "school" in schema.dimension_columns
    assert "sex" in schema.dimension_columns


def test_dynamic_schema_discovery_healthcare(datasets):
    df = datasets["healthcare"]
    schema = discover_schema(df, table_name="healthcare_heart")

    assert "age" in schema.measure_columns
    assert "chol" in schema.measure_columns
    assert "trestbps" in schema.measure_columns
    assert len(schema.measure_columns) >= 3


def test_dynamic_schema_discovery_urban(datasets):
    df = datasets["urban"]
    schema = discover_schema(df, table_name="urban_mobility")

    assert "cnt" in schema.measure_columns
    assert "temp" in schema.measure_columns
    assert "dteday" in schema.time_columns
    assert "season" in schema.dimension_columns


def test_dynamic_schema_discovery_finance(datasets):
    df = datasets["finance"]
    schema = discover_schema(df, table_name="finance_banking")

    assert "age" in schema.measure_columns
    assert "duration" in schema.measure_columns
    assert "job" in schema.dimension_columns
    assert "marital" in schema.dimension_columns


# --------------------------------------------------------------------------- #
# 2. Dynamic Catalog & In-Memory DuckDB Query Execution Tests
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("domain, measure_to_check, dim_to_group", [
    ("hr", "avg_hours_per_week", "education"),
    ("ecommerce", "sum_price", "item_name"),
    ("education", "avg_absences", "school"),
    ("healthcare", "avg_chol", "sex"),
    ("urban", "sum_cnt", "season"),
    ("finance", "avg_duration", "marital"),
])
def test_universal_duckdb_aggregation(datasets, domain, measure_to_check, dim_to_group):
    """Test creating Warehouse and running aggregate queries without hardcoded tables or metrics."""
    df = datasets[domain]
    wh = Warehouse.from_df(df, name=f"test_{domain}")
    cat = wh.catalog
    assert cat is not None
    assert measure_to_check in cat.metrics
    assert dim_to_group in cat.dimensions

    # Run DuckDB aggregation via semantic metric layer
    res = M.aggregate(wh, [measure_to_check], by=[dim_to_group], catalog=cat)
    assert not res.empty
    assert dim_to_group in res.columns
    assert measure_to_check in res.columns
    assert "n_lines" in res.columns
    assert (res["n_lines"] > 0).all()

    # Run totals KPI computation
    t = M.totals(wh, [measure_to_check], catalog=cat)
    assert measure_to_check in t
    assert t[measure_to_check] is not None


# --------------------------------------------------------------------------- #
# 3. Dynamic Chart Selection & Rendering Tests
# --------------------------------------------------------------------------- #

def test_chart_selection_across_all_domains(datasets):
    for domain, df in datasets.items():
        wh = Warehouse.from_df(df, name=f"test_{domain}")
        cat = wh.catalog
        M.set_active_catalog(cat)

        metric_key = list(cat.metrics.keys())[1]   # pick first generated measure
        dim_key = list(cat.dimensions.keys())[0]

        # 1 Dim, 1 Metric -> Bar or HBar
        shape_1d = C.choose([dim_key], [metric_key], n_rows=5)
        assert shape_1d in ("bar", "hbar")

        # 0 Dim, 1 Metric -> KPI
        assert C.choose([], [metric_key]) == "kpi"

        # 2 Dims, 1 Metric -> Heatmap
        if len(cat.dimensions) >= 2:
            dim_key2 = list(cat.dimensions.keys())[1]
            assert C.choose([dim_key, dim_key2], [metric_key]) == "heatmap"

        # 1 Dim, 2 Metrics -> Scatter
        if len(cat.metrics) >= 3:
            metric_key2 = list(cat.metrics.keys())[2]
            assert C.choose([dim_key], [metric_key, metric_key2]) == "scatter"

        # Render auto_figure on real query output
        agg_df = M.aggregate(wh, [metric_key], by=[dim_key], catalog=cat).head(10)
        fig = C.auto_figure(agg_df, [dim_key], [metric_key])
        assert fig is not None

        M.set_active_catalog(None)


# --------------------------------------------------------------------------- #
# 4. AI Agent Plan Validation & Hallucination Guard Tests
# --------------------------------------------------------------------------- #

def test_agent_plan_and_guard_on_dynamic_datasets(datasets):
    for domain, df in datasets.items():
        wh = Warehouse.from_df(df, name=f"test_{domain}")
        cat = wh.catalog
        M.set_active_catalog(cat)

        metric_key = list(cat.metrics.keys())[1]
        dim_key = list(cat.dimensions.keys())[0]

        # Plan creation and validation against dynamic catalog
        raw_plan = P.Plan(metrics=[metric_key], by=[dim_key], limit=10)
        validated = P.validate(raw_plan, wh=wh)
        assert validated.metrics == [metric_key]
        assert validated.by == [dim_key]

        # Plan execution through DuckDB
        frame = P.execute(wh, validated)
        assert not frame.empty
        assert metric_key in frame.columns
        assert dim_key in frame.columns

        # Verify truthful summary against frame
        first_row_val = frame[metric_key].iloc[0]
        first_row_label = str(frame[dim_key].iloc[0])

        prose = f"The {dim_key} {first_row_label} has {metric_key} of {first_row_val:,.2f}."
        verif = G.verify_summary(prose, frame)
        assert verif.ok, f"Truthful sentence rejected for {domain}: {verif.problems}"

        # Verify hallucination rejection: fabricated number
        fabricated_prose = f"The {dim_key} {first_row_label} has {metric_key} of 9999999.88."
        fab_verif = G.verify_summary(fabricated_prose, frame)
        assert not fab_verif.ok, f"Fabricated sentence was not caught for {domain}"

        M.set_active_catalog(None)
