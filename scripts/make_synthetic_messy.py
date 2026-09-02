"""Generate synthetic messy data with KNOWN defects (test fixture).

This exists so Phase 1 can be built and verified before the real demo data
arrives. Every defect below is deliberate and is asserted against in
tests/test_profile.py - if the profiler stops catching one, a test fails.

Deliberate defects, by column:
  order_id       duplicate ids; some with stray whitespace
  order_date     FOUR formats mixed (ISO, D/M/Y, M/D/Y, "12 Mar 2024"),
                 including rows that can only be one order or the other
  customer_name  leading/trailing whitespace, mojibake ("JosAo" style)
  country        case + punctuation variants (USA / usa / U.S.A.)
  region         nulls as text: "N/A", "-", "unknown", "" and real blanks
  quantity       thousands separators, a spelled-out number, negatives
  unit_price     currency symbols, parenthesised negative, bare decimals
  discount_pct   percent signs mixed with bare fractions
  email          a few malformed addresses
  status         boolean spelled 5 ways
  legacy_flag    constant column (no information)
  internal_note  ~85% missing

Usage:  python scripts/make_synthetic_messy.py [--rows 500] [--outdir data/raw]
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

COUNTRY_VARIANTS = ["USA", "usa", "U.S.A.", "United States", "UK", "uk",
                    "United Kingdom", "Germany", "germany", "India", "INDIA"]
REGION_NULLS = ["N/A", "-", "unknown", "", "  ", "NULL", "TBD"]
REGIONS = ["North", "South", "East", "West", "north", " South"]
STATUS_FORMS = ["Yes", "No", "Y", "N", "TRUE", "false", "1", "0"]
NAMES = ["Ada Lovelace", "Grace Hopper", "Alan Turing", "JosÃ© Silva",
         "Katherine Johnson", "Margaret Hamilton", "Linus Torvalds",
         "Barbara Liskov", "Radia Perlman", "Tim Berners-Lee"]
PRODUCTS = ["Widget A", "Widget B", "widget a", "Gadget-1", "Gadget 1", "Doohickey"]


def messy_date(d: date, rng: random.Random) -> str:
    """Emit the same date in one of four formats, chosen at random."""
    style = rng.choice(["iso", "eu", "us", "text"])
    if style == "iso":
        return d.strftime("%Y-%m-%d")
    if style == "eu":
        return d.strftime("%d/%m/%Y")
    if style == "us":
        return d.strftime("%m/%d/%Y")
    return d.strftime("%d %b %Y")


def messy_int(n: int, rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.05:
        return f"{n:,}"          # "1,200"
    if roll < 0.07:
        return "three"           # not a number at all
    if roll < 0.10:
        return str(-n)           # negative quantity
    return str(n)


def messy_money(amount: float, rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.20:
        return f"${amount:,.2f}"
    if roll < 0.28:
        return f"{amount:,.2f} USD"
    if roll < 0.32:
        return f"({amount:,.2f})"     # accounting negative
    if roll < 0.36:
        return f"£{amount:,.2f}"  # a different currency, undeclared
    return f"{amount:.2f}"


def messy_pct(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.35:
        return f"{rng.choice([0, 5, 10, 15, 20])}%"
    if roll < 0.60:
        return str(round(rng.choice([0.0, 0.05, 0.1, 0.15, 0.2]), 2))
    return ""


def messy_email(name: str, rng: random.Random) -> str:
    handle = name.strip().lower().replace(" ", ".")
    roll = rng.random()
    if roll < 0.06:
        return handle + "@@example..com"   # malformed
    if roll < 0.10:
        return handle                      # no domain at all
    if roll < 0.14:
        return "  " + handle + "@example.com  "
    return handle + "@example.com"


HEADERS = ["order_id", "order_date", "customer_name", "email", "country",
           "region", "product", "quantity", "unit_price", "discount_pct",
           "status", "legacy_flag", "internal_note"]


def build_rows(n_rows: int, rng: random.Random) -> list[list[str]]:
    start = date(2024, 1, 1)
    rows: list[list[str]] = []
    for i in range(n_rows):
        name = rng.choice(NAMES)
        d = start + timedelta(days=rng.randrange(0, 540))
        qty = rng.randrange(1, 40) * rng.choice([1, 1, 1, 50])
        price = round(rng.uniform(3.5, 480.0), 2)
        rows.append([
            "ORD-" + str(10000 + i),
            messy_date(d, rng),
            (" " + name + " ") if rng.random() < 0.12 else name,
            messy_email(name, rng),
            rng.choice(COUNTRY_VARIANTS),
            rng.choice(REGIONS) if rng.random() < 0.75 else rng.choice(REGION_NULLS),
            rng.choice(PRODUCTS),
            messy_int(qty, rng),
            messy_money(price, rng),
            messy_pct(rng),
            rng.choice(STATUS_FORMS),
            "ACTIVE",
            "follow up with finance" if rng.random() < 0.15 else "",
        ])

    # Guarantee day/month order IS resolvable: force a few unambiguous rows.
    rows[0][1] = "25/12/2024"   # only D/M/Y can parse this
    rows[1][1] = "12/25/2024"   # only M/D/Y can parse this -> mixed-order finding

    # Exact duplicates, plus near-duplicates differing only by case/whitespace.
    for src in (5, 17, 33):
        rows.append(list(rows[src]))
    near = list(rows[7])
    near[2] = near[2].upper() + " "
    near[4] = near[4].lower()
    rows.append(near)

    # A completely empty row, as exported by more than one BI tool.
    rows.append([""] * len(HEADERS))
    rng.shuffle(rows)
    return rows


# Second source, deliberately using a different naming convention and a
# different delimiter/encoding, so schema_map.py has something to reconcile.
TARGET_HEADERS = ["Region_Name", "Country_Code", "FY", "Target_Revenue", "Owner"]
TARGET_ROWS = [
    ["North", "US", "FY24", "1,250,000", "A. Ng"],
    ["South", "US", "FY24", "980000", "B. Oyelaran"],
    ["East", "GB", "FY24", "$1,100,000", "C. Müller"],
    ["West", "DE", "FY24", "1100000.00", "D. Rossi"],
    ["north", "IN", "FY24", "640,000", "E. Sharma"],
    ["Central", "US", "FY24", "", "F. Dubois"],
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=500)
    ap.add_argument("--outdir", default="data/_synthetic")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    orders = outdir / "sales_orders.csv"
    with orders.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADERS)
        w.writerows(build_rows(args.rows, rng))

    # cp1252 + tabs: exercises encoding detection and delimiter sniffing.
    targets = outdir / "region_targets.tsv"
    with targets.open("w", encoding="cp1252", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(TARGET_HEADERS)
        w.writerows(TARGET_ROWS)

    print("wrote " + str(orders) + "  (" + str(args.rows) + " rows + injected defects)")
    print("wrote " + str(targets) + "  (cp1252, tab-delimited)")


if __name__ == "__main__":
    main()
