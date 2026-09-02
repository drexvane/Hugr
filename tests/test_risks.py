"""Phase 1.1 risk-register and CLI tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dtp import risks, schema_map

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def register(profile_list):
    mx = schema_map.build_matrix(profile_list)
    return risks.build(profile_list, mx)


def findings_at(register, where_contains: str) -> list[risks.Risk]:
    return [r for r in register.risks if where_contains in r.where]


def test_severity_ordering(register):
    """Blockers first: the register is read top-down under time pressure."""
    order = [risks.SEVERITIES.index(r.severity) for r in register.risks]
    assert order == sorted(order)


def test_every_risk_is_actionable(register):
    """A risk without an impact and an action is just noise."""
    for r in register.risks:
        assert r.finding and r.impact and r.action, r
        assert r.severity in risks.SEVERITIES


def test_blockers_are_the_wrong_number_defects(register):
    blockers = {r.where for r in register.by_severity("BLOCKER")}
    assert "sales_orders.order_date" in blockers      # day/month unresolvable
    assert "sales_orders.unit_price" in blockers      # 3 currencies summed together
    assert "sales_orders.discount_pct" in blockers    # 100x scale mix
    assert any("Country_Code" in b for b in blockers)  # join returns nothing


def test_verdict_blocks_phase_two(register):
    assert register.counts["BLOCKER"] > 0
    assert register.go_no_go.startswith("NOT READY")


def test_textual_nulls_are_high_not_low(register):
    region = findings_at(register, "sales_orders.region")
    sentinel = [r for r in region if "nulls stored as text" in r.finding]
    assert sentinel and sentinel[0].severity == "HIGH"


def test_constant_column_is_low(register):
    flag = findings_at(register, "sales_orders.legacy_flag")
    assert flag and all(r.severity == "LOW" for r in flag)


def test_markdown_has_a_verdict_and_all_sections(register):
    md = risks.render_markdown(register)
    assert "# Data Quality Risk Summary" in md
    assert "**Verdict: NOT READY" in md
    for sev in ("BLOCKER", "HIGH", "MEDIUM", "LOW"):
        assert "## " + sev in md
    assert "**So what:**" in md


# --- CLI ---------------------------------------------------------------------

def _run_cli(*argv: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8"}
    import os

    return subprocess.run(
        [sys.executable, "-m", "dtp.cli", *argv],
        cwd=str(cwd), capture_output=True, text=True,
        env={**os.environ, **env},
    )


def test_cli_audit_reports_blockers_and_exits_nonzero(synthetic_raw, tmp_path):
    r = _run_cli("audit", "--raw", str(synthetic_raw), "--out", str(tmp_path), cwd=REPO)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Phase 1.1 audit" in r.stdout
    assert "BLOCKER" in r.stdout
    assert "NOT READY" in r.stdout


def test_cli_explains_itself_when_there_is_no_data(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run_cli("audit", "--raw", str(empty), "--out", str(tmp_path), cwd=REPO)
    assert "No readable tabular sources" in r.stdout
    assert "dtp.cli synthetic" in r.stdout   # tells the user how to proceed


def test_cli_profile_writes_both_report_formats(synthetic_raw, tmp_path):
    out = tmp_path / "prof"
    r = _run_cli("profile", "--raw", str(synthetic_raw), "--out", str(out), cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (out / "profiling_report.md").exists()
    assert (out / "profiling_report.json").exists()


def test_cli_unknown_command_fails_loudly():
    r = _run_cli("nonsense", cwd=REPO)
    assert r.returncode != 0
