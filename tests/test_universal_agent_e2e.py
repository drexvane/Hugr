"""End-to-End Dynamic Agent Validation Across All 6 Sample Datasets.

Tests the complete flow:
Natural Language Question -> Structured Plan -> Dynamic Catalog Validation ->
DuckDB Execution -> Dynamic Chart Selection -> Hallucination Guard -> Final Answer

Covers:
1. E-commerce Orders
2. Education Students
3. Finance Banking
4. Healthcare Heart
5. HR Employees
6. Urban Mobility

Validates:
- No DataCo/Supply Chain retail assumptions remain in answers or captions
- Dynamic metric and dimension resolution
- Truthful data figures matching DuckDB exactly
- Appropriate chart selection for every shape
- Safe refusals for invalid, causal, forecast, PII, and SQL questions
- Multi-turn conversation context retention
"""

from pathlib import Path
import pandas as pd
import pytest

from dtp.warehouse import Warehouse
from dtp.agent.session import Session, Answer
from dtp.agent.client import KeywordModel, ScriptedModel, OllamaModel, is_ollama_available, plan_reply
from dtp.agent import guard as G
from dtp.agent import plan as P
from dtp import metrics as M
from dtp import charts as C

DATASETS_DIR = Path(__file__).resolve().parents[1] / "data" / "sample_datasets"

DOMAINS = {
    "ecommerce": "ecommerce_orders.csv",
    "education": "education_students.csv",
    "finance": "finance_banking.csv",
    "healthcare": "healthcare_heart.csv",
    "hr": "hr_employees.csv",
    "urban": "urban_mobility.csv",
}


@pytest.fixture(scope="session")
def warehouses():
    """Create in-memory Warehouses for all 6 datasets with dynamic catalogs."""
    whs = {}
    for domain, filename in DOMAINS.items():
        filepath = DATASETS_DIR / filename
        assert filepath.exists(), f"Missing dataset: {filepath}"
        df = pd.read_csv(filepath)
        wh = Warehouse.from_df(df, name=domain)
        whs[domain] = wh
    return whs


# --------------------------------------------------------------------------- #
# 1. E-commerce Domain E2E Questions (8-10 tests)
# --------------------------------------------------------------------------- #

def test_e2e_ecommerce_questions(warehouses):
    wh = warehouses["ecommerce"]
    cat = wh.catalog
    session = Session(wh, KeywordModel(catalog=cat))

    # 1. Aggregation / KPI
    a1 = session.ask("total price")
    assert a1.ok, f"Failed: {a1.refusal}"
    assert a1.chart == "kpi"
    assert a1.plan.metrics == ["sum_price"]
    assert "order lines" not in a1.caption.lower()
    # verify number matches DuckDB
    expected_sum = wh.sql("SELECT sum(price) FROM ecommerce").iloc[0, 0]
    assert abs(a1.frame["sum_price"].iloc[0] - expected_sum) < 0.01

    # 2. Grouping / Bar Chart
    a2 = session.ask("total price by item_name")
    assert a2.ok
    assert a2.chart in ("bar", "hbar")
    assert a2.plan.by == ["item_name"]
    assert "item_name" in a2.frame.columns
    assert "sum_price" in a2.frame.columns

    # 3. Limit / Ranking
    a3 = session.ask("top 5 item_name by price")
    assert a3.ok
    assert a3.plan.limit == 5
    assert len(a3.frame) <= 5

    # 4. Multi-turn Follow-up: retains metric, changes grouping
    a4 = session.ask("by choice_description")
    assert a4.ok
    assert a4.plan.by == ["choice_description"]
    assert a4.plan.metrics == ["sum_price"]

    # 5. Filtering
    a5 = session.ask("total price by item_name where item_name is Chicken Bowl")
    assert a5.ok
    assert a5.plan.where == {"item_name": ["Chicken Bowl"]}
    assert len(a5.frame) == 1

    # 6. 2D Grouping / Heatmap
    a6 = session.ask("total price by item_name and choice_description")
    assert a6.ok
    assert a6.chart == "heatmap"
    assert len(a6.plan.by) == 2

    # 7. Hallucination Guard Verification
    verif = G.verify_summary(a1.computed, a1.frame)
    assert verif.ok

    # 8. Hallucination Guard Rejection of Fake Numbers
    fake_sentence = "The total price is $999999999.00."
    assert not G.verify_summary(fake_sentence, a1.frame).ok

    # 9. Safe refusal on unknown metric
    a9 = session.ask("quantum_spin by item_name")
    assert not a9.ok
    assert a9.refusal.code in ("unknown_metric", "unparseable")

    # 10. Safe refusal on personal data / PII
    a10 = session.ask("who is the customer for order 1")
    assert not a10.ok
    assert a10.refusal.code == "personal_data"


