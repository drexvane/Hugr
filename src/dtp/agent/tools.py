"""The tool schema and the two prompts, generated from the registry at call time.

Nothing here is written out by hand, and that is the point (design reason 1.4): a
metric added in Phase 4 becomes askable the same day, and a metric renamed cannot
leave a stale name in a prompt. The generated text is also the *only* description
of the semantic layer the model gets, so `about` strings written for the dashboard's
tooltips do a second job here - which is why the gate is explained in them.

Two prompts, because there are two calls (reason 17):

* `system_prompt()` asks for a plan and is given the registry, the snapshot's real
  window and the current plan for follow-ups. It never sees data.
* `summary_prompt()` asks for one sentence over an already-computed frame, and is
  told plainly that its numbers will be checked and its sentence dropped if they do
  not hold. The truthful instruction is also the cheapest guardrail.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .. import metrics as M
from ..warehouse import Warehouse
from . import plan as P

TOOL_NAME = "query_data"

HEAD_ROWS = 12       # rows of the frame the summary call is shown
MAX_CELL = 60        # characters of a group label; a product name can run long


def _metric_lines(catalog: M.Catalog | None = None) -> list[str]:
    cat = catalog or M.get_active_catalog()
    metrics_map = cat.metrics if cat else M.METRICS
    out = []
    for key, met in metrics_map.items():
        bits = [key, "(" + met.unit + ")", "-", met.label]
        if not getattr(met, "aggregatable", True):
            bits.append("[funnel only]")
        if met.about:
            bits.append("- " + met.about)
        out.append(" ".join(bits))
    return out


def _dimension_lines(catalog: M.Catalog | None = None) -> list[str]:
    cat = catalog or M.get_active_catalog()
    dims_map = cat.dimensions if cat else M.DIMENSIONS
    return [key + " - " + dim.label
            for key, dim in dims_map.items()]


def tool_schema(catalog: M.Catalog | None = None) -> dict[str, Any]:
    """The one tool, with every enum taken from the registry."""
    cat = catalog or M.get_active_catalog()
    metrics_map = cat.metrics if cat else M.METRICS
    dims_map = cat.dimensions if cat else M.DIMENSIONS
    time_grains = list(cat.drill_paths.get("time", P.TIME_GRAINS)) if cat else list(P.TIME_GRAINS)

    metric_keys = list(metrics_map)
    dim_keys = [k for k in dims_map if k not in time_grains]
    return {
        "name": TOOL_NAME,
        "description":
            "Answer a question about the dataset by naming registry metrics "
            "and dimensions. Code turns this into one SQL query with the correct "
            "gates applied; you never write SQL and never state a figure yourself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "enum": metric_keys},
                    "description": "One or more figures to compute.",
                },
                "by": {
                    "type": "array",
                    "items": {"type": "string", "enum": dim_keys},
                    "maxItems": P.MAX_DIMS,
                    "description":
                        "Group by these columns - at most "
                        + str(P.MAX_DIMS) + ". Use `grain` for periods.",
                },
                "grain": {
                    "type": "string",
                    "enum": time_grains,
                    "description":
                        "Set only when the question is about change over time.",
                },
                "date_from": {"type": "string",
                              "description": "YYYY-MM-DD, inclusive."},
                "date_to": {"type": "string",
                            "description": "YYYY-MM-DD, inclusive."},
                "where": {
                    "type": "object",
                    "description":
                        "Dimension key to a list of values to keep, e.g. "
                        "{\"market\": [\"Europe\"]}. Values are matched exactly "
                        "(case-insensitively) against the data.",
                    "additionalProperties": {
                        "type": "array", "items": {"type": "string"}},
                },
                "order_by": {
                    "type": "string",
                    "description":
                        "A metric or dimension key in this plan, prefixed with "
                        "'-' for descending. Ranking questions want '-<metric>'.",
                },
                "limit": {"type": "integer",
                          "description": "Rows to keep, at most "
                                         + str(P.MAX_LIMIT) + "."},
                "min_lines": {
                    "type": "integer",
                    "description":
                        "Exclude groups with fewer than this many records.",
                },
                "kind": {
                    "type": "string",
                    "enum": list(P.KINDS),
                    "description": "Defaults to 'aggregate'. Set to 'funnel' only when asking about conversion.",
                },
                "funnel_by": {
                    "type": "string",
                    "enum": list(P.FUNNEL_DIMS),
                    "description": "Group the funnel by this dimension.",
                },
            },
            "required": ["metrics"],
        },
    }


# The refusals the *model* is asked to declare, rather than the ones code raises.
# Design reason 8: this is the fourth line of defence, not the first - `causal`,
# `forecast` and `personal_data` are already screened in `guard.scope_screen`, and
# the structural ones cannot be reached at all. Asking anyway costs one line and
# buys a better sentence when the screen's patterns miss.
DECLINE_TAG = "CANNOT_ANSWER"

_RULES = """\
Rules, in the order they matter:

1. Never state a figure. You choose which figures to compute; code computes them,
   formats them and puts them on screen. A number you write down that code did not
   compute is the one failure this system is built to prevent.
2. Use the registry keys exactly as listed. Do not invent a metric or a column. If
   the question needs something not listed, do not substitute the nearest thing -
   reply with {tag} and one sentence naming what is missing.
