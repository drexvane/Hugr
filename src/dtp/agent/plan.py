"""What the model is allowed to ask for, and the check that runs before it does.

A `Plan` is the whole of the model's output surface: registry metric keys, registry
dimension keys, a grain, a date window, bound filter values, a sort and a limit.
`docs/03-agent-design.md` reason 1 argues why this is a plan rather than SQL; the
short version is that `SUM(Sales)` over this data overstates revenue by
$1,570,305.33, so the obvious query is the wrong one and a model writing SQL would
write it. A plan cannot express the ungated figure at all - `revenue` *means*
`sum(order_item_sales) FILTER (WHERE is_revenue_recognised)` because that is what
the registry says it means.

Three properties this module is responsible for:

* **Every key is checked before anything runs.** `validate()` turns an unknown
  metric into a `Refusal` that names the near misses, not a DuckDB error and not a
  plausible number from a column that means something else.
* **Filter values are checked too.** A value that matches nothing would otherwise
  produce an empty frame and a confident summary of nothing; `unknown_value` names
  what was close instead.
* **No SQL is written here.** `execute()` calls `metrics.aggregate`,
  `metrics.timeseries` or `metrics.funnel`, which remain the only places analytical
  SQL exists. A test pins that.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from .. import metrics as M
from ..warehouse import Warehouse
from .guard import refuse

# Caps, and why each is where it is. All three are the tool schema's business as
# much as the validator's - `tools.py` states them to the model, and `validate()`
# is what makes them true.
MAX_DIMS = 2        # `charts.choose` has an honest chart for 0, 1 and 2
MAX_LIMIT = 200     # a 200-row bar chart is already not an answer
MAX_VALUES = 25     # a filter naming more values than this is not a filter

TIME_GRAINS: tuple[str, ...] = M.DRILL_PATHS["time"]

# Reachable through a plan. `aggregate` covers everything over `order_items`;
# `funnel` is the one purpose-built query, because it spans two tables over its own
# window and cannot be expressed as an aggregate call (design reason 5).
KINDS: tuple[str, ...] = ("aggregate", "funnel")

# The funnel's own grouping choices. Its query joins on a folded key, so it takes a
# single product-path dimension rather than an arbitrary list.
FUNNEL_DIMS: tuple[str, ...] = M.DRILL_PATHS["product"]


@dataclass
class Plan:
    """A resolved question. Unset fields mean "unchanged" only inside `patch()`.

    Mutable on purpose: `validate()` returns a corrected copy, and clamping a date
    or filling in a default sort is a normal part of resolving a question rather
    than an error. What is *not* allowed is validating in place - a caller that
    holds the previous plan for a follow-up must still be holding it afterwards.
    """

    metrics: list[str] = field(default_factory=list)
    by: list[str] = field(default_factory=list)
    grain: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    where: dict[str, list[str]] = field(default_factory=dict)
    order_by: str | None = None
    limit: int | None = None
    min_lines: int = 0
    kind: str = "aggregate"
    funnel_by: str = "product"

    # What `validate()` changed and why - a clamped window, a lowered limit, a
    # grain moved out of `by`. Not part of the tool surface (`_FIELDS` excludes it)
    # and not shown to the model, but it travels with the plan into the answer,
    # because a correction the user is not told about is indistinguishable from a
    # misread question.
    notes: tuple[str, ...] = ()

    # ----------------------------------------------------------------- shape --
    def dims(self) -> list[str]:
        """The grouping columns in the order the frame will carry them.

        The grain leads, because a time series read left to right is the chart the
        result becomes, and `charts.choose` picks a line only when the first
        dimension is temporal.
        """
        return ([self.grain] if self.grain else []) + list(self.by)

    def filters(self) -> M.Filters:
        return M.Filters(date_from=self.date_from, date_to=self.date_to,
                         where={k: list(v) for k, v in self.where.items() if v})

    def to_dict(self) -> dict[str, Any]:
        """The form the model sees and returns. Empty fields are dropped, so the
        plan in the system prompt reads as the question asked rather than as a
        schema with nulls in it."""
        out: dict[str, Any] = {}
        if self.kind != "aggregate":
            out["kind"] = self.kind
            out["funnel_by"] = self.funnel_by
        for name in ("metrics", "by", "grain", "date_from", "date_to", "where",
                     "order_by", "limit", "min_lines"):
            value = getattr(self, name)
            if value:
                out[name] = value
        return out

    def patch(self, changes: dict[str, Any]) -> Plan:
        """This plan with `changes` applied - the whole of session memory.

        "break that down by region" is `{"by": ["region"]}` against whatever was
        asked before (design reason 13). A key that is present and empty *clears*
        the field, because "drop the filter" has to be expressible; a key that is
        absent leaves it alone.
        """
        out = replace(self, metrics=list(self.metrics), by=list(self.by),
                      where={k: list(v) for k, v in self.where.items()})
        for name, value in changes.items():
            if name not in _FIELDS:
                raise refuse("unparseable",
                             "The follow-up named a field this plan does not "
                             "have: " + repr(name) + ".")
            setattr(out, name, value)
        return out

    # ------------------------------------------------------------------ prose --
    def describe(self) -> str:
        """One line naming exactly what ran, for the CLI, the caption and the log.

        Printed beside every answer. A user who cannot see the SQL can still see
        which metric, which grouping and which window produced the number, which is
        the only way a wrong reading of a question is noticed.
        """
        cat = M.get_active_catalog()
        active_metrics = cat.metrics if cat else M.METRICS
        active_dims = cat.dimensions if cat else M.DIMENSIONS

        if self.kind == "funnel":
            return ("views against orders by "
                    + M.dimension(self.funnel_by, cat).label.lower()
                    + ", over the access log's own window")
        default_label = "records" if cat and cat.name != "order_items" else "lines"
        names = [M.metric(k, cat).label for k in self.metrics] or [default_label]
        text = _and(names)
        dims = [M.dimension(k, cat).label.lower() for k in self.dims()]
        if dims:
            text += " by " + _and(dims)
        where = self.filters().describe(cat)
        if where != "no filters":
            text += " (" + where + ")"
        if self.order_by:
            column = self.order_by.lstrip("-")
            label = (active_metrics[column].label if column in active_metrics
                     else column.replace("_", " "))
            text += ", " + ("highest" if self.order_by.startswith("-")
                            else "lowest") + " " + label.lower() + " first"
        if self.limit:
            text += ", top " + str(self.limit)
        if self.min_lines:
            text += ", groups under " + str(self.min_lines) + " lines excluded"
        return text


_FIELDS = frozenset(f.name for f in Plan.__dataclass_fields__.values()) - {"notes"}


def _and(parts: list[str]) -> str:
    """"a", "a and b", "a, b and c" - a caption is read, not parsed."""
    if len(parts) < 3:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# --------------------------------------------------------------------------- #
# building one from a tool call
# --------------------------------------------------------------------------- #

def from_tool_input(payload: dict[str, Any]) -> Plan:
    """A `query_data` tool call as a Plan, refusing anything mis-shaped.

    Type coercion happens here rather than in `validate()` so that the validator
    only ever sees a well-typed plan: a model that sends `"metrics": "revenue"`
    means the single-element list, and a model that sends `"limit": "10"` means the
    integer. Neither is worth a refusal. A model that sends `{"metrics": {...}}`
    is not making a typo, and that is `unparseable`.
    """
    if not isinstance(payload, dict):
        raise refuse("unparseable", "The tool call was not an object.")
    unknown = sorted(set(payload) - _FIELDS)
    if unknown:
        raise refuse("unparseable",
                     "The tool call named " + ", ".join(map(repr, unknown))
                     + ", which is not part of a plan.")
    plan = Plan()
    plan.metrics = _as_list(payload.get("metrics"), "metrics")
    plan.by = _as_list(payload.get("by"), "by")
    plan.grain = _as_str(payload.get("grain"), "grain")
    plan.date_from = _as_str(payload.get("date_from"), "date_from")
    plan.date_to = _as_str(payload.get("date_to"), "date_to")
    plan.order_by = _as_str(payload.get("order_by"), "order_by")
    plan.limit = _as_int(payload.get("limit"), "limit")
    plan.min_lines = _as_int(payload.get("min_lines"), "min_lines") or 0
    plan.kind = _as_str(payload.get("kind"), "kind") or "aggregate"
    plan.funnel_by = _as_str(payload.get("funnel_by"), "funnel_by") or "product"
    where = payload.get("where") or {}
    if not isinstance(where, dict):
        raise refuse("unparseable", "`where` must map a dimension to values.")
    plan.where = {str(k): _as_list(v, "where." + str(k))
                  for k, v in where.items()}
    return plan


def _as_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    raise refuse("unparseable", name + " must be a list of strings.")


def _as_str(value: Any, name: str) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (str, int)):
        return str(value)
    raise refuse("unparseable", name + " must be a string.")


def _as_int(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise refuse("unparseable", name + " must be a whole number.") from None


# --------------------------------------------------------------------------- #
# validation
#
# Everything here happens before a query runs, and every failure is a `Refusal`
# with a code. Two kinds of correction are made silently-with-a-note rather than
# refused: a window that overlaps the data is clamped to it, and a limit above the
# cap is lowered. Both are cases where the question is answerable and only its
# edges were wrong, and a note is how the answer says so.
# --------------------------------------------------------------------------- #

_MAX_TOTAL_DIMS = 3     # beyond this a frame is a cross product, not an answer
_CHART_DIMS = 2         # beyond this `charts.choose` returns a table, not a chart
_VALUE_SCAN = 5000      # distinct values fetched to check a filter against


def validate(plan: Plan, wh: Warehouse | None = None) -> Plan:
    """A plan that is safe to execute, or a `PlanError` carrying the refusal.

    `wh` is optional so the whole validator is testable without data. With a
    warehouse it additionally checks the date window against what the snapshot
    covers and every filter value against what the column actually holds; without
    one it checks everything that is a property of the registry. Nothing it skips
    can produce a wrong number - an unmatched filter value produces an *empty*
    frame, which `execute()` catches either way.
    """
    out = replace(plan, metrics=list(plan.metrics), by=list(plan.by),
                  where={k: list(v) for k, v in plan.where.items()}, notes=())
    notes: list[str] = list(plan.notes)

    if out.kind not in KINDS:
        raise refuse("unparseable", "Unknown plan kind " + repr(out.kind) + ".")
    _check_metrics(out, notes, wh)
    if out.kind == "funnel":
        return _validate_funnel(out, notes)
    _check_dims(out, notes, wh)
    # `where` first: a `{"year": ["2017"]}` filter becomes a date range, and the
    # range it becomes has to face the same window check as one the model wrote.
    _check_where(out, notes, wh)
    _check_dates(out, notes, wh)
    _check_sort(out, notes, wh)
    if out.min_lines < 0:
        raise refuse("unparseable", "`min_lines` cannot be negative.")
    return replace(out, notes=tuple(notes))


def _near(word: str, pool: list[str], n: int = 3) -> list[str]:
    """Registry keys that look like what was asked for.

    A refusal that names the alternatives is the difference between a dead end and
    a second attempt, and `difflib` gets "profits" -> `profit` and "sales" ->
    nothing, which is the right answer in both cases.
    """
    return difflib.get_close_matches(word.lower(), pool, n=n, cutoff=0.6)


def _listing(pool: list[str], limit: int = 8) -> str:
    head = sorted(pool)[:limit]
    tail = "" if len(pool) <= limit else ", and " + str(len(pool) - limit) + " more"
    return ", ".join(head) + tail


# Metrics that do not come from `order_items` and so cannot be grouped by an
# arbitrary dimension: they are the funnel's, over the access log's own window.
_FUNNEL_METRICS = tuple(k for k, m in M.METRICS.items() if not m.aggregatable)

# What `metrics.funnel` actually returns. Wider than `_FUNNEL_METRICS`, because the
# funnel carries its own orders, lines and revenue over the log's window - so
# "views and orders by category" is one query, not a refusal.
_FUNNEL_COLUMNS = (*_FUNNEL_METRICS, "orders", "lines", "revenue")


def _get_active_metrics(wh: Warehouse | None = None) -> dict[str, Any]:
    cat = getattr(wh, "catalog", None) or M.get_active_catalog()
    return cat.metrics if cat else M.METRICS


def _get_active_dimensions(wh: Warehouse | None = None) -> dict[str, Any]:
    cat = getattr(wh, "catalog", None) or M.get_active_catalog()
    return cat.dimensions if cat else M.DIMENSIONS


def _get_active_time_grains(wh: Warehouse | None = None) -> tuple[str, ...]:
    cat = getattr(wh, "catalog", None) or M.get_active_catalog()
    if cat:
        return tuple(cat.drill_paths.get("time", ()))
    return TIME_GRAINS


def _check_metrics(plan: Plan, notes: list[str], wh: Warehouse | None = None) -> None:
    active_metrics = _get_active_metrics(wh)
    lookup: dict[str, str] = {k.lower(): k for k in active_metrics}
    lookup.update({getattr(m, "label", str(m)).lower(): k for k, m in active_metrics.items()})
    lookup.update({k.replace("_", " ").lower(): k for k in active_metrics})

    raw_keys = [lookup.get(str(k).strip().lower(), k) for k in plan.metrics]
    keys = list(dict.fromkeys(raw_keys))          # order-preserving dedupe
    for key in keys:
        if key not in active_metrics:
            near = _near(key, list(active_metrics))
            raise refuse(
                "unknown_metric",
                repr(key) + " is not a metric here.",
                tuple(near) or (_listing(list(active_metrics)),))
    if not keys:
        if "lines" in active_metrics:
            keys = ["lines"]
            notes.append("no metric was named, so this counts order lines")
        else:
            default_key = "row_count" if "row_count" in active_metrics else list(active_metrics.keys())[0]
            keys = [default_key]
            notes.append(f"no metric was named, so this counts {default_key}")
    wrong_shape = [k for k in keys if k in _FUNNEL_METRICS]
    if wrong_shape and plan.kind == "aggregate":
        if all(k in _FUNNEL_COLUMNS for k in keys) and _funnel_shaped(plan):
            plan.kind = "funnel"
            plan.funnel_by = (plan.by or ["product"])[0]
            notes.append("answered from the funnel query, which is where page "
                         "views live")
        else:
            raise refuse(
                "funnel_window",
                ", ".join(sorted(wrong_shape)) + " comes from the access log, "
                "which covers only part of the order history, so it is available "
                "by product, category or department - and beside orders, lines "
                "and revenue over that same window, but not beside a metric the "
                "log cannot see.",
                ("views and orders by category",
                 "the view-to-order rate by product"))
    plan.metrics = keys


def _funnel_shaped(plan: Plan) -> bool:
    """Whether a funnel-metric plan can be served by `metrics.funnel`."""
    return (not plan.grain and not plan.where and not plan.date_from
            and not plan.date_to and len(plan.by) <= 1
            and all(k in FUNNEL_DIMS for k in plan.by))


def _validate_funnel(plan: Plan, notes: list[str]) -> Plan:
    by = [k for k in plan.by if k in FUNNEL_DIMS]
    if by:
        # A follow-up says "by department", not "funnel_by: department". Adopting it
        # is what makes `Plan.patch()` work across the two shapes.
        plan.funnel_by = by[0]
    if plan.funnel_by not in FUNNEL_DIMS:
        raise refuse("unknown_dimension",
                     "The funnel groups by " + ", ".join(FUNNEL_DIMS)
                     + " only, because it joins the log to orders on a product "
                       "name.",
                     FUNNEL_DIMS)
    if plan.grain or plan.date_from or plan.date_to:
        notes.append("the funnel is fixed to the access log's own window, so the "
                     "dates asked for do not apply")
    if plan.where:
        raise refuse("funnel_window",
                     "The funnel query does not take filters: its window and its "
                     "join are what make the rate comparable.",
                     ("views and orders by category",))
    return replace(plan, grain=None, date_from=None, date_to=None, where={},
                   by=[], limit=min(plan.limit or MAX_LIMIT, MAX_LIMIT),
                   notes=tuple(notes))


def _check_dims(plan: Plan, notes: list[str], wh: Warehouse | None = None) -> None:
    active_dims = _get_active_dimensions(wh)
    time_grains = _get_active_time_grains(wh)

    dim_lookup: dict[str, str] = {k.lower(): k for k in active_dims}
    dim_lookup.update({getattr(d, "label", str(d)).lower(): k for k, d in active_dims.items()})
    dim_lookup.update({k.replace("_", " ").lower(): k for k in active_dims})

    if plan.grain is not None:
        if not time_grains:
            notes.append("this dataset has no date column, so time grain was dropped")
            plan.grain = None
        else:
            grain_canon = dim_lookup.get(str(plan.grain).strip().lower(), plan.grain)
            if grain_canon in time_grains:
                plan.grain = grain_canon
            else:
                raise refuse("unknown_dimension",
                             repr(plan.grain) + " is not a time grain.", time_grains)
    keys: list[str] = []
    for raw in dict.fromkeys(plan.by):
        key = dim_lookup.get(str(raw).strip().lower(), raw)
        if key not in active_dims:
            near = _near(key, list(active_dims))
            raise refuse(
                "unknown_dimension",
                repr(key) + " is not a column in this dataset.",
                tuple(near) or (_listing(list(active_dims), limit=15),))
        if key in time_grains:
            # A grain asked for as a grouping is the same request spelled the other
            # way. Moving it keeps `execute()`'s routing to `timeseries()` intact,
            # which is the only call that guarantees one row per period.
            if plan.grain is None:
                plan.grain = key
            elif plan.grain != key:
                notes.append("grouped by " + plan.grain + " rather than by both "
                             + plan.grain + " and " + key)
            continue
        keys.append(key)
    plan.by = keys
    total = len(plan.dims())
    if total > _MAX_TOTAL_DIMS:
        raise refuse(
            "unparseable",
            "That asks for " + str(total) + " groupings at once, which is a cross "
            "product rather than an answer. Ask for at most two, and drill.",
            ("revenue by market and category",
             "monthly revenue by department"))
    if total > _CHART_DIMS:
        notes.append("three groupings render as a table: no chart is honest for "
                     "that shape")


def _check_dates(plan: Plan, notes: list[str],
                 wh: Warehouse | None) -> None:
    lo_asked = _date(plan.date_from, "date_from")
    hi_asked = _date(plan.date_to, "date_to")
    if lo_asked is not None and hi_asked is not None and lo_asked > hi_asked:
        lo_asked, hi_asked = hi_asked, lo_asked
        notes.append("the two dates were the wrong way round and were swapped")
    plan.date_from = None if lo_asked is None else lo_asked.strftime("%Y-%m-%d")
    plan.date_to = None if hi_asked is None else hi_asked.strftime("%Y-%m-%d")

    cat = getattr(wh, "catalog", None) if wh else M.get_active_catalog()
    if cat and not cat.primary_date_col:
        if lo_asked is not None or hi_asked is not None:
            notes.append("this dataset has no date column, so date filters do not apply")
        plan.date_from = None
        plan.date_to = None
        return

    if wh is None or (lo_asked is None and hi_asked is None):
        return

    lo, hi = M.date_bounds(wh)
    window = lo.strftime("%Y-%m-%d") + " to " + hi.strftime("%Y-%m-%d")
    if (hi_asked is not None and hi_asked < lo.normalize()) or \
       (lo_asked is not None and lo_asked > hi):
        raise refuse("out_of_window",
                     "This snapshot covers " + window + ".",
                     ("the last twelve months of the data",
                      "revenue by month for " + str(hi.year)))
    if lo_asked is not None and lo_asked < lo.normalize():
        plan.date_from = lo.strftime("%Y-%m-%d")
        notes.append("start moved to " + plan.date_from
                     + ", the first date in the data")
    if hi_asked is not None and hi_asked > hi:
        plan.date_to = hi.strftime("%Y-%m-%d")
        notes.append("end moved to " + plan.date_to
                     + ", the last date in the data")


def _date(value: str | None, name: str) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        raise refuse("unparseable",
                     name + " was " + repr(value) + ", which is not a date. Use "
                     "YYYY-MM-DD.") from None


def _check_where(plan: Plan, notes: list[str], wh: Warehouse | None) -> None:
    active_dims = _get_active_dimensions(wh)
    time_grains = _get_active_time_grains(wh)
    out: dict[str, list[str]] = {}
    for key, values in plan.where.items():
        if key not in active_dims:
            near = _near(key, list(active_dims))
            raise refuse("unknown_dimension",
                         "Cannot filter on " + repr(key) + ".",
                         tuple(near) or (_listing(list(active_dims), limit=15),))
        if key in time_grains:
            # A period is a window, not a value to match. A four-digit year is
            # unambiguous so it is translated; a month or quarter is not, and
            # guessing a format is how a filter silently matches nothing.
            if key == "year" and all(re.fullmatch(r"\d{4}", str(v))
                                     for v in values):
                years = sorted(str(v) for v in values)
                plan.date_from = plan.date_from or years[0] + "-01-01"
                plan.date_to = plan.date_to or years[-1] + "-12-31"
                notes.append("read " + ", ".join(years) + " as a date range")
                continue
            raise refuse("unparseable",
                         "Periods are set with a date range, not by naming a "
                         + key + ".",
                         ("date_from and date_to, as YYYY-MM-DD",))
        values = [v for v in dict.fromkeys(values) if str(v).strip()]
        if not values:
            notes.append("an empty filter on " + key + " was dropped")
            continue
        if len(values) > MAX_VALUES:
            raise refuse("unparseable",
                         "A filter naming " + str(len(values)) + " values is a "
                         "grouping, not a filter. Group by " + key + " instead.",
                         ("revenue by " + key,))
        out[key] = _canonical(wh, key, values) if wh is not None else values
    plan.where = out


def _canonical(wh: Warehouse, key: str, values: list[str]) -> list[str]:
    """The filter's values as the column spells them, or `unknown_value`.

    `Filters` binds values and compares them to the column as text, so `europe`
    matches nothing while `Europe` matches 55,262 lines. Case-folding here is the
    difference between an answer and a confident summary of an empty frame - which
    is the failure this refusal exists to prevent, not a convenience.
    """
    cat = getattr(wh, "catalog", None) or M.get_active_catalog()
    known = M.dimension_values(wh, key, limit=_VALUE_SCAN, catalog=cat)
    lookup = {str(v).casefold(): str(v) for v in known}
    out: list[str] = []
    for value in values:
        match = lookup.get(str(value).casefold())
        if match is None:
            near = _near(str(value), list(lookup))
            raise refuse(
                "unknown_value",
                repr(value) + " is not a value of " + M.dimension(key, cat).label
                + " in this snapshot.",
                tuple(lookup[n] for n in near) or (_listing(known, limit=6),))
        out.append(match)
    return out


def _check_sort(plan: Plan, notes: list[str], wh: Warehouse | None = None) -> None:
    if plan.limit is not None and plan.limit <= 0:
        plan.limit = None
    if plan.limit is not None and plan.limit > MAX_LIMIT:
        plan.limit = MAX_LIMIT
        notes.append("limited to " + str(MAX_LIMIT) + " rows")

    active_metrics = _get_active_metrics(wh)
    if plan.order_by:
        column = plan.order_by.lstrip("-")
        if column not in plan.metrics and column not in plan.dims() \
                and column != "n_lines":
            if column in active_metrics:
                # Sorting by a figure is asking to see it. Adding the column is
                # what the question meant, and `aggregate` would refuse the sort.
                plan.metrics = [*plan.metrics, column]
                notes.append(column + " was added because the sort is on it")
            else:
                near = _near(column, [*active_metrics, *plan.dims()])
                raise refuse("unknown_metric",
                             "Cannot sort by " + repr(column)
                             + ": it is not part of this answer.",
                             tuple(near) or tuple(plan.metrics))
    elif plan.dims() and not plan.grain and plan.metrics:
        # A ranked question with no stated sort is a ranking by its own measure;
        # a time series is chronological, which `aggregate` already does.
        plan.order_by = "-" + plan.metrics[0]


# --------------------------------------------------------------------------- #
# execution
#
# The only calls out of this module. `metrics` stays the one place analytical SQL
# is written, and `test_the_agent_writes_no_sql` pins that by walking the AST.
# --------------------------------------------------------------------------- #

def execute(wh: Warehouse, plan: Plan) -> pd.DataFrame:
    """Run a validated plan. An empty result is a refusal, not an empty chart.

    A frame with no rows is where a hallucination would otherwise be born: a
    summary written over nothing reads exactly like a summary written over
    something, and the chart beside it is blank. So the emptiness is caught here
    and explained in terms of the thing that caused it - including the ungrouped
    case, where SQL hands back one row of nulls rather than no rows at all.
    """
    cat = getattr(wh, "catalog", None) or M.get_active_catalog()
    if plan.kind == "funnel":
        frame = M.funnel(wh, by=plan.funnel_by,
                         limit=plan.limit or MAX_LIMIT)
        # The fold is how the join worked, not something to read - and here the
        # frame *is* the deliverable, so it goes before the chart and the model
        # rather than at display time as it does on the dashboard.
        frame = frame.drop(columns=[c for c in frame.columns
                                    if c.endswith("_key")])
    elif plan.grain and not plan.by and not plan.order_by and not plan.limit \
            and not plan.min_lines:
        frame = M.timeseries(wh, plan.metrics, grain=plan.grain,
                             filters=plan.filters(), catalog=cat)
    else:
        frame = M.aggregate(wh, plan.metrics, by=plan.dims(),
                            filters=plan.filters(), order_by=plan.order_by,
                            limit=plan.limit, min_lines=plan.min_lines, catalog=cat)
    if frame.empty or _matched_nothing(frame):
        raise _empty(plan)
    return frame


def _matched_nothing(frame: pd.DataFrame) -> bool:
    """True when a frame has rows but they describe nothing.

    An *ungrouped* aggregate over a window that matches no data is one row of
    nulls, not no rows: SQL returns a row for `sum(...)` over zero rows. So
    "revenue in June 2017 for LATAM" arrives as `revenue = NaN, n_lines = 0`, and
    without this it becomes a KPI tile reading "n/a" beside a fluent sentence -
    the same failure `frame.empty` is here to prevent, in the one shape that has no
    chart to look blank.

    `n_lines` is the honest test: every `aggregate` emits it and it is an ungated
    `count(*)`, so a group cannot exist with zero of them. The funnel does not carry
    it and does not need to - it has no ungrouped shape.
    """
    if "n_lines" not in frame.columns:
        return False
    return not pd.to_numeric(frame["n_lines"], errors="coerce").fillna(0).gt(0).any()


def _empty(plan: Plan):
    """The refusal for a well-formed plan that matched nothing."""
    if plan.min_lines:
        return refuse("empty_result",
                      "No group has at least " + str(plan.min_lines)
                      + " order lines under " + plan.filters().describe() + ".",
                      ("the same question without the thin-group cut-off",))
    if plan.where and (plan.date_from or plan.date_to):
        return refuse("empty_result",
                      "Nothing matches " + plan.filters().describe()
                      + ". Each part exists; the combination does not.",
                      ("the same filters over the whole window",))
    if plan.where:
        return refuse("empty_result",
                      "Nothing matches " + plan.filters().describe() + ".",
                      ("one filter at a time",))
    return refuse("empty_result",
                  "The query returned no rows for " + plan.describe() + ".")
