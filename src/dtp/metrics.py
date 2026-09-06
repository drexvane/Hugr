"""Semantic layer: named metrics and dimensions over the warehouse (Phase 2.2).

The point of this module is that **a metric carries its own correctness gate**.
Phase 1 found two traps in this data: 7,754 cancelled and suspected-fraud lines
carry full sales and profit values, and those same rows still carry shipping
counters for shipments that never happened. Any view, and in Phase 3 any query the
agent generates, can reproduce both errors by writing the obvious `SUM`.

So revenue is not "sum of order_item_sales". Revenue is
`sum(order_item_sales) FILTER (WHERE is_revenue_recognised)`, and there is no way
to ask this module for the ungated figure except by name (`revenue_ungated`),
which exists only so the overview can show the delta and label it.

Metrics and dimensions are data, not code paths, which is what lets Phase 3 hand
the registry to a model as the list of things it is allowed to ask for. A metric
the registry does not define cannot be requested, so the agent cannot invent
`sum(profit)` over cancelled orders.

A rate is a `Ratio` of two of those metrics rather than an expression of its own,
because SQL attaches `FILTER` to a single aggregate call: gating
`100.0 * sum(profit) / sum(sales)` as one string either fails to parse or - worse,
if the aggregate happens to land last - gates one half of a fraction. Margin is
therefore *the profit metric over the revenue metric*, and reads as the two tiles
beside it by construction.

Everything returns a DataFrame, and every SQL string is built from registry keys
that are validated against the registry first - never from caller text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .warehouse import Warehouse, log_window

# Gate expressions, named once. `None` means the metric is defined over every row.
REVENUE_GATE = "is_revenue_recognised"
SHIPMENT_GATE = "is_shipment_valid"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    expr: str                      # ONE aggregate call: sum(x), count(*), avg(x)
    gate: str | None = None
    unit: str = "count"            # money | percent | days | count | ratio | measure
    higher_is_better: bool = True
    about: str = ""                # shown in the UI; explains the gate

    aggregatable = True            # `aggregate()` can group this by any dimension

    def __post_init__(self) -> None:
        if self.gate is not None and not _is_single_call(self.expr):
            raise ValueError(
                "gated metric " + repr(self.key) + " must be one aggregate call, "
                "not " + repr(self.expr) + " - SQL attaches FILTER to a single "
                "call, so a composite expression belongs in a Ratio")

    def sql(self) -> str:
        """The aggregate, with the gate applied as a FILTER rather than a WHERE.

        A FILTER keeps the gate attached to this one metric, so a single query can
        return gated revenue and ungated row counts side by side without the
        WHERE clause quietly narrowing both.
        """
        if self.gate is None:
            return self.expr
        return self.expr + " FILTER (WHERE " + self.gate + ")"


@dataclass(frozen=True)
class Ratio:
    """A metric defined as one metric over another, both gated in their own right.

    Margin is not `sum(profit) / sum(sales)` with a gate bolted on the end - SQL
    would reject that, and the near-miss that parses gates only the last
    aggregate. It is *the profit metric* over *the revenue metric*, so the margin
    on a tile is arithmetically the two tiles beside it and cannot drift from them.
    """

    key: str
    label: str
    num: str                       # metric key
    den: str                       # metric key
    scale: float = 1.0             # 100.0 turns a share into a percentage
    unit: str = "count"
    higher_is_better: bool = True
    about: str = ""

    aggregatable = True

    @property
    def gate(self) -> str | None:
        return metric(self.num).gate

    def sql(self) -> str:
        num, den = metric(self.num), metric(self.den)
        scaled = ("" if self.scale == 1.0 else format(self.scale, ".1f") + " * ")
        return (scaled + "(" + num.sql() + ") / nullif(" + den.sql() + ", 0)")


@dataclass(frozen=True)
class Measure:
    """A column a purpose-built query produces, registered for its label and unit.

    `funnel()` returns page views, which are not an aggregate over `order_items`
    and cannot be grouped by market or paid a visit by `aggregate()`. But a chart
    still needs to know that `views` is a count and `view_to_order_pct` is a
    percentage, and the alternative - letting the chart layer guess from the
    column name - is how an axis ends up labelled "Orders" over a line count.

    So the registry carries them, and `aggregatable = False` makes the one thing
    they cannot do fail with a sentence rather than a stack trace.
    """

    key: str
    label: str
    unit: str = "count"
    higher_is_better: bool = True
    about: str = ""

    aggregatable = False
    gate: str | None = None

    def sql(self) -> str:
        raise TypeError(
            self.key + " is produced by a purpose-built query, not by "
            "aggregate() - see metrics.funnel()")


def _is_single_call(expr: str) -> bool:
    e = expr.strip()
    opened = e.find("(")
    if opened <= 0 or not e.endswith(")"):
        return False
    if not e[:opened].strip().replace("_", "").isalnum():
        return False
    depth = 0
    for i, char in enumerate(e):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i == len(e) - 1
    return False



@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    expr: str
    table: str = "order_items"


@dataclass
class Catalog:
    name: str
    metrics: dict[str, Metric | Ratio | Measure] = field(default_factory=dict)
    dimensions: dict[str, Dimension] = field(default_factory=dict)
    drill_paths: dict[str, tuple[str, ...]] = field(default_factory=dict)
    schema: Any = None
    primary_date_col: str | None = None

    def metric(self, key: str) -> Metric | Ratio | Measure:
        try:
            return self.metrics[key]
        except KeyError:
            raise KeyError("unknown metric " + repr(key) + ". Known: "
                           + ", ".join(sorted(self.metrics))) from None

    def dimension(self, key: str) -> Dimension:
        try:
            return self.dimensions[key]
        except KeyError:
            raise KeyError("unknown dimension " + repr(key) + ". Known: "
                           + ", ".join(sorted(self.dimensions))) from None


MONEY = "money"
PERCENT = "percent"
DAYS = "days"
COUNT = "count"
RATIO = "ratio"                    # a plain decimal: 2.99 lines per order

METRICS: dict[str, Metric | Ratio | Measure] = {m.key: m for m in [
    Metric("revenue", "Revenue", "sum(order_item_sales)", REVENUE_GATE, MONEY,
           about="Cancelled and suspected-fraud lines excluded: they carry full "
                 "sales values but never converted."),
    Metric("revenue_ungated", "Revenue (ungated)", "sum(order_item_sales)", None,
           MONEY,
           about="Every line including cancelled and suspected fraud. Shown only "
                 "to name the difference; not a figure to report."),
    Metric("profit", "Profit", "sum(order_item_profit)", REVENUE_GATE, MONEY,
           about="Per line item, not per order - the source column's name invites "
                 "the opposite reading."),
    Metric("discount", "Discount given", "sum(order_item_discount)", REVENUE_GATE,
           MONEY, higher_is_better=False),
    Metric("orders", "Orders", "count(DISTINCT order_id)", REVENUE_GATE, COUNT,
           about="Distinct orders, not lines: 45,902 orders hold more than one."),
    Metric("lines", "Order lines", "count(*)", REVENUE_GATE, COUNT),
    Metric("units", "Units", "sum(order_item_quantity)", REVENUE_GATE, COUNT),
    # --- delivery: a different gate, because a different trap ------------------
    Metric("shipped_lines", "Shipped lines", "count(*)", SHIPMENT_GATE, COUNT,
           about="Cancelled shipments excluded: 4,423 of them read as late and "
                 "1,774 as early for deliveries that never happened."),
    Metric("on_time_lines", "On-time lines", "count(*)",
           SHIPMENT_GATE + " AND shipping_delay_days <= 0", COUNT,
           about="On time or early, measured against the scheduled days."),
    Metric("late_lines", "Late lines", "count(*)",
           SHIPMENT_GATE + " AND shipping_delay_days > 0", COUNT,
           higher_is_better=False,
           about="Measured against the scheduled days, not the risk flag."),
    Metric("avg_delay_days", "Average delay", "avg(shipping_delay_days)",
           SHIPMENT_GATE, DAYS, higher_is_better=False,
           about="Positive is late. Negative means early."),
    # --- derived: numerator and denominator are metrics above, so a rate on a
    #     tile is the two tiles beside it and cannot drift from them ------------
    Ratio("margin_pct", "Margin", "profit", "revenue", 100.0, PERCENT,
          about="Profit as a share of revenue, both gated."),
    Ratio("discount_pct", "Discount rate", "discount", "revenue", 100.0, PERCENT,
          higher_is_better=False),
    Ratio("aov", "Average order value", "revenue", "orders", 1.0, MONEY,
          about="Gated revenue over distinct gated orders."),
    Ratio("on_time_pct", "On-time rate", "on_time_lines", "shipped_lines", 100.0,
          PERCENT,
          about="Share of valid shipments that arrived on or before the "
                "scheduled day. Cancelled shipments are in neither half."),
    Ratio("late_pct", "Late rate", "late_lines", "shipped_lines", 100.0, PERCENT,
          higher_is_better=False),
    Ratio("lines_per_order", "Lines per order", "lines", "orders", 1.0, RATIO,
          about="Basket size. It is here because it moves for reasons that are "
                "not commercial: this extract's last four months carry one line "
                "per order against three before, which drops monthly revenue by "
                "two thirds without a single price or unit changing."),
    # --- funnel columns: labels and units only, no SQL -------------------------
    Measure("views", "Page views", COUNT,
            about="From access_logs, over the window that table covers "
                  "(2017-09 to 2018-01) and no other."),
    Measure("view_to_order_pct", "View-to-order rate", PERCENT,
            about="Gated orders per page view, both clamped to the log's own "
                  "window. Over the full order window it understates by ~7x."),
]}

DIMENSIONS: dict[str, Dimension] = {d.key: d for d in [
    Dimension("month", "Month", "date_trunc('month', order_date)"),
    Dimension("quarter", "Quarter", "date_trunc('quarter', order_date)"),
    Dimension("year", "Year", "date_trunc('year', order_date)"),
    Dimension("market", "Market", "market"),
    Dimension("region", "Region", "order_region"),
    Dimension("country", "Country", "order_country"),
    Dimension("segment", "Customer segment", "customer_segment"),
    Dimension("department", "Department", "department_name"),
    Dimension("category", "Category", "category_name"),
    Dimension("product", "Product", "product_name"),
    Dimension("shipping_mode", "Shipping mode", "shipping_mode"),
    Dimension("order_status", "Order status", "order_status"),
    Dimension("payment_type", "Payment type", "payment_type"),
    Dimension("delivery_status", "Delivery status", "delivery_status"),
    # The delay column holds seven integer values (-2 to +4), so it is a usable
    # grouping key rather than something to bin: "histogram of delay days" is a
    # bar chart over every value the column actually takes, with no bin edges to
    # choose and nothing hidden inside a bucket.
    Dimension("delay_days", "Delay (days)", "shipping_delay_days"),
]}

# Dimensions that make a drill-down path, coarse to fine.
DRILL_PATHS: dict[str, tuple[str, ...]] = {
    "geography": ("market", "region", "country"),
    "product": ("department", "category", "product"),
    "time": ("year", "quarter", "month"),
}


DEFAULT_CATALOG = Catalog(
    name="order_items",
    metrics=METRICS,
    dimensions=DIMENSIONS,
    drill_paths=DRILL_PATHS,
    primary_date_col="order_date",
)


_ACTIVE_CATALOG: Catalog | None = None


def set_active_catalog(catalog: Catalog | None) -> None:
    global _ACTIVE_CATALOG
    _ACTIVE_CATALOG = catalog


def get_active_catalog() -> Catalog | None:
    return _ACTIVE_CATALOG


def metric(key: str, catalog: Catalog | None = None) -> Metric | Ratio | Measure:
    cat = catalog or _ACTIVE_CATALOG
    target = cat.metrics if cat else METRICS
    if key in target:
        return target[key]
    if METRICS and key in METRICS:
        return METRICS[key]
    raise KeyError("unknown metric " + repr(key) + ". Known: "
                   + ", ".join(sorted(target)))


def dimension(key: str, catalog: Catalog | None = None) -> Dimension:
    cat = catalog or _ACTIVE_CATALOG
    target = cat.dimensions if cat else DIMENSIONS
    if key in target:
        return target[key]
    if DIMENSIONS and key in DIMENSIONS:
        return DIMENSIONS[key]
    raise KeyError("unknown dimension " + repr(key) + ". Known: "
                   + ", ".join(sorted(target)))


@dataclass
class Filters:
    """What the UI's controls narrow to. Values are bound, never interpolated."""

    date_from: str | None = None
    date_to: str | None = None
    where: dict[str, list[str]] = field(default_factory=dict)
    date_col: str | None = None

    def clauses(self, catalog: Catalog | None = None) -> tuple[list[str], dict[str, Any]]:
        sql: list[str] = []
        params: dict[str, Any] = {}
        if self.date_from or self.date_to:
            primary_col = catalog.primary_date_col if catalog else "order_date"
            date_col = self.date_col or primary_col
            if date_col:
                date_expr = f'"{date_col}"' if not date_col.startswith('"') and not date_col.isalnum() else date_col
                if self.date_from:
                    sql.append(f"{date_expr} >= $date_from::TIMESTAMP")
                    params["date_from"] = self.date_from
                if self.date_to:
                    # Inclusive of the whole final day: the column carries a time.
                    sql.append(f"{date_expr} < ($date_to::TIMESTAMP + INTERVAL 1 DAY)")
                    params["date_to"] = self.date_to
        for i, (key, values) in enumerate(sorted(self.where.items())):
            if not values:
                continue
            name = "f" + str(i)
            # `dimension()` validates the key, so only registry SQL reaches the
            # statement; the values themselves are bound.
            sql.append("list_contains($" + name + "::VARCHAR[], CAST("
                       + dimension(key, catalog).expr + " AS VARCHAR))")
            params[name] = [str(v) for v in values]
        return sql, params

    def describe(self, catalog: Catalog | None = None) -> str:
        bits = []
        if self.date_from or self.date_to:
            bits.append((self.date_from or "start") + " to " + (self.date_to or "end"))
        for key, values in sorted(self.where.items()):
            if values:
                bits.append(dimension(key, catalog).label + " in " + ", ".join(map(str, values)))
        return "; ".join(bits) if bits else "no filters"


