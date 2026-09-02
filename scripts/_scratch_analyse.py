"""Throwaway: answer the Phase 1.2 questions the audit can't (semantic ones)."""
import pandas as pd, numpy as np, re, sys
pd.set_option("display.width", 200)

P = "dataset/DataCoSupplyChainDataset.csv"
df = pd.read_csv(P, encoding="latin-1", dtype=str, keep_default_na=False, na_values=[])
print("shape", df.shape)
print("\n=== dtype-ish scan: missing / distinct / sample ===")
for c in df.columns:
    s = df[c]
    blank = (s.str.strip() == "").sum()
    print(f"{c!r:42} blank={blank:>7} distinct={s.nunique():>7}  ex={s.iloc[0]!r}")

print("\n=== DATES ===")
for c in ["order date (DateOrders)", "shipping date (DateOrders)"]:
    v = df[c]
    print(c, "->", v.iloc[:3].tolist())
    pat = v.str.extract(r"^(\d+)/(\d+)/(\d{4})")
    a, b = pd.to_numeric(pat[0]), pd.to_numeric(pat[1])
    print("   first-part max:", a.max(), " second-part max:", b.max(),
          "=> ", "M/D/Y" if b.max() > 12 else ("D/M/Y" if a.max() > 12 else "AMBIGUOUS"))
    print("   year range:", pat[2].min(), pat[2].max())

print("\n=== ORDER vs SHIP ORDERING ===")
od = pd.to_datetime(df["order date (DateOrders)"], format="%m/%d/%Y %H:%M", errors="coerce")
sd = pd.to_datetime(df["shipping date (DateOrders)"], format="%m/%d/%Y %H:%M", errors="coerce")
print("order unparsed:", od.isna().sum(), " ship unparsed:", sd.isna().sum())
print("ship < order (impossible):", (sd < od).sum())
delta = (sd - od).dt.total_seconds() / 86400
real = pd.to_numeric(df["Days for shipping (real)"])
print("days real vs (ship-order) mismatch >1d:", ((delta - real).abs() > 1).sum())

print("\n=== NUMERIC SCALE CHECKS ===")
for c in ["Order Item Discount Rate", "Order Item Profit Ratio", "Late_delivery_risk",
          "Product Status", "Order Item Quantity", "Days for shipping (real)",
          "Days for shipment (scheduled)"]:
    n = pd.to_numeric(df[c], errors="coerce")
    print(f"{c!r:34} min={n.min():>12} max={n.max():>12} nunique={df[c].nunique()}")

print("\n=== REDUNDANCY (are these the same column twice?) ===")
def near(a, b):
    x, y = pd.to_numeric(df[a], errors="coerce"), pd.to_numeric(df[b], errors="coerce")
    d = (x - y).abs()
    return f"maxdiff={d.max():.6f} n_diff>0.01={int((d > 0.01).sum())}"
for a, b in [("Benefit per order", "Order Profit Per Order"),
             ("Sales per customer", "Order Item Total"),
             ("Order Item Cardprod Id", "Product Card Id"),
             ("Category Id", "Product Category Id"),
             ("Customer Id", "Order Customer Id"),
             ("Order Item Product Price", "Product Price")]:
    print(f"  {a!r} vs {b!r}: {near(a, b)}")

print("\n=== ARITHMETIC IDENTITIES ===")
q = pd.to_numeric(df["Order Item Quantity"])
pp = pd.to_numeric(df["Order Item Product Price"])
sales = pd.to_numeric(df["Sales"])
disc = pd.to_numeric(df["Order Item Discount"])
rate = pd.to_numeric(df["Order Item Discount Rate"])
tot = pd.to_numeric(df["Order Item Total"])
prof = pd.to_numeric(df["Order Profit Per Order"])
ratio = pd.to_numeric(df["Order Item Profit Ratio"])
print("  Sales == price*qty      : viol>0.01 =", int(((sales - pp * q).abs() > 0.01).sum()))
print("  Total == Sales - Disc   : viol>0.01 =", int(((tot - (sales - disc)).abs() > 0.01).sum()))
print("  Disc == Sales*Rate      : viol>0.01 =", int(((disc - sales * rate).abs() > 0.01).sum()))
print("  Profit == Total*Ratio   : viol>0.01 =", int(((prof - tot * ratio).abs() > 0.01).sum()))

print("\n=== KEYS ===")
for c in ["Order Item Id", "Order Id"]:
    print(f"  {c}: distinct={df[c].nunique()} of {len(df)}  dup={len(df) - df[c].nunique()}")
print("  exact dup rows:", int(df.duplicated().sum()))
print("  Order Id -> n items: ", pd.to_numeric(df.groupby('Order Id').size()).describe().to_dict())

print("\n=== CATEGORICAL VOCABULARIES ===")
for c in ["Type", "Delivery Status", "Customer Segment", "Market", "Order Status",
          "Shipping Mode", "Department Name", "Customer Country"]:
    vc = df[c].value_counts()
    print(f"  {c} ({len(vc)}): {dict(list(vc.items())[:12])}")

print("\n=== WHITESPACE / CASE variants in text cols ===")
for c in df.columns:
    s = df[c]
    ws = (s != s.str.strip()).sum()
    if ws:
        print(f"  {c!r}: {ws} values with untrimmed whitespace")
for c in ["Order Country", "Order City", "Order Region", "Order State", "Customer City", "Category Name", "Product Name"]:
    s = df[c].str.strip()
    folded = s.str.casefold()
    g = s.groupby(folded).nunique()
    bad = g[g > 1]
    if len(bad):
        print(f"  {c}: {len(bad)} case-variant group(s), e.g.",
              {k: sorted(s[folded == k].unique())[:4] for k in list(bad.index)[:3]})

print("\n=== NON-ASCII / mojibake ===")
for c in df.columns:
    s = df[c]
    bad = s[s.str.contains(r"[^\x00-\x7F]", na=False, regex=True)]
    if len(bad):
        print(f"  {c!r}: {len(bad)} non-ascii, e.g. {bad.unique()[:4].tolist()}")

print("\n=== PII ===")
for c in ["Customer Email", "Customer Password", "Customer Fname", "Customer Lname",
          "Customer Street", "Customer Zipcode", "Latitude", "Longitude"]:
    print(f"  {c!r}: distinct={df[c].nunique()} ex={df[c].iloc[0]!r}")

print("\n=== ZIPCODE / geo nulls ===")
for c in ["Order Zipcode", "Customer Zipcode", "Product Description", "Product Image"]:
    s = df[c]
    print(f"  {c!r}: blank={(s.str.strip() == '').sum()} distinct={s.nunique()} ex={s.iloc[0]!r}")

print("\n=== float32 precision noise ===")
for c in ["Sales per customer", "Order Item Discount Rate", "Order Item Profit Ratio",
          "Benefit per order", "Latitude", "Longitude"]:
    vals = df[c].astype(str)
    long = vals[vals.str.len() > 10]
    print(f"  {c!r}: {len(long)} values with >10 chars, e.g. {long.unique()[:3].tolist()}")
