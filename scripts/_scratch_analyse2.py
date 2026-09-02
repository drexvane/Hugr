"""Throwaway round 2: encoding truth, tolerance calibration, referential integrity."""
import pandas as pd, numpy as np, re

P = "dataset/DataCoSupplyChainDataset.csv"

print("=== RAW BYTE TRUTH (is it cp1252/latin-1 Spanish, or already-damaged?) ===")
raw = open(P, "rb").read()
try:
    raw.decode("utf-8"); print("  decodes as utf-8: YES")
except UnicodeDecodeError as e:
    print("  decodes as utf-8: NO ->", e)
print("  0xEF 0xBF 0xBD (real U+FFFD) count:", raw.count(b"\xef\xbf\xbd"))
for b, cp in [(b"\xfa", "ú"), (b"\xf3", "ó"), (b"\xed", "í"), (b"\xf1", "ñ"), (b"\xe9", "é")]:
    print(f"  byte {b!r} (cp1252 {cp!r}) count: {raw.count(b)}")
i = raw.find(b"Se\xfal")
print("  context around b'Se\\xfal':", raw[i-30:i+20] if i > 0 else "not found")

df = pd.read_csv(P, encoding="cp1252", dtype=str, keep_default_na=False, na_values=[])
print("\n  cp1252-decoded samples:",
      [v for v in df["Order City"].unique() if any(ord(ch) > 127 for ch in v)][:6])
print("  cp1252 Order Country:",
      [v for v in df["Order Country"].unique() if any(ord(ch) > 127 for ch in v)][:6])

num = lambda c: pd.to_numeric(df[c], errors="coerce")
q, pp = num("Order Item Quantity"), num("Order Item Product Price")
sales, disc, rate = num("Sales"), num("Order Item Discount"), num("Order Item Discount Rate")
tot, prof, ratio = num("Order Item Total"), num("Order Profit Per Order"), num("Order Item Profit Ratio")

print("\n=== TOLERANCE CALIBRATION (absolute vs relative error) ===")
def report(label, lhs, rhs):
    d = (lhs - rhs).abs()
    scale = rhs.abs().clip(lower=1e-9)
    rel = d / scale
    print(f"  {label}")
    print(f"     abs: max={d.max():.6f} p99={d.quantile(.99):.6f} n>0.01={int((d>0.01).sum())} n>0.5={int((d>0.5).sum())}")
    print(f"     rel: max={rel.max():.3e} p99={rel.quantile(.99):.3e} n>1e-6={int((rel>1e-6).sum())} n>1e-3={int((rel>1e-3).sum())}")
report("Sales  == price*qty     ", sales, pp * q)
report("Total  == Sales-Discount", tot, sales - disc)
report("Disc   == Sales*Rate    ", disc, sales * rate)
report("Profit == Total*Ratio   ", prof, tot * ratio)

print("\n  worst 'Profit == Total*Ratio' rows:")
d = (prof - tot * ratio).abs()
w = d.nlargest(5).index
print(df.loc[w, ["Order Item Total", "Order Item Profit Ratio", "Order Profit Per Order"]].to_string())

print("\n  Is Ratio just Profit/Total rounded to 2dp?")
implied = (prof / tot.replace(0, np.nan))
print("     |ratio - round(implied,2)| max:", float((ratio - implied.round(2)).abs().max()))
print("     |ratio - implied| max:", float((ratio - implied).abs().max()))

print("\n=== REFERENTIAL INTEGRITY (id -> name maps) ===")
for idc, namec in [("Category Id", "Category Name"), ("Department Id", "Department Name"),
                   ("Product Card Id", "Product Name"), ("Order Item Cardprod Id", "Product Name")]:
    g = df.groupby(idc)[namec].nunique()
    h = df.groupby(df[namec].str.strip())[idc].nunique()
    print(f"  {idc}({df[idc].nunique()}) -> {namec}({df[namec].str.strip().nunique()}): "
          f"ids with >1 name={int((g>1).sum())}, names with >1 id={int((h>1).sum())}")
    if (h > 1).any():
        for nm in h[h > 1].index[:5]:
            print(f"       {nm!r} <- ids {sorted(df.loc[df[namec].str.strip()==nm, idc].unique())}")

