"""What each view contains, as values: tiles, figures, tables and one sentence each.

Nothing here imports Streamlit. A view is a `View` - formatted tiles, Plotly
figures, frames and captions - and the renderer's whole job is to place them. Three
things follow from that, and they are the reason for the split:

1. A view is testable. "The delivery view names its worst cell" is an assertion
   about a string, not about a browser.
2. Phase 3's agent can call `overview()` and hand back the same figure the
   dashboard shows, rather than reimplementing the composition.
3. The gate, the units and the sentences stay where they were decided. This module
   chooses *which* metrics a view asks for; it does not compute them, format them
   or write their SQL.

Every panel's caption is computed from the frame that panel plots. That is the
Phase 2 narrative rule from `docs/02-dashboard-design.md`: a template cannot
contradict the chart beside it, because both come from one frame.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from dtp import charts as C
from dtp import insights as I
from dtp import metrics as M
from dtp import versioning
from dtp import warehouse
from dtp.warehouse import Warehouse

# A rate over a handful of lines swings on rounding and tops any ranked chart, so
# thin groups can be dropped - but how thin is a judgement, not a constant. It is a
# view argument with a default of 0 (drop nothing) and the app surfaces it as a
# control, so the reader can see the number rather than inherit it.
MIN_LINES_DEFAULT = 0


# --------------------------------------------------------------------------- #
# what a view is made of
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Tile:
    """One KPI. `value` is already formatted, because the registry owns the unit."""

    label: str
    value: str
    note: str = ""
    about: str = ""


@dataclass(frozen=True)
class Panel:
    """One chart or table, the sentence under it, and any caveat about it.

    `frame` is what the panel was computed from and `table` is what to display.
    Both are kept: the caption is checkable against the frame, and a formatted
    table is a grid of strings that nothing downstream should try to sum.
    """

    key: str
    title: str = ""
    figure: go.Figure | None = None
    frame: pd.DataFrame | None = None
    table: pd.DataFrame | None = None
    caption: str = ""
    note: str = ""


@dataclass(frozen=True)
class View:
    key: str
    title: str
    question: str
    tiles: tuple[Tile, ...] = ()
    panels: tuple[Panel, ...] = ()
    notes: tuple[str, ...] = ()


# The nav, as data: the app builds its menu from this rather than hardcoding a
# list, so a view cannot appear in one place and not the other. The questions are
# the ones in `docs/02-dashboard-design.md`'s question-to-view map, verbatim.
CATALOGUE: tuple[tuple[str, str, str], ...] = (
    ("overview", "Overview", "Are we growing, and can I trust this number?"),
    ("delivery", "Delivery",
     "Where are we late, by how much, and is it worsening?"),
    ("profitability", "Profitability",
     "What earns money rather than merely selling?"),
    ("geography", "Geography & segment",
     "Which markets and segments deserve attention?"),
    ("funnel", "Funnel",
     "Does traffic convert, and what gets looked at but not bought?"),
    ("health", "Data health", "Did the last load pass, and what changed?"),
)

TITLES: dict[str, str] = {key: title for key, title, _ in CATALOGUE}
QUESTIONS: dict[str, str] = {key: q for key, _, q in CATALOGUE}


# --------------------------------------------------------------------------- #
# helpers - formatting for display, and nothing else
# --------------------------------------------------------------------------- #

def _tiles(wh: Warehouse, keys: list[str],
           filters: M.Filters | None = None) -> tuple[Tile, ...]:
    """A KPI row. One query for every tile, so no two tiles can disagree."""
    values = M.totals(wh, keys, filters=filters)
    out = []
    for key in keys:
        met = M.metric(key)
        out.append(Tile(label=met.label,
                        value=M.fmt_metric(key, values.get(key), compact=True),
                        about=met.about or ""))
    return tuple(out)


def _display(df: pd.DataFrame, drop: tuple[str, ...] = ()) -> pd.DataFrame:
    """A frame of formatted strings, columns named as a reader would read them.

    Registry keys format themselves; anything else is left alone. `_key` columns
    are the fold used for a join and are dropped - they are how the query worked,
    not something to read.
    """
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        if col in drop or col.endswith("_key"):
            continue
        if col in M.METRICS:
            out[M.metric(col).label] = [M.fmt_metric(col, v) for v in df[col]]
        elif col in M.DIMENSIONS:
            out[M.dimension(col).label] = [M.fmt_dim(col, v) for v in df[col]]
        else:
            out[_prose(col)] = df[col].tolist()
    return out


def _prose(column: str) -> str:
    """`n_lines` -> "Lines"; a column name a reader did not choose."""
    return {"n_lines": "Lines", "gap": "Gap vs parent",
            "beats_parent": "Beats parent", "parent_value": "Parent",
            "delta": "Change", "delta_pct": "Change %",
            "row_delta": "Row change"}.get(column,
                                           column.replace("_", " ").capitalize())


def _view(key: str, tiles: tuple[Tile, ...], panels: tuple[Panel, ...],
          notes: tuple[str, ...] = ()) -> View:
    return View(key=key, title=TITLES[key], question=QUESTIONS[key], tiles=tiles,
                panels=panels, notes=tuple(n for n in notes if n))


# --------------------------------------------------------------------------- #
# 1. overview
# --------------------------------------------------------------------------- #

OVERVIEW_TILES = ["revenue", "profit", "margin_pct", "orders", "on_time_pct"]


def overview(wh: Warehouse, filters: M.Filters | None = None,
             grain: str = "month") -> View:
    """Gated KPIs, the monthly series with its outliers ringed, and what changed.

    The ungated total is stated here and nowhere else, as a delta. Someone who has
    totalled the raw CSV needs to see why this disagrees with them by $1.57M or
    they will assume the dashboard is broken - and once is enough, because a second
    ungated figure on a second view is an invitation to quote the wrong one.
    """
    filters = filters or M.Filters()
    series = M.timeseries(wh, ["revenue", "profit"], grain=grain, filters=filters)
    found = I.find_anomalies(series, "revenue", label_col=grain)
    panels = [Panel(
        key="series",
        title="Revenue and profit over time",
        figure=C.line_series(series, grain, ["revenue", "profit"],
                             anomalies=found),
        frame=series,
        caption=I.say_anomalies(found, "revenue", noun=grain),
    )]

    comparisons = I.period_over_period(wh, ["revenue", "margin_pct"],
                                       filters=filters)
    panels.append(Panel(
        key="compare",
        title="Against the window before this one",
        caption=" ".join(I.say_comparison(c) for c in comparisons),
    ))

    # `movers` needs two windows of equal length, so it is offered only when the
    # date control actually bounds one. An undated filter means "everything", and
    # there is no prior window for everything.
    if filters.date_from and filters.date_to:
        moved = I.movers(wh, "revenue", by="category", filters=filters, limit=8)
        panels.append(Panel(
            key="movers",
            title="Biggest movers by category",
            frame=moved,
            table=_display(moved),
            caption=I.say_movers(moved, "revenue", "category"),
        ))

    gate = M.totals(wh, ["revenue", "revenue_ungated"], filters=filters)
    return _view("overview", _tiles(wh, OVERVIEW_TILES, filters), tuple(panels),
                 notes=(I.say_gate(gate),
                        I.say_shift(I.composition_shift(wh, filters=filters))))


# --------------------------------------------------------------------------- #
# 2. delivery
# --------------------------------------------------------------------------- #

DELIVERY_TILES = ["on_time_pct", "late_pct", "avg_delay_days", "shipped_lines"]


def delivery(wh: Warehouse, filters: M.Filters | None = None,
             min_lines: int = MIN_LINES_DEFAULT) -> View:
    """Where we are late, by how much, and whether it is getting worse.

    Every metric here carries the shipment gate rather than the revenue one. The
    two exclude the same rows for a different reason: those rows hold a
    `days_shipping_real` for a delivery that never happened, and 4,423 of them read
    as late. An on-time rate computed without the gate is wrong by that much.

    The delay chart groups on the delay column itself. It holds seven integers, so
    there are no bin edges to choose and nothing hidden inside a bucket - it is a
    bar chart over every value the column takes, not a histogram.
    """
    filters = filters or M.Filters()
    grid = M.aggregate(wh, ["on_time_pct"], by=["market", "shipping_mode"],
                       filters=filters, min_lines=min_lines)
    panels = [Panel(
        key="grid",
        title="On-time rate by market and shipping mode",
        figure=C.heatmap(grid, "market", "shipping_mode", "on_time_pct"),
        frame=grid,
        caption=I.say_grid(grid, "market", "shipping_mode", "on_time_pct"),
        note="A blank cell is nothing shipped that way, which is not a 0% "
             "on-time rate. The two never share a colour here.",
    )]

    spread = M.aggregate(wh, ["shipped_lines"], by=["delay_days"], filters=filters)
    panels.append(Panel(
        key="spread",
        title="How late, in days",
        figure=C.bar(spread, "delay_days", "shipped_lines",
                     subtitle="negative is early; shipments that never happened "
                              "excluded"),
        frame=spread,
        caption=I.say_spread(spread, "delay_days", "shipped_lines"),
    ))

    trend = M.timeseries(wh, ["late_pct"], grain="month", filters=filters)
    late_flags = I.find_anomalies(trend, "late_pct", label_col="month")
    panels.append(Panel(
        key="trend",
        title="Late rate over time",
        figure=C.line_series(trend, "month", ["late_pct"], anomalies=late_flags),
        frame=trend,
        caption=I.say_anomalies(late_flags, "late_pct"),
    ))
    return _view("delivery", _tiles(wh, DELIVERY_TILES, filters), tuple(panels))


# --------------------------------------------------------------------------- #
# 3. profitability
# --------------------------------------------------------------------------- #

PROFIT_TILES = ["profit", "margin_pct", "discount_pct", "aov"]


def profitability(wh: Warehouse, filters: M.Filters | None = None,
                  min_lines: int = MIN_LINES_DEFAULT) -> View:
    """What earns money rather than merely selling.

    Three different questions, deliberately not merged into one chart: whether
    discount is buying volume at the cost of margin (the scatter), whether the
    biggest seller is the biggest earner (the ghost bar), and whether a category
    beats the department it is sold in (the benchmark). The last one is the only
    one that answers "is 17.5% good", because a margin has no meaning against
    nothing.
    """
    filters = filters or M.Filters()
    mix = M.aggregate(wh, ["discount_pct", "margin_pct", "revenue"],
                      by=["category"], filters=filters, min_lines=min_lines)
    panels = [Panel(
        key="discount",
        title="Discount against margin, by category",
        figure=C.scatter(mix, "category", "discount_pct", "margin_pct",
                         size_key="revenue", quadrants=True),
        frame=mix,
        caption=I.say_ranking(mix, "margin_pct", "category"),
        note="The dotted lines are the medians of this chart, not a target: high "
             "discount with low margin is a different conversation from high "
             "discount with high margin, and the split names which one without "
             "inventing a threshold.",
    )]

    depts = M.aggregate(wh, ["profit", "revenue"], by=["department"],
                        filters=filters, order_by="-profit")
    panels.append(Panel(
        key="departments",
        title="Profit by department, revenue behind it",
        figure=C.bar(depts, "department", "profit", ghost_key="revenue"),
        frame=depts,
        caption=I.say_ranking(depts, "profit", "department"),
    ))

    bench = I.benchmark_vs_parent(wh, "margin_pct", filters=filters,
                                  min_lines=min_lines)
    panels.append(Panel(
        key="benchmark",
        title="Each category against its own department's margin",
        figure=C.bar(bench, "category", "margin_pct",
                     highlight=bench["beats_parent"] if not bench.empty else None),
        frame=bench,
        caption=I.say_benchmark(bench, "margin_pct"),
        note="Hatched bars trail their department. The department figure is "
             "recomputed over the same filters, not averaged from its "
             "categories - a mean would weight a 61-line category like a "
             "33,000-line one.",
    ))

    losses = I.loss_makers(wh, by="product", filters=filters, limit=20,
                          min_lines=min_lines)
    panels.append(Panel(
        key="losses",
        title="Products losing money",
        frame=losses,
        table=_display(losses),
        caption=I.say_losses(losses, by="product"),
    ))
    return _view("profitability", _tiles(wh, PROFIT_TILES, filters),
                 tuple(panels))


# --------------------------------------------------------------------------- #
# 4. geography and segment
# --------------------------------------------------------------------------- #

GEO_TILES = ["revenue", "margin_pct", "orders", "aov"]
GEO_PATH = M.DRILL_PATHS["geography"]


def geography(wh: Warehouse, filters: M.Filters | None = None,
              level: str = GEO_PATH[0],
              min_lines: int = MIN_LINES_DEFAULT) -> View:
    """Revenue and margin down the geography path, plus the segment mix.

    `level` walks `market -> region -> country`. Drilling is a filter plus a level
    change, not a different query: the app narrows `filters` to the value clicked
    and asks for the next level, so every figure on the way down carries the same
    gate and the same units as the one above it.

    The confounding note is the important part of this view. In this extract a
    market is very nearly a period - most months sit in exactly one - so a
    market-to-market revenue comparison is partly a comparison of two different
    windows, and saying so is the difference between a finding and an artefact.
    """
    if level not in GEO_PATH:
        raise KeyError("geography drills " + " -> ".join(GEO_PATH)
                       + ", not " + repr(level))
    filters = filters or M.Filters()
    places = M.aggregate(wh, ["revenue", "margin_pct"], by=[level],
                         filters=filters, order_by="-revenue",
                         min_lines=min_lines)
    panels = [Panel(
        key="places",
        title="Revenue by " + M.dimension(level).label.lower(),
        figure=C.bar(places, level, "revenue", horizontal=len(places) > 6),
        frame=places,
        caption=I.say_ranking(places, "revenue", level),
    )]

    trend = M.aggregate(wh, ["revenue"], by=["month", level], filters=filters)
    panels.append(Panel(
        key="trend",
        title="Revenue over time by " + M.dimension(level).label.lower(),
        figure=C.line_grouped(trend, "month", level, "revenue"),
        frame=trend,
        # `line_grouped` keeps the largest groups by summed total, and `say_trend`
        # names the largest of those, so the sentence is always about a line that
        # is actually on screen.
        caption=I.say_trend(trend, "month", level, "revenue"),
    ))

    segments = M.aggregate(wh, ["revenue", "margin_pct"], by=["segment"],
                           filters=filters, order_by="-revenue")
    panels.append(Panel(
        key="segments",
        title="Revenue by customer segment",
        figure=C.bar(segments, "segment", "revenue"),
        frame=segments,
        caption=I.say_ranking(segments, "revenue", "segment"),
    ))
    return _view("geography", _tiles(wh, GEO_TILES, filters), tuple(panels),
                 notes=(I.say_confound(I.confounding(wh, level,
                                                     filters=filters)),))


def drill_into(filters: M.Filters, level: str,
               values: str | list[str]) -> M.Filters:
    """The filter one level down: the same window, narrowed to what was clicked.

    Returned rather than mutated, because the app keeps the un-drilled filter to
    step back up to, and a drill that edited it in place would have nothing to
    return to.
    """
    if level not in GEO_PATH:
        raise KeyError("cannot drill on " + repr(level))
    chosen = [values] if isinstance(values, str) else list(values)
    where = {k: list(v) for k, v in filters.where.items()}
    if chosen:
        where[level] = chosen
    return M.Filters(date_from=filters.date_from, date_to=filters.date_to,
                     where=where)


def next_level(level: str) -> str | None:
    """The finer level of the geography path, or None at the bottom."""
    i = GEO_PATH.index(level)
    return GEO_PATH[i + 1] if i + 1 < len(GEO_PATH) else None


# --------------------------------------------------------------------------- #
# 5. funnel
# --------------------------------------------------------------------------- #

FUNNEL_TILES = ["views", "orders", "view_to_order_pct", "revenue"]


def funnel(wh: Warehouse, by: str = "product", limit: int = 200) -> View:
    """Views against orders, over the window the log covers and no other.

    This view takes no filters, and that is the point. The log spans five of the
    fact table's thirty-seven months; a rate computed over a date range the user
    picked would divide a five-month numerator by whatever they chose and understate
    conversion by up to 7x. The window is stated on the view instead.

    The headline comes from `funnel_totals`, not from summing the frame. Each row's
    order count is distinct *within* that group, so an order spanning three
    categories is one order and three of those counts - adding the column up
    overstates orders by 29% at product grain, in the direction that flatters.
    """
    totals = M.funnel_totals(wh)
    frame = M.funnel(wh, by=by, limit=limit)
    lo, hi = totals["window"]
    tiles = tuple(Tile(label=M.metric(k).label,
                       value=M.fmt_metric(k, totals.get(k), compact=True),
                       about=M.metric(k).about or "")
                  for k in FUNNEL_TILES)

    panels = [Panel(
        key="parity",
        title="Views against orders, per " + M.dimension(by).label.lower(),
        figure=C.scatter(frame, by, "views", "orders", size_key="revenue",
                         parity=True,
                         subtitle="dotted line is one order per view; "
                                  + lo + " to " + hi),
        frame=frame,
        caption=I.say_funnel(frame, by=by, totals=totals),
        note="Above the line is more orders than views, which no group reaches; "
             "the reading is distance below it.",
    )]

    dead = frame[(frame["views"] > 0) & (frame["orders"] == 0)]
    panels.append(Panel(
        key="unordered",
        title="Viewed and never ordered",
        frame=dead,
        table=_display(dead),
        caption="Demand that arrived and left. A name in lower case here is a "
                "group the fact table has never seen at all.",
    ))

    unseen = frame[(frame["views"] == 0) & (frame["orders"] > 0)]
    panels.append(Panel(
        key="unviewed",
        title="Ordered and never viewed",
        frame=unseen,
        table=_display(unseen),
        caption="Sold without a logged view in this window - which is a question "
                "about the log's coverage before it is one about the product.",
    ))
    return _view("funnel", tiles, tuple(panels),
                 notes=("Fixed to " + lo + " to " + hi + ", the window "
                        "access_logs covers. The date filter does not apply to "
                        "this view: a five-month numerator over a longer "
                        "denominator understates conversion by about 7x.",))


# --------------------------------------------------------------------------- #
# 6. data health
# --------------------------------------------------------------------------- #

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def data_health(version_id: str | None = None, versions_dir: Path | None = None,
                alerts_path: Path | None = None) -> View:
    """Whether the numbers on the other five views can be trusted.

    Manifests only - no Parquet is opened and the warehouse is not touched. That is
    what makes this view useful when something is wrong: it still renders for a
    snapshot whose data will not load, and a failed validation is a row here rather
    than a traceback on the overview.

    A dashboard that hides the verdict invites someone to present a number from a
    failed load, which is why this is a view and not a footnote.
    """
    history = versioning.list_versions(versions_dir)
    at = next((i for i in reversed(range(len(history)))
               if version_id is None or history[i].version_id == version_id), None)
    if at is None:
        return _view("health", (Tile("Snapshot", "none found"),), (),
                     notes=("No snapshot exists under "
                            + str(versions_dir or "data/versions")
                            + ". Run `dtp pipeline` to write one.",))
    current = history[at]

    validation = current.validation or {}
    monitoring = _read_alerts(alerts_path)
    alerts = monitoring.get("alerts", [])
    counts = _severity_counts(alerts)
    tiles = (
        Tile("Snapshot", current.version_id, note=current.created_at),
        Tile("Rows", format(current.total_rows, ","),
             note=", ".join(t.table + " " + format(t.rows, ",")
                            for t in current.tables)),
        Tile("Validation", str(validation.get("status", "unknown")),
             note=str(validation.get("passed", "?")) + " of "
                  + str(validation.get("rules", "?")) + " rules held"),
        Tile("Alerts", format(sum(counts.values()), ","),
             note=", ".join(k + " " + str(v) for k, v in counts.items() if v)
                  or "nothing to report"),
    )

    panels = []
    if alerts:
        frame = pd.DataFrame(alerts)
        frame = frame.sort_values(
            "severity", key=lambda s: s.map(SEVERITY_ORDER).fillna(9),
            kind="stable")
        panels.append(Panel(
            key="alerts", title="Alerts, most severe first",
            frame=frame,
            table=_display(frame, drop=("id",)),
            caption=("From the monitoring run of "
                     + str(monitoring.get("checked_at") or "an unknown time")
                     + ", which is not part of this snapshot and may describe a "
                     "different load. Ordered critical, warning, info - not by "
                     "when they were raised."),
        ))

    rows = _history_frame(history)
    panels.append(Panel(
        key="history", title="Snapshot history",
        frame=rows, table=_display(rows),
        caption="Row change is against the snapshot above it. An unchanged "
                "content hash means the load produced byte-identical data.",
    ))

    # `at > 0`, not `len(history) > 1`: pinning the *oldest* snapshot has nothing
    # before it to diff against, and `at - 1` would wrap round to the newest.
    if at > 0:
        previous = history[at - 1]
        changes = pd.DataFrame([{
            "table": d.table, "status": d.status,
            "rows_before": d.rows_before, "rows_after": d.rows_after,
            "row_delta": d.row_delta,
            "columns_added": ", ".join(d.columns_added),
            "columns_removed": ", ".join(d.columns_removed),
            "dtype_changes": "; ".join(k + " " + v
                                       for k, v in d.dtype_changes.items()),
            "new_nulls": "; ".join(k + " " + v
                                   for k, v in d.null_changes.items()),
        } for d in versioning.diff(previous, current)])
        panels.append(Panel(
            key="diff",
            title="This snapshot against " + previous.version_id,
            frame=changes, table=_display(changes),
            caption="Nulls appearing where there were none is the quiet failure: "
                    "the schema matches, the row count matches, and a metric moves.",
        ))

    notes = (str(validation.get("verdict", "")),)
    if validation.get("status") not in (None, "PASS"):
        notes += ("Validation did not pass on this snapshot. Treat every figure "
                  "on the other views as provisional until it does.",)
    return _view("health", tiles, tuple(panels), notes=notes)


def snapshot_ids(versions_dir: Path | None = None) -> list[str]:
    """The snapshot picker's options, newest first.

    `warehouse.available_versions` already sorts newest-first. This exists because
    the renderer reversed it once: the picker opened on the *oldest* snapshot under
    a label promising the newest. Four snapshots of one source extract all total the
    same, so every figure on screen was correct and only the id above them was
    wrong - which is exactly the kind of defect a demo does not reveal. Ordering a
    picker is a decision, so it lives where a test can reach it.
    """
    return [m.version_id for m in warehouse.available_versions(versions_dir)]


def _read_alerts(path: Path | None = None) -> dict[str, Any]:
    """The last monitoring run's payload, or `{}` if it has not run.

    An absent file is not an error: monitoring is a pipeline step, and a snapshot
    written before that step existed has no alerts to show. A malformed one is not
    an error either - this view's job is to report on the data's health, and
    failing to render because its own input is broken defeats that.

    The payload is *not* part of the snapshot, so it can describe a different run
    from the one being displayed. `checked_at` is therefore carried through and
    stated on the panel rather than dropped.
    """
    path = path or Path("reports") / "alerts.json"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    alerts = payload.get("alerts")
    payload["alerts"] = ([a for a in alerts if isinstance(a, dict)]
                         if isinstance(alerts, list) else [])
    return payload


def _severity_counts(alerts: list[dict[str, Any]]) -> dict[str, int]:
    out = {"critical": 0, "warning": 0, "info": 0}
    for alert in alerts:
        key = str(alert.get("severity", "info"))
        out[key] = out.get(key, 0) + 1
    return out


def _history_frame(history: list[Any]) -> pd.DataFrame:
    rows = []
    for i, manifest in enumerate(history):
        before = history[i - 1].total_rows if i else None
        rows.append({
            "version": manifest.version_id,
            "created_at": manifest.created_at,
            "rows": manifest.total_rows,
            "row_delta": None if before is None else manifest.total_rows - before,
            "tables": len(manifest.tables),
            "validation": str((manifest.validation or {}).get("status",
                                                              "unknown")),
        })
    return pd.DataFrame(rows[::-1])          # newest first, like every other list


# --------------------------------------------------------------------------- #
# the dispatcher the app renders through
# --------------------------------------------------------------------------- #

BUILDERS = {"overview": overview, "delivery": delivery,
            "profitability": profitability, "geography": geography,
            "funnel": funnel, "health": data_health}


def build(key: str, **kwargs: Any) -> View:
    """One entry point, so the nav, the app and a test all reach a view the same way."""
    try:
        builder = BUILDERS[key]
    except KeyError:
        raise KeyError("unknown view " + repr(key) + ". Known: "
                       + ", ".join(BUILDERS)) from None
    return builder(**kwargs)