3. Reply with {tag} and one sentence, calling no tool, for a question that:
   asks who an individual is, or for a name, email, street or IP (this data holds
   them; naming a person is not something this platform does); asks *why*
   something happened (it reports what, not why); asks for a forecast (nothing
   here is fitted to predict); asks to change the data (the warehouse is
   read-only); or asks to run SQL.
4. A ranking question wants `order_by` and a `limit`. A "how has X changed"
   question wants `grain`. A question about one slice wants `where`.
5. When ranking a rate, set `min_lines` to about 100 to avoid noise from tiny groups.
"""


def system_prompt(wh: Warehouse | None = None,
                  current: P.Plan | None = None) -> str:
    """The plan call's system prompt: the registry, the window, and the rules.

    `current` is the whole of session memory (design reason 13). It is passed as
    the plan's own JSON, and the model is told that unset fields mean unchanged -
    which is what makes "break that down by region" a two-key tool call instead of
    a re-parse of a conversation it cannot see.
    """
    cat = getattr(wh, "catalog", None) if wh else M.get_active_catalog()
    dataset_name = cat.name if cat else "this dataset"
    time_grains = list(cat.drill_paths.get("time", P.TIME_GRAINS)) if cat else list(P.TIME_GRAINS)
    parts = [
        "You turn a question about " + dataset_name + " into one call of the "
        + TOOL_NAME + " tool. You are the intent parser for a reporting "
        "platform, not its calculator.",
        "",
        _window_line(wh),
        "",
        "Metrics:",
        *("  " + line for line in _metric_lines(cat)),
        "",
        "Dimensions (group by these):",
        *("  " + line for line in _dimension_lines(cat)),
        "",
        "  Periods: use grain=" + "/".join(time_grains)
        + " and date_from/date_to, never where={\"year\": ...}.",
        "",
        _RULES.format(tag=DECLINE_TAG),
    ]
    if current is not None:
        parts += [
            "",
            "This is a follow-up. The previous plan was:",
            "  " + _json(current.to_dict()),
            "Send only the fields that change; anything you omit stays as it is. "
            "Send a field as an empty list or null to clear it.",
        ]
    return "\n".join(parts)


def _window_line(wh: Warehouse | None) -> str:
    """The window, read from the data rather than hardcoded.

    A prompt that names a window the snapshot does not have is how an agent starts
    answering about periods that do not exist, so this is one of the few strings
    here that is worth a query.
    """
    if wh is None:
        return ("The snapshot covers a fixed window; a date outside it is "
                "outside the data.")
    try:
        lo, hi = M.date_bounds(wh)
        return ("This snapshot covers " + lo.strftime("%Y-%m-%d") + " to "
                + hi.strftime("%Y-%m-%d") + ".")
    except Exception:
        return "This dataset is loaded into the warehouse."


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


# --------------------------------------------------------------------------- #
# the summary call
#
# This is the only place data leaves the process, and it leaves as the head of an
# aggregated frame: group labels and metric values, formatted the way the screen
# will show them (design reason 20). No row-level data, and no customer name,
# street or IP, because those are not dimensions and so cannot be in a plan.
# --------------------------------------------------------------------------- #

SUMMARY_SYSTEM = """\
You write one sentence about a table that has already been computed. Two, if the
second earns its place.

You are not being asked for a figure: every number you write will be extracted and
matched against the table before anyone sees it, and a single number that does not
match discards your whole sentence and replaces it with a templated one. So quote
values from the table exactly as they are printed there, or write a sentence with
no numbers in it at all.

What is worth saying: which group leads and by how much, whether the trend is up or
down, where the outlier is, whether the spread is wide or flat. What is not: a
restatement of the question, a definition of the metric, advice, or a cause. This
platform reports what happened; it does not know why.

Name only groups that appear in the table. Do not round differently from the table.
Do not mention the query, the plan, the tool or yourself."""


def frame_digest(frame: pd.DataFrame, plan: P.Plan,
                 rows: int = HEAD_ROWS) -> str:
    """The frame as the summary call sees it: formatted, truncated, and labelled.

    Formatted rather than raw because the verifier checks the model's numbers at
    the precision `fmt_metric` prints - so showing the model anything else invites
    a sentence that is true and unverifiable. Truncated because a summary needs the
    shape and the extremes, not 200 rows, and because this is the payload that
    leaves the process.
    """
    shown = frame.head(rows)
    header = [_label(c) for c in frame.columns]
    lines = [" | ".join(header)]
    for _, row in shown.iterrows():
        cells = []
        for column in frame.columns:
            value = row[column]
            if column in M.METRICS:
                cells.append(M.fmt_metric(column, value))
            elif column in M.DIMENSIONS:
                cells.append(str(M.fmt_dim(column, value))[:MAX_CELL])
            elif column == "n_lines":
                cells.append(M.fmt(value))
            else:
                cells.append(str(value)[:MAX_CELL])
        lines.append(" | ".join(cells))
    if len(frame) > rows:
        lines.append("... " + str(len(frame) - rows) + " more rows, sorted the "
                     "same way")
    return "\n".join([
        "Question resolved to: " + plan.describe(),
        "",
        *lines,
        "",
        "Rows: " + str(len(frame)) + ".",
    ])


def _label(column: str) -> str:
    if column in M.METRICS:
        return M.metric(column).label
    if column in M.DIMENSIONS:
        return M.dimension(column).label
    return "Lines" if column == "n_lines" else column