print("\n  Category Id -> Department Id stability:")
g = df.groupby("Category Id")["Department Id"].nunique()
print("     categories spanning >1 department:", int((g > 1).sum()))

print("\n  Product Card Id -> Product Price stability:")
g = df.groupby("Product Card Id")["Product Price"].nunique()
print("     products with >1 price:", int((g > 1).sum()))

print("\n  Customer Id -> attribute stability (is customer dimension consistent?):")
for c in ["Customer Fname", "Customer Lname", "Customer Segment", "Customer City",
          "Customer State", "Customer Country", "Customer Zipcode", "Customer Street"]:
    g = df.groupby("Customer Id")[c].nunique()
    print(f"     {c}: customers with >1 value = {int((g>1).sum())}")

print("\n  Order Id -> order-level attribute stability (order header vs item grain):")
for c in ["order date (DateOrders)", "Customer Id", "Order Status", "Market",
          "Order Country", "Shipping Mode", "Delivery Status", "Order Region",
          "shipping date (DateOrders)", "Days for shipping (real)", "Order Zipcode"]:
    g = df.groupby("Order Id")[c].nunique()
    print(f"     {c}: orders with >1 value = {int((g>1).sum())}")

print("\n=== CROSS-FIELD LOGIC ===")
late = num("Late_delivery_risk")
ds = df["Delivery Status"].str.strip()
real, sched = num("Days for shipping (real)"), num("Days for shipment (scheduled)")
print("  Late_delivery_risk == (Delivery Status == 'Late delivery'):",
      int((late.astype(bool) != (ds == "Late delivery")).sum()), "violations")
print("  Late_delivery_risk == (real > scheduled):",
      int((late.astype(bool) != (real > sched)).sum()), "violations")
print("  Delivery Status vs real-vs-sched cross-tab:")
print(pd.crosstab(ds, np.sign(real - sched)).to_string())
print("\n  'Shipping canceled' vs Order Status:")
print(pd.crosstab(ds, df["Order Status"]).loc[["Shipping canceled"]].T.query("`Shipping canceled` > 0").to_string())
print("\n  Order Status == CANCELED/SUSPECTED_FRAUD -> does it still carry Sales?")
for st in ["CANCELED", "SUSPECTED_FRAUD"]:
    m = df["Order Status"] == st
    print(f"     {st}: n={int(m.sum())} sales_sum={sales[m].sum():,.2f} profit_sum={prof[m].sum():,.2f}")

print("\n=== NEGATIVE PROFIT ===")
print("  rows with negative profit:", int((prof < 0).sum()), f"({100*(prof<0).mean():.1f}%)")
print("  profit min:", prof.min(), " total profit:", f"{prof.sum():,.2f}")

print("\n=== GEO ===")
lat, lon = num("Latitude"), num("Longitude")
print(f"  lat range [{lat.min()}, {lat.max()}]  lon range [{lon.min()}, {lon.max()}]")
print("  out-of-range lat:", int(((lat < -90) | (lat > 90)).sum()),
      " lon:", int(((lon < -180) | (lon > 180)).sum()))
g = df.groupby("Customer Id")[["Latitude", "Longitude"]].nunique()
print("  customers with >1 lat/long:", int((g > 1).any(axis=1).sum()))
print("  Customer Zipcode blank rows ->",
      df.loc[df["Customer Zipcode"].str.strip() == "", ["Customer City", "Customer State", "Customer Country"]].to_dict("records"))

print("\n=== Order Zipcode: is it only populated for some markets? ===")
oz = df["Order Zipcode"].str.strip() != ""
print(pd.crosstab(df["Market"], oz).to_string())
print("\n  by Order Country (top 8 populated):")
print(pd.crosstab(df["Order Country"], oz).sort_values(True, ascending=False).head(8).to_string())