# --------------------------------------------------------------------------- #
# 2. Education Domain E2E Questions (8-10 tests)
# --------------------------------------------------------------------------- #

def test_e2e_education_questions(warehouses):
    wh = warehouses["education"]
    cat = wh.catalog
    session = Session(wh, KeywordModel(catalog=cat))

    # 1. Total absences KPI
    a1 = session.ask("total absences")
    assert a1.ok
    assert a1.chart == "kpi"
    assert a1.plan.metrics == ["sum_absences"]
    expected = wh.sql("SELECT sum(absences) FROM education").iloc[0, 0]
    assert abs(a1.frame["sum_absences"].iloc[0] - expected) < 0.01

    # 2. Average absences by school
    a2 = session.ask("average absences by school")
    assert a2.ok
    assert a2.chart in ("bar", "hbar")
    assert a2.plan.by == ["school"]
    assert a2.plan.metrics == ["avg_absences"]

    # 3. Top 5 schools by failures
    a3 = session.ask("top 5 school by failures")
    assert a3.ok
    assert a3.plan.limit == 5

    # 4. Multi-turn follow-up
    a4 = session.ask("by sex")
    assert a4.ok
    assert a4.plan.by == ["sex"]
    assert a4.plan.metrics == ["sum_failures"]

    # 5. Filtering
    a5 = session.ask("average absences by school where sex is F")
    assert a5.ok
    assert a5.plan.where == {"sex": ["F"]}

    # 6. 2D Heatmap
    a6 = session.ask("average absences by school and sex")
    assert a6.ok
    assert a6.chart == "heatmap"

    # 7. Safe refusal on causal question
    a7 = session.ask("why do students fail math")
    assert not a7.ok
    assert a7.refusal.code == "causal"

    # 8. Safe refusal on forecast question
    a8 = session.ask("forecast student grades for next semester")
    assert not a8.ok
    assert a8.refusal.code == "forecast"

    # 9. Safe refusal on unknown metric
    a9 = session.ask("average rocket_fuel by school")
    assert not a9.ok
    assert a9.refusal.code in ("unknown_metric", "unparseable")


# --------------------------------------------------------------------------- #
# 3. Finance Banking Domain E2E Questions (8-10 tests)
# --------------------------------------------------------------------------- #

def test_e2e_finance_questions(warehouses):
    wh = warehouses["finance"]
    cat = wh.catalog
    session = Session(wh, KeywordModel(catalog=cat))

    # 1. Average duration KPI
    a1 = session.ask("average duration")
    assert a1.ok
    assert a1.chart == "kpi"
    expected = wh.sql("SELECT avg(duration) FROM finance").iloc[0, 0]
    assert abs(a1.frame["avg_duration"].iloc[0] - expected) < 0.01

    # 2. Average duration by job
    a2 = session.ask("average duration by job")
    assert a2.ok
    assert a2.chart in ("bar", "hbar")
    assert a2.plan.by == ["job"]

    # 3. Top 5 jobs by duration
    a3 = session.ask("top 5 job by duration")
    assert a3.ok
    assert a3.plan.limit == 5

    # 4. Multi-turn follow-up: switch grouping to marital
    a4 = session.ask("by marital")
    assert a4.ok
    assert a4.plan.by == ["marital"]
    assert a4.plan.metrics == ["sum_duration"]

    # 5. Filtered aggregation
    a5 = session.ask("average duration by job where marital is married")
    assert a5.ok
    assert a5.plan.where == {"marital": ["married"]}

    # 6. 2D Heatmap
    a6 = session.ask("average duration by job and marital")
    assert a6.ok
    assert a6.chart == "heatmap"

    # 7. Safe refusal on SQL injection attempt
    a7 = session.ask("SELECT * FROM accounts")
    assert not a7.ok
    assert a7.refusal.code == "raw_sql"

    # 8. Safe refusal on write question
    a8 = session.ask("update bank balance to 1000000")
    assert not a8.ok
    assert a8.refusal.code == "write"

    # 9. Safe refusal on forecast question
    a9 = session.ask("predict default rates for next year")
    assert not a9.ok
    assert a9.refusal.code == "forecast"


