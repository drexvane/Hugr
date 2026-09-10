"""Automated verification suite for the Cipher FastAPI backend and web server.

Tests:
- API status & schema discovery endpoint
- Dynamic prompt generation
- In-memory zero-hallucination OLAP query execution (/api/ask)
- Single and multi-file CSV ingestion (/api/upload)
- Executive reports and data export endpoints (/api/export)
- Reset conversation memory (/api/reset)
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from dtp.api.server import app, state

client = TestClient(app)
SAMPLE_CSV = Path(__file__).resolve().parents[1] / "data" / "sample_datasets" / "ecommerce_orders.csv"
EDUCATION_CSV = Path(__file__).resolve().parents[1] / "data" / "sample_datasets" / "education_students.csv"


@pytest.fixture(autouse=True)
def clean_catalog():
    yield
    from dtp import metrics as M
    M.set_active_catalog(None)


def test_api_status_endpoint():
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "dataset_name" in data
    assert data["row_count"] > 0
    assert data["col_count"] > 0
    assert data["quality_score"] == 100.0
    assert len(data["starter_prompts"]) > 0


def test_api_prompts_endpoint():
    response = client.get("/api/prompts")
    assert response.status_code == 200
    data = response.json()
    assert "prompts" in data
    assert isinstance(data["prompts"], list)
    assert len(data["prompts"]) > 0


def test_api_ask_natural_query():
    # Ask deterministic query
    payload = {"question": "total quantity by item name"}
    response = client.post("/api/ask", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["ok"] is True
    assert "Chicken Bowl" in data["summary"]
    assert len(data["tiles"]) > 0
    assert data["figure"] is not None
    assert "data" in data["figure"]
    assert "layout" in data["figure"]
    assert len(data["columns"]) >= 2
    assert len(data["records"]) > 0
    assert "plan" in data
    assert len(data["follow_ups"]) > 0


def test_api_upload_csv_dataset():
    assert EDUCATION_CSV.exists()
    with open(EDUCATION_CSV, "rb") as f:
        files = [("files", ("education_students.csv", f.read(), "text/csv"))]
        response = client.post("/api/upload", files=files)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["dataset_name"] == "education_students.csv"
    assert data["row_count"] == 395
    assert data["col_count"] >= 30
    assert len(data["profile"]["measures"]) > 0
    assert len(data["profile"]["dimensions"]) > 0


def test_api_export_endpoints_after_query():
    # Execute query first
    client.post("/api/ask", json={"question": "total failures by school"})

    # Markdown report export
    md_res = client.get("/api/export/markdown")
    assert md_res.status_code == 200
    assert "# ✦ Cipher Executive Data Intelligence Report" in md_res.text

    # Standalone HTML report export
    html_res = client.get("/api/export/html")
    assert html_res.status_code == 200
    assert "<!DOCTYPE html>" in html_res.text
    assert "Cipher Intelligence Report" in html_res.text

    # CSV export
    csv_res = client.get("/api/export/csv")
    assert csv_res.status_code == 200
    assert len(csv_res.content) > 0


def test_api_reset_session():
    response = client.post("/api/reset")
    assert response.status_code == 200
    assert response.json()["ok"] is True
