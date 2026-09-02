"""Throwaway: does cleaning_rules.yml parse, and does it cover every real column?"""
import csv, sys, yaml, pandas as pd, numpy as np

CFG = "config/cleaning_rules.yml"
cfg = yaml.safe_load(open(CFG, encoding="utf-8"))
print("YAML parses OK. version =", cfg.get("version"))

FILES = {
    "DataCoSupplyChainDataset": "dataset/DataCoSupplyChainDataset.csv",
    "tokenized_access_logs": "dataset/tokenized_access_logs.csv",
}

ok = True
for name, spec in cfg["sources"].items():
    path = FILES[name]
    with open(path, encoding=spec["encoding"], newline="") as fh:
        header = next(csv.reader(fh))
    kept = [c["source"] for c in spec.get("columns", [])]
    dropped = [d["source"] for d in spec.get("drop", [])]
    configured = kept + dropped

    print(f"\n=== {name} ===")
    print(f"  real header: {len(header)}  kept: {len(kept)}  dropped: {len(dropped)}"
          f"  configured total: {len(configured)}")

    dup = [c for c in set(configured) if configured.count(c) > 1]
    missing = [c for c in header if c not in configured]
    unknown = [c for c in configured if c not in header]
    if dup:
        print("  !! configured TWICE:", dup); ok = False
    if missing:
        print("  !! in file but NOT configured:", missing); ok = False
    if unknown:
        print("  !! configured but NOT in file:", unknown); ok = False
    if not (dup or missing or unknown):
        print("  coverage: 100% exact, no duplicates")

    clean_names = [c["clean"] for c in spec.get("columns", [])] + \
                  [d["name"] for d in spec.get("derived", [])]
    cdup = [c for c in set(clean_names) if clean_names.count(c) > 1]
    if cdup:
        print("  !! clean-name collision:", cdup); ok = False
    bad = [c for c in clean_names if c != c.lower() or " " in c]
    if bad:
        print("  !! not snake_case:", bad); ok = False

print("\n=== VERIFY the 'overstates revenue by X%' claim ===")
df = pd.read_csv(FILES["DataCoSupplyChainDataset"], encoding="cp1252",
                 dtype=str, keep_default_na=False, na_values=[])
sales = pd.to_numeric(df["Sales"], errors="coerce")
prof = pd.to_numeric(df["Order Profit Per Order"], errors="coerce")
st = df["Order Status"].str.strip()
never = st.isin(["CANCELED", "SUSPECTED_FRAUD"])
tot_sales, tot_prof = sales.sum(), prof.sum()
print(f"  total sales (all rows)      : {tot_sales:,.2f}")
print(f"  sales on non-completed rows : {sales[never].sum():,.2f}  ({int(never.sum())} rows)")
print(f"  => overstatement of SUM(sales): {100*sales[never].sum()/tot_sales:.2f}%")
print(f"  total profit (all rows)     : {tot_prof:,.2f}")
print(f"  profit on non-completed rows: {prof[never].sum():,.2f}")
print(f"  => overstatement of SUM(profit): {100*prof[never].sum()/tot_prof:.2f}%")
for s in ["CANCELED", "SUSPECTED_FRAUD"]:
    m = st == s
    print(f"     {s}: n={int(m.sum())} sales={sales[m].sum():,.2f} profit={prof[m].sum():,.2f}")

print("\n=== VERIFY row_fixes.customer_geo_shift preconditions ===")
zc = df["Customer Zipcode"].str.strip()
stt = df["Customer State"].str.strip()
m = (zc == "") & stt.str.fullmatch(r"[0-9]{5}")
print(f"  rows matching 'zipcode blank and state is 5 digits': {int(m.sum())}")
print(df.loc[m, ["Customer City", "Customer State", "Customer Zipcode",
                 "Customer Country", "Latitude", "Longitude"]].to_string())
print(f"  rows with blank zipcode overall: {int((zc == '').sum())}")

print("\n=== VERIFY value_maps ===")
cc = df["Customer Country"].str.strip().value_counts()
print("  Customer Country:", dict(cc))
print("  Order Country contains 'Estados Unidos':",
      bool((df["Order Country"].str.strip() == "Estados Unidos").any()))
for cid in ["13", "37"]:
    m = df["Category Id"].str.strip() == cid
    print(f"  Category Id {cid}: n={int(m.sum())} name={df.loc[m,'Category Name'].unique().tolist()}"
          f" dept={df.loc[m,'Department Name'].str.strip().unique().tolist()}")

print("\n=== VERIFY trailing-whitespace claims ===")
print("  Order Region untrimmed:", int((df["Order Region"] != df["Order Region"].str.strip()).sum()))
al = pd.read_csv(FILES["tokenized_access_logs"], encoding="cp1252",
                 dtype=str, keep_default_na=False, na_values=[])
print("  access_logs shape:", al.shape, " exact dup rows:", int(al.duplicated().sum()))
print("  Department untrimmed:", int((al["Department"] != al["Department"].str.strip()).sum()))

print("\nRESULT:", "CONFIG OK" if ok else "CONFIG HAS PROBLEMS")
sys.exit(0 if ok else 1)
