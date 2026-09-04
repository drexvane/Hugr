"""Shared fixtures: one synthetic messy dataset, generated once per session."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
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


# --------------------------------------------------------------------------- #
# Phase 2 fixture: a snapshot small enough to compute by hand.
#
# The warehouse, metric, insight and chart layers all read a snapshot, so they get
# one built here rather than the real 647,247-row extract, which is gitignored.
# Every trap those layers exist to handle is present exactly once, and every
# number a test asserts is derivable from the table below with a calculator:
#
#   * two cancelled lines carrying full money and a delay, so a missing gate
#     changes revenue, profit, on-time rate and average delay all at once;
#   * labels that differ between the two tables by case, by a trailing space and
#     by punctuation ('Indoor/Outdoor Games' against 'indoor outdoor games'), so
#     a join on the raw labels returns fewer rows than a join on the folded keys;
#   * one label pair that is genuinely two different things, so the fold is shown
#     not to over-reach;
#   * a monthly revenue series that is flat except for one month at 3x, which is
#     the design doc's stated acceptance criterion for anomaly flagging;
#   * one category above and one below its department's margin;
#   * a product with page views and no orders at all.
# --------------------------------------------------------------------------- #

# The 2017 months are the anomaly fixture and nothing else: 4 lines a month at
# $100, except one month at 3x. Everything that would disturb that series - the
# benchmark pair, the loss-maker, the cancelled lines, the split label - is dated
# 2016, so `timeseries(..., date_from="2017-01-01")` is flat-plus-one-spike exactly
# and the unfiltered series still exercises a two-year window.
_FLAT_MONTHS = ["2017-01", "2017-02", "2017-03", "2017-04", "2017-05", "2017-06",
                "2017-08", "2017-09", "2017-10", "2017-11", "2017-12"]
_SPIKE_MONTH = "2017-07"


def _fixture_order_items() -> "pd.DataFrame":
    """One row per order line, built so every total is a round number."""
    rows: list[dict] = []
    oid, item = 1000, 5000

    def add(month: str, sales: float, profit: float, discount: float,
            dept: str, cat: str, prod: str, *, delay: int, recognised: bool,
            market: str = "Europe", mode: str = "Standard Class",
            status: str = "COMPLETE", segment: str = "Consumer",
            lines: int = 1, quantity: int = 1) -> None:
        nonlocal oid, item
        oid += 1
        for _ in range(lines):
            item += 1
            rows.append({
                "order_item_id": item, "order_id": oid,
                "order_date": pd.Timestamp(month + "-15"),
                "shipping_date": pd.Timestamp(month + "-15") + pd.Timedelta(days=4),
                "order_item_sales": sales, "order_item_profit": profit,
                "order_item_discount": discount, "order_item_quantity": quantity,
                "order_status": status, "shipping_mode": mode, "market": market,
                "order_region": "Western Europe", "order_country": "France",
                "customer_segment": segment, "customer_city": "Paris",
                "payment_type": "TRANSFER", "delivery_status": "Shipping on time",
                "department_name": dept, "category_name": cat, "product_name": prod,
                "department_id": 1 + (dept == "Fan Shop"),
                "category_id": 10 + len(cat) % 7, "product_id": 100 + len(prod) % 11,
                "shipping_delay_days": delay,
                "days_shipping_scheduled": 4, "days_shipping_real": 4 + delay,
                "is_revenue_recognised": recognised,
                "is_shipment_valid": recognised,
            })

    # The flat baseline: 4 lines a month at $100, $10 profit, on time.
    for month in _FLAT_MONTHS:
        for _ in range(4):
            add(month, 100.0, 10.0, 5.0, "Apparel", "Women's Apparel",
                "Nike Polo", delay=0, recognised=True)
    # The spike: 12 lines, so the month totals $1,200 against a $400 median.
    for _ in range(12):
        add(_SPIKE_MONTH, 100.0, 10.0, 5.0, "Apparel", "Women's Apparel",
            "Nike Polo", delay=0, recognised=True)

    # Margin against the parent department. Apparel is 10% (100 sales, 10 profit);
    # these two sit either side of Fan Shop's own 14% inside Fan Shop.
    add("2016-03", 400.0, 80.0, 0.0, "Fan Shop", "Indoor/Outdoor Games",
        "Dart Board", delay=2, recognised=True, mode="First Class")     # 20%
    add("2016-03", 600.0, 60.0, 0.0, "Fan Shop", "Sporting Goods",
        "Bowling Ball", delay=3, recognised=True, mode="First Class")   # 10%

    # A loss-maker, and a two-line order so lines_per_order is not 1.0 everywhere.
    add("2016-04", 200.0, -50.0, 100.0, "Fan Shop", "Sporting Goods",
        "Clearance Bat", delay=-2, recognised=True, lines=2, market="LATAM")

    # One recognised Nike Polo order inside the log's window, so the funnel's
    # viewed-never-ordered list holds exactly the product that has never sold.
    add("2016-04", 100.0, 10.0, 5.0, "Apparel", "Women's Apparel", "Nike Polo",
        delay=0, recognised=True)

    # The gate's whole reason: full money, a real delay, never recognised.
    add("2016-05", 5000.0, 500.0, 0.0, "Apparel", "Women's Apparel", "Nike Polo",
        delay=4, recognised=False, status="CANCELED")
    add("2016-05", 3000.0, 300.0, 0.0, "Fan Shop", "Sporting Goods",
        "Bowling Ball", delay=4, recognised=False, status="SUSPECTED_FRAUD")

    # An 'Electronics' split in two, which the fold must NOT merge with the log's
    # single 'electronics'.
    add("2016-06", 250.0, 25.0, 0.0, "Technology", "Electronics (Footwear)",
        "Smart Watch", delay=1, recognised=True, market="Pacific Asia")
    add("2016-06", 350.0, 35.0, 0.0, "Technology", "Electronics (Outdoors)",
        "Trail Camera", delay=1, recognised=True, market="Pacific Asia")

    return pd.DataFrame(rows)


def _fixture_access_logs() -> "pd.DataFrame":
    """Page views, spelling every label the way the real log does: lower, with a
    trailing space on the department, and no punctuation in 'indoor outdoor'.

    Dated 2016-03 to 2016-06, which is where the non-baseline orders are, so the
    funnel window derived from this table overlaps orders instead of covering the
    flat 2017 series. That mirrors the real extract, where the log covers five of
    the fact table's thirty-seven months.
    """
    views = [
        # (product, category, department, n)
        ("nike polo", "women's apparel", "apparel ", 30),
        ("dart board", "indoor outdoor games", "fan shop ", 20),
        ("never sold hat", "featured shops", "fan shop ", 15),   # views, no orders
        ("smart watch", "electronics", "technology ", 10),       # the split label
    ]
    rows = []
    log_id = 1
    span = 119                      # 2016-03-01 + 119 days = 2016-06-28
    for product, category, department, count in views:
        for i in range(count):
            offset = round(i * span / max(count - 1, 1))
            rows.append({
                "access_log_id": log_id,
                "viewed_at": pd.Timestamp("2016-03-01") + pd.Timedelta(days=offset),
                "product_name": product, "category_name": category,
                "department_name": department,
                "client_ip": "10.0.0." + str(1 + log_id % 250),
                "request_url": "/shop/" + product.replace(" ", "-"),
            })
            log_id += 1
    return pd.DataFrame(rows)


@pytest.fixture(scope="session")
def snapshot_dir(tmp_path_factory) -> Path:
    """A written snapshot directory holding the two fixture tables."""
    from dtp import versioning

    versions = tmp_path_factory.mktemp("versions")
    versioning.write_snapshot(
        {"order_items": _fixture_order_items(),
         "access_logs": _fixture_access_logs()},
        source_dir=Path("fixture"),
        versions_dir=versions,
        validation={"status": "PASS", "verdict": "PASS - every rule held",
                    "rules": 2, "passed": 2, "failed": 0, "warned": 0},
        version_id="20200101T000000",
    )
    return versions


@pytest.fixture
def wh(snapshot_dir: Path):
    """An open warehouse over the fixture snapshot, closed after the test."""
    from dtp import warehouse

    with warehouse.open_warehouse(versions_dir=snapshot_dir) as opened:
        yield opened