def _select(metric_keys: list[str], catalog: Catalog | None = None) -> str:
    return ", ".join(metric(k, catalog).sql() + " AS " + k for k in metric_keys)


def aggregate(wh: Warehouse, metric_keys: list[str], by: list[str] | None = None,
              filters: Filters | None = None, order_by: str | None = None,
              limit: int | None = None, min_lines: int = 0,
              catalog: Catalog | None = None, table: str | None = None) -> pd.DataFrame:
    """One row per combination of `by`, one column per metric."""
    by = by or []
    metric_keys = list(metric_keys)
    cat = catalog or getattr(wh, "catalog", None)
    for key in metric_keys:
        m = metric(key, cat)
        if not m.aggregatable:
            raise KeyError(key + " cannot be grouped by a dimension: it comes "
                           "from a purpose-built query, not from order_items")
    dims = [dimension(k, cat) for k in by]

    filters = filters or Filters()
    where, params = filters.clauses(cat)

    select = [d.expr + " AS " + d.key for d in dims]
    if metric_keys:
        select.append(_select(metric_keys, cat))
    select.append("count(*) AS n_lines")

    table_name = table or (cat.name if cat and cat.name else "order_items")
    from_clause = f'FROM "{table_name}"' if not table_name.startswith('"') and not table_name.isalnum() else f"FROM {table_name}"

    sql = ["SELECT " + ", ".join(select), from_clause]
    if where:
        sql.append("WHERE " + " AND ".join(where))
    if dims:
        sql.append("GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims))))
    if min_lines:
        sql.append("HAVING count(*) >= " + str(int(min_lines)))
    if order_by:
        col = order_by.lstrip("-")
        if col not in metric_keys and col not in by and col != "n_lines":
            raise KeyError("cannot order by " + repr(order_by)
                           + ": not selected by this query")
        sql.append("ORDER BY " + col
                   + (" DESC" if order_by.startswith("-") else " ASC")
                   + " NULLS LAST")
    elif dims:
        sql.append("ORDER BY 1")
    if limit:
        sql.append("LIMIT " + str(int(limit)))
    return wh.sql("\n".join(sql), **params)


def totals(wh: Warehouse, metric_keys: list[str],
           filters: Filters | None = None,
           catalog: Catalog | None = None) -> dict[str, Any]:
    """The KPI row: one value per metric, no grouping."""
    df = aggregate(wh, metric_keys, filters=filters, catalog=catalog)
    if df.empty:
        return {k: None for k in metric_keys}
    return {k: df.iloc[0][k] for k in [*metric_keys, "n_lines"]}


def timeseries(wh: Warehouse, metric_keys: list[str], grain: str = "month",
               filters: Filters | None = None,
               catalog: Catalog | None = None) -> pd.DataFrame:
    cat = catalog or getattr(wh, "catalog", None)
    drill = cat.drill_paths.get("time", DRILL_PATHS["time"]) if cat else DRILL_PATHS["time"]
    if grain not in drill:
        raise KeyError("grain must be one of " + ", ".join(drill))
    return aggregate(wh, metric_keys, by=[grain], filters=filters, catalog=cat)


def date_bounds(wh: Warehouse, catalog: Catalog | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The window the table covers, for the date control's limits."""
    cat = catalog or getattr(wh, "catalog", None)
    table_name = cat.name if cat and cat.name else "order_items"
    date_col = cat.primary_date_col if cat and cat.primary_date_col else "order_date"
    date_expr = f'"{date_col}"' if not date_col.startswith('"') and not date_col.isalnum() else date_col
    from_expr = f'"{table_name}"' if not table_name.startswith('"') and not table_name.isalnum() else table_name
    df = wh.sql(f"SELECT min({date_expr}) AS lo, max({date_expr}) AS hi FROM {from_expr}")
    return pd.Timestamp(df.iat[0, 0]), pd.Timestamp(df.iat[0, 1])


def dimension_values(wh: Warehouse, key: str, limit: int = 200,
                     catalog: Catalog | None = None) -> list[str]:
    cat = catalog or getattr(wh, "catalog", None)
    dim = dimension(key, cat)
    table_name = dim.table or (cat.name if cat and cat.name else "order_items")
    from_expr = f'"{table_name}"' if not table_name.startswith('"') and not table_name.isalnum() else table_name
    df = wh.sql("SELECT CAST(" + dim.expr + " AS VARCHAR) AS v, count(*) AS n "
                f"FROM {from_expr} WHERE " + dim.expr + " IS NOT NULL "
                "GROUP BY 1 ORDER BY n DESC, v LIMIT " + str(int(limit)))
    return df["v"].tolist()


def funnel_totals(wh: Warehouse) -> dict[str, Any]:
    """The window's own totals, which `funnel()`'s columns cannot be summed into.

    `funnel()` counts *distinct orders within each group*. An order that touches
    three categories is one order and three of those counts, so adding the column
    up overstates orders - by 29% at product grain here (12,517 against 9,683) -
    and a rate built on that sum is wrong in the direction that flatters it. The
    headline therefore comes from one query over the whole window, and the frame is
    left to do what it is for: rank groups against each other.

    `orders` and `revenue` are gated; `views` is every logged view in the window,
    including views of things that never sold, because a conversion rate whose
    denominator drops its own failures is not a conversion rate.
    """
    lo, hi = log_window(wh)
    row = wh.sql("""
        WITH win AS (SELECT $lo::TIMESTAMP AS lo,
                            ($hi::TIMESTAMP + INTERVAL 1 DAY) AS hi)
        SELECT (SELECT count(*) FROM access_logs, win
                 WHERE viewed_at >= win.lo AND viewed_at < win.hi) AS views,
               (SELECT count(DISTINCT order_id) FILTER (WHERE is_revenue_recognised)
                  FROM order_items, win
                 WHERE order_date >= win.lo AND order_date < win.hi) AS orders,
               (SELECT count(*) FILTER (WHERE is_revenue_recognised)
                  FROM order_items, win
                 WHERE order_date >= win.lo AND order_date < win.hi) AS lines,
               (SELECT sum(order_item_sales) FILTER (WHERE is_revenue_recognised)
                  FROM order_items, win
                 WHERE order_date >= win.lo AND order_date < win.hi) AS revenue
    """, lo=lo, hi=hi)
    out = {k: (None if pd.isna(v) else v) for k, v in row.iloc[0].to_dict().items()}
    views, orders = out.get("views") or 0, out.get("orders") or 0
    out["view_to_order_pct"] = (100.0 * orders / views) if views else None
    out["window"] = (lo, hi)
    return out


def funnel(wh: Warehouse, by: str = "product", limit: int = 200) -> pd.DataFrame:
    """Page views against orders, over the window the log actually covers.

    The log spans 2017-09 → 2018-01; the fact table spans 2015-01 → 2018-01. A
    view-to-order ratio computed over the full order window divides a five-month
    numerator by a thirty-seven-month denominator and understates conversion by
    about 7x, so both sides are clamped to `log_window()` here rather than left to
    the caller's date filter.

    The join is on the folded `*_key` columns the warehouse view adds. On the raw
    labels it returns zero rows.

    Column names are registry keys - `views`, `orders`, `lines`, `revenue` - and
    each means here exactly what it means everywhere else. `orders` is distinct
    orders, not ordered lines; both are returned because the ratio a funnel wants
    is per order, while the lines column is what reconciles against a revenue
    chart.

    Counts and revenue are coalesced to zero, the ratio is not. A product that was
    viewed and never ordered has genuinely earned $0 in this window, so a null
    there is `sum()` over an empty set leaking into the output rather than a fact;
    `view_to_order_pct` stays null when there are no views because that ratio is
    undefined, not zero, and a chart should leave a gap rather than draw a floor.

    Do not sum these columns for a headline - `orders` is distinct *within* a group,
    so the column double-counts orders that span groups. `funnel_totals()` is the
    headline.

    Two columns name each group: `<by>` is the label to display, preferring the fact
    table's Title Case over the log's lowercase, and `<by>_key` is the folded key the
    join actually used. A group that reads lowercase on screen is therefore a group
    the fact table has never seen - `featured shops` stays lowercase because nothing
    in it has ever been ordered, which is the finding rather than a formatting slip.
    """
    if by not in ("product", "category", "department"):
        raise KeyError("funnel can group by product, category or department")
    col = by + "_key"
    label = by + "_name"
    window = log_window(wh)
    return wh.sql("""
        WITH win AS (SELECT $lo::TIMESTAMP AS lo,
                            ($hi::TIMESTAMP + INTERVAL 1 DAY) AS hi),
        v AS (SELECT """ + col + """ AS k, count(*) AS views,
                     any_value(""" + label + """) AS log_label
                FROM access_logs, win
               WHERE viewed_at >= win.lo AND viewed_at < win.hi
               GROUP BY 1),
        o AS (SELECT """ + col + """ AS k,
                     any_value(""" + label + """) AS fact_label,
                     count(DISTINCT order_id) FILTER (WHERE is_revenue_recognised)
                       AS orders,
                     count(*) FILTER (WHERE is_revenue_recognised) AS lines,
                     sum(order_item_sales) FILTER (WHERE is_revenue_recognised)
                       AS revenue
                FROM order_items, win
               WHERE order_date >= win.lo AND order_date < win.hi
               GROUP BY 1)
        SELECT coalesce(o.fact_label, v.log_label, v.k, o.k) AS """ + by + """,
               coalesce(v.k, o.k) AS """ + col + """,
               coalesce(v.views, 0)  AS views,
               coalesce(o.orders, 0) AS orders,
               coalesce(o.lines, 0)  AS lines,
               coalesce(o.revenue, 0) AS revenue,
               CASE WHEN coalesce(v.views, 0) = 0 THEN NULL
                    ELSE 100.0 * coalesce(o.orders, 0) / v.views END
                 AS view_to_order_pct
        FROM v FULL OUTER JOIN o USING (k)
        ORDER BY views DESC, orders DESC
        LIMIT """ + str(int(limit)),
        lo=window[0], hi=window[1])


# --------------------------------------------------------------------------- #
# formatting - one place, so a number reads the same on a tile, an axis and in
# an annotation sentence
# --------------------------------------------------------------------------- #

def fmt(value: Any, unit: str = COUNT, compact: bool = False) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit == MONEY:
        # The sign goes outside the currency symbol: "-$40.00", not "$-40.00".
        # Loss-making lines are a whole view in this dashboard, so the negative
        # case is not an edge case.
        sign = "-" if v < 0 else ""
        mag = abs(v)
        if compact and mag >= 1_000_000:
            return sign + "$" + format(mag / 1_000_000, ",.2f") + "M"
        if compact and mag >= 1_000:
            return sign + "$" + format(mag / 1_000, ",.1f") + "k"
        return sign + "$" + format(mag, ",.2f")
    if unit == PERCENT:
        return format(v, ",.1f") + "%"
    if unit == DAYS:
        return format(v, ",.2f") + " days"
    if unit == RATIO:
        return format(v, ",.2f")
    return format(int(round(v)), ",")


# Time dimensions carry a timestamp, and a caption that says "2018-01-01
# 00:00:00" reads as a defect. One formatter per grain, used by the axis, the
# annotation and the agent's answer alike, so all three name a month identically.
def fmt_dim(key: str, value: Any) -> str:
    """A dimension value as it should read on an axis or in a sentence."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    if key in DRILL_PATHS["time"]:
        try:
            ts = pd.Timestamp(value)
        except (TypeError, ValueError):
            return str(value)
        if key == "year":
            return format(ts.year)
        if key == "quarter":
            return "Q" + str(ts.quarter) + " " + format(ts.year)
        return ts.strftime("%b %Y")
    return str(value)


def fmt_metric(key: str, value: Any, compact: bool = False, catalog: Catalog | None = None) -> str:
    return fmt(value, metric(key, catalog).unit, compact=compact)


def fmt_delta(value: Any, unit: str = COUNT, compact: bool = False) -> str:
    """A signed change, with the sign always shown - "0.0%" hides a direction."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    sign = "+" if float(value) >= 0 else "-"
    return sign + fmt(abs(float(value)), unit, compact=compact)


def fmt_change(value: Any, unit: str = COUNT, compact: bool = False) -> str:
    """The size of an absolute change - percentages in *points*, not percent.

    "Margin is up 0.4%" is ambiguous: 0.4 percentage points, or 0.4% of 10.7%?
    Those are 0.4 and 0.04, and a reader cannot tell which is meant. Anything
    that subtracts one percentage from another goes through here and reads "pp".
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    if unit == PERCENT:
        return format(float(value), ",.1f") + " pp"
    return fmt(value, unit, compact=compact)
