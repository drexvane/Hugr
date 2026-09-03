"""Shared fixtures: one synthetic messy dataset, generated once per session."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dtp import io_utils, profile

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def synthetic_raw(tmp_path_factory) -> Path:
    """Directory holding the fixture sources with known, deliberate defects."""
    raw = tmp_path_factory.mktemp("raw")
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "make_synthetic_messy.py"),
            "--rows", "500",
            "--outdir", str(raw),
            "--seed", "42",
        ],
        check=True,
        capture_output=True,
    )
    return raw


@pytest.fixture(scope="session")
def loaded(synthetic_raw: Path):
    tables, errors = io_utils.load_all(synthetic_raw)
    assert not errors, errors
    return tables


@pytest.fixture(scope="session")
def profile_list(loaded) -> list[profile.TableProfile]:
    return [profile.profile_table(t) for t in loaded]


@pytest.fixture(scope="session")
def profiled(profile_list) -> dict[str, profile.TableProfile]:
    return {tp.name: tp for tp in profile_list}


# --------------------------------------------------------------------------- #
# Phase 1.2 / 1.3 fixture
#
# A deliberately tiny source that exhibits one instance of every defect the
# cleaning engine claims to handle, so a test failure points at a mechanism
# rather than at 180,000 rows. It does NOT depend on the real dataset, which is
# gitignored - the suite has to pass on a fresh clone.
# --------------------------------------------------------------------------- #

MINI_CSV = (
    "Row Id,Order Ref,Ordered On,Qty,Unit Price,Line Total,Discount,"
    "Zip,City,Region,Status,Notes\n"
    # normal row
    "1,A-1,03/04/2021 10:30,2,10.00,18.00,2.00,02110,Boston,East ,COMPLETE,ok\n"
    # trailing/internal whitespace, sentinel null in Notes
    "2,A-1, 03/04/2021 10:30 ,1,5.00,5.00,0.00,7030,Jersey  City,East,COMPLETE,N/A\n"
    # geo shift: the geo fields sit one column left, so Zip is blank and Region
    # holds what should be the zipcode
    "3,B-2,03/05/2021 09:00,3,7.50,22.50,0.00,,East,02120,PENDING,shifted\n"
    # cancelled row that still carries money - the trap the flag exists for
    "4,C-3,03/06/2021 14:15,1,99.99,99.99,0.00,10001,New York,East,CANCELED,keep\n"
    # value-map target and a zero-sales row for the null_when branch
    "5,D-4,03/07/2021 08:05,1,0.00,0.00,0.00,90001,LA,west,COMPLETE,zero\n"
    # exact duplicate of row 5's payload but its own id, plus a real duplicate below
    "6,D-4,03/07/2021 08:05,1,0.00,0.00,0.00,90001,LA,west,COMPLETE,zero\n"
    # uncoercible quantity - must be quarantined, not dropped
    "7,E-5,03/08/2021 11:00,many,4.00,4.00,0.00,60601,Chicago,Central,COMPLETE,bad qty\n"
)

MINI_CLEANING = """
version: 1
defaults:
  trim_whitespace: true
  collapse_internal_spaces: true
  sentinel_nulls_to_null: true
  money_dp: 2
  ratio_dp: 6
skip:
  - source: readme
    reason: Not a table, listed so it is not silently ignored.
sources:
  mini:
    table: mini
    role: fact
    grain: one row per order line
    key: [row_id]
    date_formats: ["%m/%d/%Y %H:%M"]
    row_count_expected: 7
    row_fixes:
      - id: geo_shift
        where: "Zip is blank and Region matches ^[0-9]{5}$"
        do: shift_right
        columns: [City, Region, Zip]
        reason: One row has its geo fields shifted one column left.
    value_maps:
      Region:
        west: West
    columns:
      - {source: "Row Id", clean: row_id, type: integer, role: key,
         description: Line identifier and the grain of the table.}
      - {source: "Order Ref", clean: order_ref, type: text,
         description: Order this line belongs to.}
      - {source: "Ordered On", clean: ordered_on, type: datetime,
         description: When the order was placed.}
      - {source: "Qty", clean: qty, type: integer,
         description: Units ordered.}
      - {source: "Unit Price", clean: unit_price, type: money,
         description: Price per unit.}
      - {source: "Line Total", clean: line_total, type: money,
         description: Money charged for this line.}
      - {source: "Discount", clean: discount, type: money,
         description: Money taken off this line.}
      - {source: "Zip", clean: zip_code, type: text, transform: zero_pad, width: 5,
         description: Postal code, five digits of text.}
      - {source: "City", clean: city, type: text, nulls: keep_null,
         description: City, null where the geo repair could not recover one.}
      - {source: "Region", clean: region, type: category,
         description: Sales region.}
      - {source: "Status", clean: status, type: category,
         description: Order status.}
    drop:
      - {source: "Notes", reason: Free text with no analytical use.}
    derived:
      - name: discount_rate
        type: ratio
        expr: "discount / line_total"
        null_when: "line_total == 0"
        reason: Exact rate rather than a rounded one.
      - name: is_revenue
        type: boolean
        expr: "status not in ['CANCELED']"
        reason: Cancelled lines still carry money; this makes the filter explicit.
"""

MINI_VALIDATION = """
version: 1
tables:
  mini:
    min_rows: 1
    rules:
      - id: row_id_unique
        type: unique
        columns: [row_id]
        severity: error
      - id: keys_present
        type: not_null
        columns: [row_id, order_ref, status]
        severity: error
      - id: qty_positive
        type: range
        column: qty
        min: 1
        severity: error
        reason: One row's quantity is unparseable and becomes null; nulls are exempt.
      - id: zip_shape
        type: regex
        column: zip_code
        pattern: "^[0-9]{5}$"
        severity: error
      - id: status_vocabulary
        type: allowed_values
        column: status
        values: [COMPLETE, PENDING, CANCELED]
        severity: error
      - id: total_identity
        type: identity
        lhs: "line_total"
        rhs: "unit_price * qty - discount"
        tol_abs: 0.005
        severity: error
      - id: revenue_flag
        type: expression
        expr: "is_revenue == (status != 'CANCELED')"
        severity: error
      - id: ref_determines_date
        type: consistency
        determinant: order_ref
        dependent: ordered_on
        severity: error
      - id: cancelled_carry_money
        type: expression
        expr: "is_revenue or line_total == 0"
        severity: warn
        expect_violations: 1
        reason: One cancelled line carries 99.99; asserted so it cannot grow.
"""


@pytest.fixture
def mini_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "mini.csv").write_text(MINI_CSV, encoding="utf-8")
    (raw / "readme.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return raw


@pytest.fixture
def mini_config(tmp_path: Path) -> Path:
    path = tmp_path / "cleaning_rules.yml"
    path.write_text(MINI_CLEANING, encoding="utf-8")
    return path


@pytest.fixture
def mini_rules(tmp_path: Path) -> Path:
    path = tmp_path / "validation_rules.yml"
    path.write_text(MINI_VALIDATION, encoding="utf-8")
    return path


@pytest.fixture
def reports(tmp_path: Path) -> Path:
    """Where a test's reports go.

    Every `run()` writes a markdown and a JSON report, and by default that is the
    repository's own `reports/`. A test that let it default would overwrite the
    real reports with seven rows of fixture data - and the next person to read
    them would have no way of knowing. Pass this to any `run()` under test.
    """
    return tmp_path / "reports"