# --------------------------------------------------------------------------- #
# 4. Healthcare Heart Domain E2E Questions (8-10 tests)
# --------------------------------------------------------------------------- #

def test_e2e_healthcare_questions(warehouses):
    wh = warehouses["healthcare"]
    cat = wh.catalog
    session = Session(wh, KeywordModel(catalog=cat))

    # 1. Average cholesterol KPI
    a1 = session.ask("average chol")
    assert a1.ok
    assert a1.chart == "kpi"
    expected = wh.sql("SELECT avg(chol) FROM healthcare").iloc[0, 0]
    assert abs(a1.frame["avg_chol"].iloc[0] - expected) < 0.01

    # 2. Average chol by sex
    a2 = session.ask("average chol by sex")
    assert a2.ok
    assert a2.chart in ("bar", "hbar")
    assert a2.plan.by == ["sex"]

    # 3. Average resting blood pressure (trestbps) by cp
    a3 = session.ask("average trestbps by cp")
    assert a3.ok
    assert a3.plan.by == ["cp"]

    # 4. Top 3 cp by chol
    a4 = session.ask("top 3 cp by chol")
    assert a4.ok
    assert a4.plan.limit == 3

    # 5. Multi-turn follow-up
    a5 = session.ask("by sex")
    assert a5.ok
    assert a5.plan.by == ["sex"]
    assert a5.plan.metrics == ["sum_chol"]

    # 6. Filter
    a6 = session.ask("average chol by cp where sex is 1")
    assert a6.ok
    assert a6.plan.where == {"sex": ["1"]}

    # 7. 2D Heatmap
    a7 = session.ask("average chol by cp and sex")
    assert a7.ok
    assert a7.chart == "heatmap"

    # 8. Safe refusal on diagnosis / causal
    a8 = session.ask("why did this patient have heart attack")
    assert not a8.ok
    assert a8.refusal.code == "causal"

    # 9. Safe refusal on unknown metric
    a9 = session.ask("average warp_drive by cp")
    assert not a9.ok
    assert a9.refusal.code in ("unknown_metric", "unparseable")


# --------------------------------------------------------------------------- #
# 5. HR Employees Domain E2E Questions (8-10 tests)
# --------------------------------------------------------------------------- #

