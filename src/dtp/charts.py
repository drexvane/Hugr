"""Chart layer: Plotly figures, and the rule that picks which one to draw.

This module knows nothing about Streamlit. That is the point rather than a
nicety: roadmap 3.2 requires the Phase 3 agent to *reuse* the dashboard's chart
generation instead of reimplementing it, so every function here takes a
DataFrame plus registry keys and returns a `go.Figure`. The dashboard renders the
figure; the agent will hand the same figure back as JSON. Neither can drift from
the other, because there is only one of them.

`choose()` is the shape rule the agent needs - given the dimensions and metrics a
query actually returned, which chart is honest. It is a plain function over keys
and row counts, so it is testable without rendering anything.

Two conventions the whole file keeps:

- **Colour never carries meaning on its own.** A flagged month also gets a
  different marker and a text label; a bar that misses its benchmark also gets a
  hatch pattern. The palette is Okabe-Ito, which stays distinguishable under the
  common colour-vision deficiencies, but a reader who sees no colour at all still
  gets the distinction.
- **Axis units come from the metric registry**, so a money axis is prefixed `$`
  and a percentage suffixed `%` without any caller passing a format string.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

from . import metrics as M
from .insights import Anomaly

# Okabe-Ito, plus neutrals. Assigned by role, not by taste: the same metric keeps
# the same colour on every view, so a reader learns it once.
INK = "#1b1f24"
MUTED = "#8b96a3"
GRID = "#e7e9ec"
PRIMARY = "#0072B2"       # blue    - the metric being asked about
SECONDARY = "#E69F00"     # orange  - the supporting metric
GOOD = "#009E73"          # green   - better than the benchmark
BAD = "#D55E00"           # vermillion - worse, or flagged
GHOST = "#c9d1d9"         # the context bar behind a value bar

SERIES_COLOURS = (PRIMARY, SECONDARY, GOOD, BAD, "#CC79A7", "#56B4E9")

FONT = ("-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, "
        "Arial, sans-serif")


def _axis(unit: str, title: str) -> dict[str, Any]:
    """Axis config for a metric unit - the registry decides the format."""
    conf: dict[str, Any] = {
        "title": {"text": title, "font": {"size": 12, "color": MUTED}},
        "showgrid": True, "gridcolor": GRID, "zeroline": False,
        "tickfont": {"size": 12, "color": INK},
    }
    if unit == M.MONEY:
        conf["tickprefix"] = "$"
        conf["tickformat"] = "~s"
    elif unit == M.PERCENT:
        conf["ticksuffix"] = "%"
    elif unit == M.COUNT:
        conf["tickformat"] = "~s"
    return conf


def _layout(title: str, subtitle: str = "") -> dict[str, Any]:
    """One layout for every figure: same margins, same type scale, same grid.

    The subtitle is where a gate is named. A chart headed "Revenue by market"
    that silently excludes 7,754 lines is not wrong, but it is unattributed, and
    the reader cannot reconcile it with a raw total they may have seen.
    """
    heading = "<b>" + title + "</b>"
    if subtitle:
        heading += ("<br><span style='font-size:12px;color:" + MUTED + "'>"
                    + subtitle + "</span>")
    return {
        "title": {"text": heading, "font": {"size": 16, "color": INK}, "x": 0,
                  "xanchor": "left", "y": 0.97, "yanchor": "top"},
        "font": {"family": FONT, "size": 13, "color": INK},
        "paper_bgcolor": "white", "plot_bgcolor": "white",
        "margin": {"l": 70, "r": 30, "t": 84 if subtitle else 64, "b": 56},
        "hoverlabel": {"font": {"family": FONT, "size": 13}, "bgcolor": "white",
                       "bordercolor": GRID},
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.0,
                   "xanchor": "right", "x": 1.0, "font": {"size": 12}},
        "showlegend": False,
    }


def _hover(label: str, unit: str, axis: str = "y") -> str:
    """A hover string for one trace. `axis` is "x" on a horizontal bar."""
    value = "%{" + axis
    if unit == M.MONEY:
        return label + ": $" + value + ":,.2f}<extra></extra>"
    if unit == M.PERCENT:
        return label + ": " + value + ":,.1f}%<extra></extra>"
    if unit == M.RATIO:
        return label + ": " + value + ":,.2f}<extra></extra>"
    if unit == M.DAYS:
        return label + ": " + value + ":,.2f} days<extra></extra>"
    return label + ": " + value + ":,.0f}<extra></extra>"


def _gate_note(metric_keys: list[str]) -> str:
    """Name the gate once, in the subtitle, from the metrics actually plotted."""
    gates = {M.metric(k).gate for k in metric_keys}
    notes = []
    if M.REVENUE_GATE in gates:
        notes.append("cancelled and suspected-fraud lines excluded")
    if any(g and g.startswith(M.SHIPMENT_GATE) for g in gates):
        notes.append("shipments that never happened excluded")
    if None in gates and len(gates) > 1:
        notes.append("one series is ungated and labelled so")
    return "; ".join(notes)


# --------------------------------------------------------------------------- #
# the shape rule - what the agent asks before it draws anything
# --------------------------------------------------------------------------- #

TIME_DIMS = frozenset(M.DRILL_PATHS["time"])


def choose(dims: list[str], metric_keys: list[str], n_rows: int = 0) -> str:
    """The chart type a result's shape justifies: the Phase 3 selector, usable now.

    Deliberately a function of shape alone - keys and a row count, no data - so
    it can be tested exhaustively and so the dashboard and the agent cannot
    disagree about what a two-dimension, one-metric result looks like.

    The ordering of the tests matters more than the tests themselves:

    1. A time dimension wins over everything. A monthly result plotted as a bar
       chart sorted by value destroys the one thing a series is for.
    2. Two dimensions and one metric is a heatmap. Grouped bars over 4x5 cells
       are 20 bars a reader has to pair up by colour.
    3. Two *metrics* over one dimension is a scatter, because the question is the
       relationship, not either value's ranking.
    4. Long or numerous labels go horizontal - a rotated 60-character product
       name is unreadable at any font size.
    5. Anything else has no honest chart, and the answer is "table" rather than
       a chart that implies a comparison the shape does not support.
    """
    dims = list(dims)
    metric_keys = list(metric_keys)
    if not metric_keys:
        return "table"
    if not dims:
        return "kpi"
    if len(dims) == 1 and dims[0] in TIME_DIMS:
        return "line"
    if len(dims) == 2 and TIME_DIMS.intersection(dims) and len(metric_keys) == 1:
        return "line_grouped"
    if len(dims) == 2 and len(metric_keys) == 1:
        return "heatmap"
    if len(dims) == 1 and len(metric_keys) >= 2:
        return "scatter"
    if len(dims) == 1:
        if dims[0] == "delay_days":
            return "bar"            # seven ordered integers: keep them in order
        return "hbar" if n_rows > 6 else "bar"
    return "table"


def auto_figure(df: pd.DataFrame, dims: list[str], metric_keys: list[str],
                title: str = "", **kwargs: Any) -> go.Figure | None:
    """Draw whatever `choose` picked. `None` means "no chart is honest: show the rows".

    Returning None rather than raising is the deliberate choice: a caller that
    asked for a chart of a 4-dimension result should render a table, not fail.
    Both callers - the dashboard and the agent - handle None the same way.
    """
    kind = choose(dims, metric_keys, len(df))
    if kind in ("table", "kpi"):
        return None
    if kind == "line":
        return line_series(df, dims[0], metric_keys, title=title, **kwargs)
    if kind == "line_grouped":
        time_dim = next(d for d in dims if d in TIME_DIMS)
        other = next(d for d in dims if d != time_dim)
        return line_grouped(df, time_dim, other, metric_keys[0], title=title,
                            **kwargs)
    if kind == "heatmap":
        return heatmap(df, dims[0], dims[1], metric_keys[0], title=title, **kwargs)
    if kind == "scatter":
        return scatter(df, dims[0], metric_keys[0], metric_keys[1],
                       size_key=metric_keys[2] if len(metric_keys) > 2 else None,
                       title=title, **kwargs)
    return bar(df, dims[0], metric_keys[0], horizontal=(kind == "hbar"),
               title=title, **kwargs)


# --------------------------------------------------------------------------- #
# the figures
# --------------------------------------------------------------------------- #

def line_series(df: pd.DataFrame, time_dim: str, metric_keys: list[str],
                anomalies: list[Anomaly] | None = None, title: str = "",
                subtitle: str = "") -> go.Figure:
    """A time series, one trace per metric, flagged points ringed and labelled.

    Two metrics with different units get a second y-axis; three would need a
    third and the chart stops being readable, so beyond two the units are
    expected to match and the axis is labelled with the first metric's unit.
    """
    fig = go.Figure()
    units = [M.metric(k).unit for k in metric_keys]
    right_axis = len(metric_keys) == 2 and units[0] != units[1]

    for i, key in enumerate(metric_keys):
        met = M.metric(key)
        fig.add_trace(go.Scatter(
            x=df[time_dim], y=df[key], name=met.label, mode="lines+markers",
            line={"color": SERIES_COLOURS[i % len(SERIES_COLOURS)], "width": 2.5},
            marker={"size": 6},
            yaxis="y2" if (right_axis and i == 1) else "y",
            hovertemplate=_hover(met.label, met.unit),
        ))

    if anomalies:
        first = M.metric(metric_keys[0])
        labels = {str(M.fmt_dim(time_dim, v)): v for v in df[time_dim]}
        xs, ys, texts = [], [], []
        for anomaly in anomalies:
            if anomaly.label not in labels:
                continue
            xs.append(labels[anomaly.label])
            ys.append(anomaly.value)
            texts.append(anomaly.label + " " + M.fmt(anomaly.value, first.unit,
                                                     compact=True))
        if xs:
            # Ring, label and legend entry: three signals, only one of them colour.
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="markers+text", name="flagged", text=texts,
                textposition="top center",
                textfont={"size": 11, "color": BAD},
                marker={"size": 15, "color": "rgba(0,0,0,0)", "symbol": "circle",
                        "line": {"color": BAD, "width": 2.5}},
                hovertemplate="flagged: %{text}<extra></extra>",
            ))

    layout = _layout(title or _default_title(metric_keys, time_dim),
                     subtitle or _gate_note(metric_keys))
    layout["xaxis"] = _axis("", M.dimension(time_dim).label)
    layout["yaxis"] = _axis(units[0], M.metric(metric_keys[0]).label)
    if right_axis:
        layout["yaxis2"] = {**_axis(units[1], M.metric(metric_keys[1]).label),
                            "overlaying": "y", "side": "right", "showgrid": False}
    layout["showlegend"] = len(metric_keys) > 1 or bool(anomalies)
    fig.update_layout(**layout)
    return fig


def line_grouped(df: pd.DataFrame, time_dim: str, group_dim: str,
                 metric_key: str, title: str = "", subtitle: str = "",
                 max_groups: int = 6) -> go.Figure:
    """One line per dimension value over time, biggest total first.

    Capped at `max_groups`: beyond six lines a reader is matching colours to a
    legend rather than reading a trend, and the rest are folded into nothing
    rather than drawn - the cap is stated in the subtitle instead of implied.
    """
    met = M.metric(metric_key)
    totals = (df.groupby(group_dim, dropna=False)[metric_key].sum()
              .sort_values(ascending=False))
    keep = list(totals.index[:max_groups])
    fig = go.Figure()
    for i, value in enumerate(keep):
        part = df[df[group_dim] == value].sort_values(time_dim)
        fig.add_trace(go.Scatter(
            x=part[time_dim], y=part[metric_key], name=str(value),
            mode="lines+markers",
            line={"color": SERIES_COLOURS[i % len(SERIES_COLOURS)], "width": 2.2},
            marker={"size": 5}, hovertemplate=str(value) + " - "
            + _hover(met.label, met.unit)))

    note = subtitle or _gate_note([metric_key])
    if len(totals) > len(keep):
        hidden = format(len(totals) - len(keep), ",")
        note = (note + "; " if note else "") + ("top " + str(len(keep)) + " of "
                + format(len(totals), ",") + " shown, " + hidden + " omitted")
    layout = _layout(title or (met.label + " by " + M.dimension(group_dim).label.lower()),
                     note)
    layout["xaxis"] = _axis("", M.dimension(time_dim).label)
    layout["yaxis"] = _axis(met.unit, met.label)
    layout["showlegend"] = True
    fig.update_layout(**layout)
    return fig


def bar(df: pd.DataFrame, dim_key: str, metric_key: str,
        ghost_key: str | None = None, horizontal: bool = False,
        title: str = "", subtitle: str = "", sort: bool = True,
        highlight: pd.Series | None = None, limit: int = 25) -> go.Figure:
    """Ranked bars, optionally over a faint context bar of a second metric.

    `ghost_key` answers the profitability view's actual question: a department
    with the largest profit may be third by revenue, and the pair read together
    is the finding. The ghost is drawn behind at full width in a neutral tone so
    it reads as context rather than as a second competing series.

    `highlight` is a boolean Series aligned to `df` - True bars keep the primary
    colour, False bars get the warning colour *and* a hatch pattern, so the split
    survives a greyscale print.
    """
    met = M.metric(metric_key)
    frame = df.copy()
    if sort and dim_key not in TIME_DIMS and dim_key != "delay_days":
        frame = frame.sort_values(metric_key, ascending=not met.higher_is_better,
                                 na_position="last")
    elif dim_key == "delay_days":
        frame = frame.sort_values(dim_key)
    truncated = max(0, len(frame) - limit)
    frame = frame.head(limit)
    labels = [M.fmt_dim(dim_key, v) for v in frame[dim_key]]

    colours, patterns = PRIMARY, ""
    if highlight is not None:
        flags = highlight.reindex(frame.index).fillna(False).astype(bool)
        colours = [GOOD if ok else BAD for ok in flags]
        patterns = ["" if ok else "/" for ok in flags]

    fig = go.Figure()
    if ghost_key:
        ghost = M.metric(ghost_key)
        fig.add_trace(_bar_trace(labels, frame[ghost_key], ghost.label, GHOST,
                                 "", horizontal, ghost.unit, width=0.78))
    fig.add_trace(_bar_trace(labels, frame[metric_key], met.label, colours,
                             patterns, horizontal, met.unit,
                             width=0.46 if ghost_key else 0.66))

    note = subtitle or _gate_note([metric_key] + ([ghost_key] if ghost_key else []))
    if truncated:
        note = (note + "; " if note else "") + ("top " + str(limit) + ", "
                + format(truncated, ",") + " more not shown")
    layout = _layout(title or (met.label + " by " + M.dimension(dim_key).label.lower()),
                     note)
    value_axis, cat_axis = _axis(met.unit, met.label), _axis("", M.dimension(dim_key).label)
    if horizontal:
        cat_axis["autorange"] = "reversed"
        layout["xaxis"], layout["yaxis"] = value_axis, cat_axis
        layout["margin"] = {**layout["margin"], "l": 210}
        layout["height"] = max(280, 34 * len(frame) + 130)
    else:
        layout["xaxis"], layout["yaxis"] = cat_axis, value_axis
    layout["showlegend"] = bool(ghost_key)
    layout["barmode"] = "overlay"
    fig.update_layout(**layout)
    return fig


def _bar_trace(labels: list[str], values: Any, name: str, colour: Any,
               pattern: Any, horizontal: bool, unit: str,
               width: float) -> go.Bar:
    marker: dict[str, Any] = {"color": colour, "line": {"width": 0}}
    if isinstance(pattern, list) or pattern:
        marker["pattern"] = {"shape": pattern, "fgcolor": "white", "size": 5}
    return go.Bar(
        x=values if horizontal else labels,
        y=labels if horizontal else values,
        name=name, orientation="h" if horizontal else "v",
        marker=marker, width=width,
        hovertemplate=_hover(name, unit, "x" if horizontal else "y"),
    )


def scatter(df: pd.DataFrame, label_dim: str, x_key: str, y_key: str,
            size_key: str | None = None, title: str = "", subtitle: str = "",
            parity: bool = False, quadrants: bool = False,
            limit: int = 400) -> go.Figure:
    """One point per dimension value, positioned by two metrics.

    `quadrants` draws the median of each axis, which is what turns a cloud into a
    reading: high discount and low margin is a different conversation from high
    discount and high margin, and the median split names which quadrant a
    category is in without inventing a threshold.

    `parity` draws y = x, for the funnel: a product above the line converts more
    per view than one below it, whatever the absolute numbers.
    """
    x_met, y_met = M.metric(x_key), M.metric(y_key)
    frame = df.dropna(subset=[x_key, y_key]).head(limit)
    labels = [M.fmt_dim(label_dim, v) for v in frame[label_dim]]

    sizes, size_note = 11, ""
    if size_key and size_key in frame.columns:
        # A null size means "this group has no revenue at all" - the never-ordered
        # products the funnel exists to surface. They get the smallest marker
        # rather than being dropped: a point that is not there cannot be read.
        magnitude = pd.to_numeric(frame[size_key], errors="coerce").abs().fillna(0.0)
        biggest = float(magnitude.max()) if len(magnitude) else 0.0
        if biggest > 0:
            sizes = (9.0 + 26.0 * (magnitude / biggest) ** 0.5).tolist()
        size_note = "point size: " + M.metric(size_key).label.lower()

    custom = frame[[size_key]].to_numpy() if size_key in frame.columns else None
    hover = ("<b>%{text}</b><br>" + x_met.label + ": %{x:,.2f}<br>"
             + y_met.label + ": %{y:,.2f}")
    if custom is not None:
        hover += "<br>" + M.metric(size_key).label + ": %{customdata[0]:,.2f}"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=frame[x_key], y=frame[y_key], mode="markers", text=labels,
        customdata=custom,
        marker={"size": sizes, "color": PRIMARY, "opacity": 0.72,
                "line": {"color": "white", "width": 1}},
        hovertemplate=hover + "<extra></extra>", name=""))

    if parity and len(frame):
        top = float(max(pd.to_numeric(frame[x_key], errors="coerce").max(),
                        pd.to_numeric(frame[y_key], errors="coerce").max()))
        fig.add_trace(go.Scatter(x=[0, top], y=[0, top], mode="lines",
                                 line={"color": MUTED, "width": 1, "dash": "dot"},
                                 hoverinfo="skip", name="parity"))
    if quadrants and len(frame):
        fig.add_vline(x=float(frame[x_key].median()), line_width=1,
                      line_dash="dot", line_color=MUTED)
        fig.add_hline(y=float(frame[y_key].median()), line_width=1,
                      line_dash="dot", line_color=MUTED,
                      annotation_text="medians", annotation_font_size=11,
                      annotation_font_color=MUTED)

    note = subtitle or _gate_note([x_key, y_key])
    if size_note:
        note = (note + "; " if note else "") + size_note
    layout = _layout(title or (y_met.label + " against " + x_met.label.lower()), note)
    layout["xaxis"] = _axis(x_met.unit, x_met.label)
    layout["yaxis"] = _axis(y_met.unit, y_met.label)
    fig.update_layout(**layout)
    return fig


def heatmap(df: pd.DataFrame, row_dim: str, col_dim: str, metric_key: str,
            title: str = "", subtitle: str = "", min_lines: int = 0) -> go.Figure:
    """A grid of one metric over two dimensions, blank where there is no data.

    Blank, not zero. An empty shipping-mode/market cell means nothing shipped that
    way there; a 0% on-time rate means everything did and all of it was late, and
    those two must not share a colour.
    """
    met = M.metric(metric_key)
    frame = df
    if min_lines and "n_lines" in frame.columns:
        frame = frame[frame["n_lines"] >= min_lines]
    grid = frame.pivot_table(index=row_dim, columns=col_dim, values=metric_key,
                             aggfunc="first")
    counts = (frame.pivot_table(index=row_dim, columns=col_dim, values="n_lines",
                               aggfunc="first")
              if "n_lines" in frame.columns else None)

    text = [[M.fmt(v, met.unit, compact=True) if pd.notna(v) else ""
             for v in row] for row in grid.to_numpy()]
    hover = ("<b>%{y} / %{x}</b><br>" + met.label + ": %{text}")
    if counts is not None:
        hover += "<br>lines: %{customdata:,.0f}"

    fig = go.Figure(go.Heatmap(
        z=grid.to_numpy(), x=[M.fmt_dim(col_dim, c) for c in grid.columns],
        y=[M.fmt_dim(row_dim, r) for r in grid.index],
        text=text, texttemplate="%{text}", textfont={"size": 12},
        customdata=counts.to_numpy() if counts is not None else None,
        hovertemplate=hover + "<extra></extra>",
        # Diverging around nothing in particular would imply a neutral point the
        # metric does not have; a single-hue ramp reads as "more" and nothing else.
        colorscale=[[0.0, "#f2f6fa"], [1.0, PRIMARY]],
        colorbar={"title": {"text": met.label, "font": {"size": 12}},
                  "thickness": 12, "outlinewidth": 0},
        hoverongaps=False))

    layout = _layout(title or (met.label + ": " + M.dimension(row_dim).label.lower()
                               + " by " + M.dimension(col_dim).label.lower()),
                     subtitle or _gate_note([metric_key]))
    layout["xaxis"] = {**_axis("", M.dimension(col_dim).label), "showgrid": False}
    layout["yaxis"] = {**_axis("", M.dimension(row_dim).label), "showgrid": False}
    layout["height"] = max(280, 44 * len(grid.index) + 150)
    fig.update_layout(**layout)
    return fig


def _default_title(metric_keys: list[str], dim_key: str) -> str:
    names = " and ".join(M.metric(k).label.lower() for k in metric_keys[:2])
    return names[:1].upper() + names[1:] + " by " + M.dimension(dim_key).label.lower()