def test_e2e_hr_questions(warehouses):
    wh = warehouses["hr"]
    cat = wh.catalog
    session = Session(wh, KeywordModel(catalog=cat))

    # 1. Average hours_per_week KPI
    a1 = session.ask("average hours_per_week")
    assert a1.ok
    assert a1.chart == "kpi"
    expected = wh.sql("SELECT avg(hours_per_week) FROM hr").iloc[0, 0]
    assert abs(a1.frame["avg_hours_per_week"].iloc[0] - expected) < 0.01

    # 2. Average hours_per_week by education
    a2 = session.ask("average hours_per_week by education")
    assert a2.ok
    assert a2.chart in ("bar", "hbar")
    assert a2.plan.by == ["education"]

    # 3. Average hours_per_week by workclass
    a3 = session.ask("average hours_per_week by workclass")
    assert a3.ok
    assert a3.plan.by == ["workclass"]

    # 4. Top 5 occupations by hours_per_week
    a4 = session.ask("top 5 occupation by average hours_per_week")
    assert a4.ok
    assert a4.plan.limit == 5

    # 5. Multi-turn follow-up
    a5 = session.ask("by sex")
    assert a5.ok
    assert a5.plan.by == ["sex"]
    assert a5.plan.metrics == ["avg_hours_per_week"]

    # 6. Filter
    a6 = session.ask("average hours_per_week by occupation where sex is Female")
    assert a6.ok
    assert a6.plan.where == {"sex": ["Female"]}

    # 7. 2D Heatmap
    a7 = session.ask("average hours_per_week by education and sex")
    assert a7.ok
    assert a7.chart == "heatmap"

    # 8. Safe refusal on PII / naming an employee
    a8 = session.ask("what is the name and email of the CEO")
    assert not a8.ok
    assert a8.refusal.code == "personal_data"

    # 9. Safe refusal on write question
    a9 = session.ask("increase salary of all employees by 10 percent")
    assert not a9.ok
    assert a9.refusal.code == "write"


# --------------------------------------------------------------------------- #
# 6. Urban Mobility Domain E2E Questions (8-10 tests)
# --------------------------------------------------------------------------- #

def test_e2e_urban_mobility_questions(warehouses):
    wh = warehouses["urban"]
    cat = wh.catalog
    session = Session(wh, KeywordModel(catalog=cat))

    # 1. Total cnt KPI
    a1 = session.ask("total cnt")
    assert a1.ok
    assert a1.chart == "kpi"
    expected = wh.sql("SELECT sum(cnt) FROM urban").iloc[0, 0]
    assert abs(a1.frame["sum_cnt"].iloc[0] - expected) < 0.01

    # 2. Total cnt by season
    a2 = session.ask("total cnt by season")
    assert a2.ok
    assert a2.chart in ("bar", "hbar")
    assert a2.plan.by == ["season"]

    # 3. Average temp by weathersit
    a3 = session.ask("average temp by weathersit")
    assert a3.ok
    assert a3.plan.by == ["weathersit"]

    # 4. Top 4 seasons by cnt
    a4 = session.ask("top 4 season by cnt")
    assert a4.ok
    assert a4.plan.limit == 4

    # 5. Multi-turn follow-up
    a5 = session.ask("by workingday")
    assert a5.ok
    assert a5.plan.by == ["workingday"]
    assert a5.plan.metrics == ["sum_cnt"]

    # 6. Filter
    a6 = session.ask("total cnt by season where weathersit is 1")
    assert a6.ok
    assert a6.plan.where == {"weathersit": ["1"]}

    # 7. 2D Heatmap
    a7 = session.ask("total cnt by season and weathersit")
    assert a7.ok
    assert a7.chart == "heatmap"

    # 8. Time Grain Aggregation
    a8 = session.ask("daily cnt")
    assert a8.ok
    assert a8.plan.grain == "day"

    # 9. Safe refusal on forecast question
    a9 = session.ask("predict bike rentals for next Monday")
    assert not a9.ok
    assert a9.refusal.code == "forecast"

    # 10. Safe refusal on unknown metric
    a10 = session.ask("total antimatter by season")
    assert not a10.ok
    assert a10.refusal.code in ("unknown_metric", "unparseable")


# --------------------------------------------------------------------------- #
# 7. Live Ollama Integration Test (if local Ollama is running)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not is_ollama_available(), reason="Local Ollama is not running")
def test_ollama_live_end_to_end(warehouses):
    """Test full LLM intent parsing and answering with local Ollama model."""
    wh = warehouses["education"]
    model = OllamaModel(model="gemma3:4b")
    session = Session(wh, model)

    answer = session.ask("What is the average absences by school?")
    assert answer.ok
    assert answer.plan is not None
    assert "school" in answer.plan.by
    assert any("absence" in m.lower() for m in answer.plan.metrics)
    assert not answer.frame.empty
    assert answer.chart in ("bar", "hbar")
    # Verify hallucination guard checked answer
    assert answer.summary
