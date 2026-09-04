"""Analysis layer: anomalies, comparisons and the sentences that describe them.

Roadmap 2.3 asks for three things - anomaly highlighting, comparative views and
narrative annotation. All three are here rather than in the chart code, for one
reason: a figure and its caption must be computed from the same frame. If the
annotation is generated anywhere other than beside the numbers it describes, it
can contradict the chart it sits under, and the reader has no way to tell which
of the two is wrong.

**This module writes no SQL.** Every number comes back through
`metrics.aggregate` / `totals` / `timeseries`, so the gates travel with it and an
insight cannot quietly widen its own row set. The one thing that changes here is
pandas arithmetic over frames those functions already returned.

Anomalies use a median/MAD robust z-score rather than mean and standard
deviation. The distinction matters in exactly the case the feature exists for: a
single extreme month inflates σ enough to keep its own z-score under the
threshold, so the mean/σ version is at its least sensitive precisely when it is
most needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from . import metrics as M
from .warehouse import Warehouse

# 0.6745 is the constant that puts MAD on the same scale as a standard deviation
# for normally distributed data, so 3.5 means "three and a half sigma equivalent".
# Iglewicz & Hoaglin's threshold, kept as the default rather than tuned to this
# dataset - a threshold fitted to the data it is judging finds what it was told to.
MAD_TO_SIGMA = 0.6745
Z_THRESHOLD = 3.5

# When MAD is 0 the series is more than half identical values. The mean absolute
# deviation is the documented fallback, on its own scale (1.253314).
MEANAD_TO_SIGMA = 1.253314


@dataclass(frozen=True)
class Anomaly:
    """One flagged point, carrying enough to write its own caption."""

    label: str
    value: float
    z: float
    median: float
    direction: str          # "above" | "below"

    @property
    def pct_from_median(self) -> float | None:
        if not self.median:
            return None
        return 100.0 * (self.value - self.median) / abs(self.median)


def robust_z(values: pd.Series) -> pd.Series:
    """Signed robust z-scores, NaN-safe, aligned to the input index.

    A series of fewer than 4 points gets all-NaN back rather than a score: with
    three months, one of them *is* the median and the other two are the whole
    spread, so any threshold either flags nothing or flags a third of the series.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    clean = numeric.dropna()
    out = pd.Series(float("nan"), index=values.index, dtype="float64")
    if len(clean) < 4:
        return out
    median = float(clean.median())
    mad = float((clean - median).abs().median())
    if mad > 0:
        scale = mad / MAD_TO_SIGMA
    else:
        mean_ad = float((clean - median).abs().mean())
        if mean_ad <= 0:
            return out          # every value identical: nothing is an outlier
        scale = mean_ad / MEANAD_TO_SIGMA
    out.loc[clean.index] = (clean - median) / scale
    return out


def find_anomalies(df: pd.DataFrame, value_col: str, label_col: str | None = None,
                   threshold: float = Z_THRESHOLD) -> list[Anomaly]:
    """Flagged points, worst first. Empty list when the series is well behaved.

    `label_col` is a dimension key when it is one, so a flagged month is labelled
    "Jan 2018" rather than with the timestamp the group-by returned.
    """
    if df.empty or value_col not in df.columns:
        return []
    z = robust_z(df[value_col])
    median = float(pd.to_numeric(df[value_col], errors="coerce").median())
    flagged = z.abs() >= threshold
    found: list[Anomaly] = []
    for idx in z[flagged].index:
        raw = df.loc[idx, label_col] if label_col else idx
        label = M.fmt_dim(label_col, raw) if label_col in M.DIMENSIONS else str(raw)
        found.append(Anomaly(label=_short_label(label),
                             value=float(df.loc[idx, value_col]),
                             z=float(z.loc[idx]), median=median,
                             direction="above" if z.loc[idx] > 0 else "below"))
    return sorted(found, key=lambda a: abs(a.z), reverse=True)


def flag_anomalies(df: pd.DataFrame, value_col: str,
                   threshold: float = Z_THRESHOLD) -> pd.DataFrame:
    """The same frame with `z` and `is_anomaly` columns, for a chart to style.

    A copy, not a view: the caller's frame came out of a query and other panels
    on the page may still be reading it.
    """
    out = df.copy()
    out["z"] = robust_z(out[value_col]) if value_col in out.columns else float("nan")
    out["is_anomaly"] = out["z"].abs() >= threshold
    return out


# --------------------------------------------------------------------------- #
# comparison 1: period over period
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Comparison:
    metric: str
    current: float | None
    previous: float | None
    window: str
    prior_window: str

    @property
    def delta(self) -> float | None:
        if self.current is None or self.previous is None:
            return None
        return float(self.current) - float(self.previous)

    @property
    def delta_pct(self) -> float | None:
        """None, not zero, when the prior period is empty or zero.

        A division that silently returns 0% reads as "no change" on a tile, which
        is the opposite of "there was nothing to compare against".
        """
        if self.delta is None or not self.previous:
            return None
        return 100.0 * self.delta / abs(float(self.previous))

    @property
    def improved(self) -> bool | None:
        if self.delta is None:
            return None
        better = M.metric(self.metric).higher_is_better
        return self.delta >= 0 if better else self.delta <= 0


def prior_window(date_from: str, date_to: str) -> tuple[str, str]:
    """The same-length window ending the day before `date_from`.

    Same length, immediately prior, no calendar cleverness: comparing a 31-day
    January against a 28-day February would move the number for a reason that has
    nothing to do with the business.
    """
    lo = pd.Timestamp(date_from).normalize()
    hi = pd.Timestamp(date_to).normalize()
    if hi < lo:
        lo, hi = hi, lo
    span = hi - lo
    prev_hi = lo - pd.Timedelta(days=1)
    prev_lo = prev_hi - span
    return prev_lo.strftime("%Y-%m-%d"), prev_hi.strftime("%Y-%m-%d")


def period_over_period(wh: Warehouse, metric_keys: list[str],
                       filters: M.Filters | None = None) -> list[Comparison]:
    """Each metric for the filtered window and for the same length before it.

    When the caller has set no dates, the window is the whole fact table and
    there is nothing before it, so `previous` is None and the tiles say so rather
    than inventing a baseline.
    """
    filters = filters or M.Filters()
    now = M.totals(wh, metric_keys, filters=filters)
    if not (filters.date_from and filters.date_to):
        return [Comparison(k, _num(now.get(k)), None, filters.describe(),
                           "no prior window") for k in metric_keys]

    prev_lo, prev_hi = prior_window(filters.date_from, filters.date_to)
    before_filters = M.Filters(date_from=prev_lo, date_to=prev_hi,
                               where=dict(filters.where))
    before = M.totals(wh, metric_keys, filters=before_filters)
    window = filters.date_from + " to " + filters.date_to
    return [Comparison(k, _num(now.get(k)), _num(before.get(k)),
                       window, prev_lo + " to " + prev_hi) for k in metric_keys]


# --------------------------------------------------------------------------- #
# comparison 2: a child against the parent it sits inside
# --------------------------------------------------------------------------- #

def benchmark_vs_parent(wh: Warehouse, metric_key: str, child: str = "category",
                        parent: str = "department",
                        filters: M.Filters | None = None,
                        min_lines: int = 0) -> pd.DataFrame:
    """Each child's value beside its own parent's, and the gap between them.

    "Golf Bags & Carts has a 17.5% margin" is not yet a finding - the question is
    whether that beats the department it is sold in. The parent figure is
    recomputed over the same filters rather than averaged from the children: a
    mean of category margins weights a 61-line category the same as a 33,000-line
    one and is not the department's margin at all.
    """
    M.metric(metric_key)
    rows = M.aggregate(wh, [metric_key], by=[parent, child], filters=filters,
                       min_lines=min_lines)
    parents = M.aggregate(wh, [metric_key], by=[parent], filters=filters)
    if rows.empty:
        return rows.assign(parent_value=[], gap=[])
    merged = rows.merge(parents[[parent, metric_key]].rename(
        columns={metric_key: "parent_value"}), on=parent, how="left")
    merged["gap"] = merged[metric_key] - merged["parent_value"]
    better = M.metric(metric_key).higher_is_better
    merged["beats_parent"] = merged["gap"] >= 0 if better else merged["gap"] <= 0
    return merged.sort_values("gap", ascending=not better,
                              na_position="last").reset_index(drop=True)


def loss_makers(wh: Warehouse, by: str = "product",
                filters: M.Filters | None = None, limit: int = 20,
                min_lines: int = 0) -> pd.DataFrame:
    """Groups whose gated profit is negative, deepest loss first.

    Gated: a cancelled order's recorded loss was never realised, and a list that
    counts it sends someone to renegotiate a price that was never paid.
    """
    df = M.aggregate(wh, ["profit", "revenue", "margin_pct", "units"], by=[by],
                     filters=filters, order_by="profit", min_lines=min_lines)
    return df[df["profit"] < 0].head(limit).reset_index(drop=True)


def movers(wh: Warehouse, metric_key: str, by: str,
           filters: M.Filters | None = None, limit: int = 10,
           min_lines: int = 0) -> pd.DataFrame:
    """Period-over-period change per dimension value, biggest absolute move first.

    An outer merge, deliberately: a value that appears in only one of the two
    windows is a mover - arriving or vanishing is the largest move there is - and
    an inner join would drop exactly those rows.
    """
    filters = filters or M.Filters()
    if not (filters.date_from and filters.date_to):
        raise ValueError("movers needs a dated window to compare against")
    prev_lo, prev_hi = prior_window(filters.date_from, filters.date_to)
    now = M.aggregate(wh, [metric_key], by=[by], filters=filters,
                      min_lines=min_lines)
    before = M.aggregate(wh, [metric_key], by=[by], min_lines=min_lines,
                         filters=M.Filters(date_from=prev_lo, date_to=prev_hi,
                                           where=dict(filters.where)))
    merged = now[[by, metric_key]].merge(
        before[[by, metric_key]], on=by, how="outer",
        suffixes=("", "_prior")).fillna({metric_key: 0.0,
                                         metric_key + "_prior": 0.0})
    merged["delta"] = merged[metric_key] - merged[metric_key + "_prior"]
    merged["delta_pct"] = [
        None if not p else 100.0 * d / abs(p)
        for d, p in zip(merged["delta"], merged[metric_key + "_prior"])
    ]
    merged["abs_delta"] = merged["delta"].abs()
    return (merged.sort_values("abs_delta", ascending=False)
            .drop(columns="abs_delta").head(limit).reset_index(drop=True))


# --------------------------------------------------------------------------- #
# composition: is the series still measuring the same thing?
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Shift:
    """A step change in *how orders are composed*, not in how much they sold.

    This exists because of what the real extract does: from 2017-10 an order
    carries one line where it previously carried three, so monthly revenue falls
    by two thirds while revenue per line actually rises. A revenue chart with no
    warning tells the reader that sales collapsed, and the anomaly flag agrees
    with it - both are describing a change in the shape of the data.
    """

    at: str                 # first period of the later regime, formatted
    before: float
    after: float
    n_before: int
    n_after: int
    metric: str = "lines_per_order"

    @property
    def ratio(self) -> float | None:
        return None if not self.before else self.after / self.before


def composition_shift(wh: Warehouse, grain: str = "month",
                      metric_key: str = "lines_per_order",
                      threshold: float = 0.25, min_side: int = 3,
                      filters: M.Filters | None = None) -> Shift | None:
    """The single largest step change in `metric_key`, or None if the series is stable.

    A search over split points rather than a hardcoded date: the boundary in this
    extract happens to be 2017-10, but a check that knows that date only works on
    this file. Each candidate split is scored by how far the later median sits
    from the earlier one, and the best split is reported only if it clears
    `threshold` - 25% by default, far outside the 4% the monthly ratio otherwise
    varies by here.
    """
    series = M.timeseries(wh, [metric_key], grain=grain, filters=filters)
    values = pd.to_numeric(series[metric_key], errors="coerce")
    if len(values) < 2 * min_side or values.isna().all():
        return None

    best: tuple[float, int] | None = None
    for i in range(min_side, len(values) - min_side + 1):
        lo, hi = values.iloc[:i].median(), values.iloc[i:].median()
        if not lo or pd.isna(lo) or pd.isna(hi):
            continue
        score = abs(hi / lo - 1.0)
        if best is None or score > best[0]:
            best = (score, i)
    if best is None or best[0] < threshold:
        return None

    _, at = best
    return Shift(at=M.fmt_dim(grain, series[grain].iloc[at]),
                 before=float(values.iloc[:at].median()),
                 after=float(values.iloc[at:].median()),
                 n_before=at, n_after=len(values) - at, metric=metric_key)


def say_shift(shift: Shift | None) -> str:
    """Empty string when there is nothing to warn about - not a reassuring sentence.

    A caption that says "the series is stable" on every well-behaved chart trains
    the reader to skip the line, which is the line that matters when it changes.
    """
    if shift is None:
        return ""
    met = M.metric(shift.metric)
    text = ("From " + shift.at + ", " + met.label.lower() + " is "
            + M.fmt(shift.after, met.unit) + " against "
            + M.fmt(shift.before, met.unit) + " over the "
            + format(shift.n_before, ",") + " periods before it.")
    if shift.ratio is not None and shift.ratio < 1:
        text += (" Totals fall across that boundary for a reason that is not "
                 "commercial, so compare per-order or per-line figures rather "
                 "than monthly sums, or filter to one side of it.")
    elif shift.ratio is not None:
        text += (" Totals rise across that boundary without prices or units "
                 "changing, so compare per-order figures across it.")
    return text


# --------------------------------------------------------------------------- #
# confounding: is this dimension comparable across time at all?
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Confound:
    """How far a dimension's values are separated in time rather than mixed.

    The real extract is ordered by market, and its dates run with the row order,
    so 2015-Q1 is entirely LATAM and 2015-Q3 entirely Europe. Nothing is missing
    and no rule fails - but "revenue by market" over the whole window is largely
    a chart of which months each market's block happens to cover, and a
    period-over-period comparison by market compares two different windows.
    """

    dimension: str
    grain: str
    n_values: int
    periods: int
    multi_value_periods: int
    median_top_share: float     # median over periods of the largest value's share

    @property
    def single_value_periods(self) -> int:
        return self.periods - self.multi_value_periods

    @property
    def is_time_partitioned(self) -> bool:
        """A high bar, and nothing in this data sits near it.

        At monthly grain the real extract's `market` scores 100.0 and the next
        most concentrated dimension - shipping mode - scores 59.7, so the
        threshold separates a genuine time partition from ordinary imbalance
        rather than sitting in the middle of a continuum.
        """
        return self.median_top_share >= 90.0


def confounding(wh: Warehouse, dim_key: str, grain: str = "month",
                filters: M.Filters | None = None) -> Confound:
    """Measure, per period, how much of the data sits in one value of `dim_key`.

    Counted on ungated rows - `n_lines`, which `aggregate` always returns. This is
    a question about the shape of the extract, not about revenue, and gating it
    would let a cancelled-heavy period look more concentrated than it is.
    """
    M.dimension(dim_key)
    df = M.aggregate(wh, [], by=[grain, dim_key], filters=filters)
    if df.empty:
        return Confound(dim_key, grain, 0, 0, 0, 0.0)
    shares, multi = [], 0
    for _, group in df.groupby(grain, dropna=False):
        total = float(group["n_lines"].sum())
        if not total:
            continue
        shares.append(100.0 * float(group["n_lines"].max()) / total)
        multi += int(len(group) > 1)
    return Confound(dimension=dim_key, grain=grain,
                    n_values=int(df[dim_key].nunique(dropna=False)),
                    periods=len(shares), multi_value_periods=multi,
                    median_top_share=float(pd.Series(shares).median())
                    if shares else 0.0)


def say_confound(conf: Confound) -> str:
    """A warning, or nothing. Silence is the common case and should stay cheap."""
    if not conf.is_time_partitioned:
        return ""
    dim = M.dimension(conf.dimension)
    return (dim.label + " is separated in time rather than mixed through it: "
            + format(conf.single_value_periods, ",") + " of "
            + format(conf.periods, ",") + " " + _form(conf.periods, conf.grain)
            + " contain exactly one " + dim.label.lower() + ", and the median "
            + conf.grain + " has " + format(conf.median_top_share, ".0f")
            + "% of its lines in one. Totals by " + dim.label.lower()
            + " are largely a statement about which " + _plural(conf.grain)
            + " each one covers; filtering to one narrows the date range with it, "
            "and a period-over-period comparison across "
            + dim.label.lower() + " compares two different windows.")


def _plural(word: str) -> str:
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def _form(n: int, word: str) -> str:
    """The singular or plural form, whichever `n` calls for."""
    return word if n == 1 else _plural(word)


def _count_of(n: int, word: str) -> str:
    """"1 product" / "4 products" - a template that cannot say "1 products"."""
    return format(n, ",") + " " + _form(n, word)


# --------------------------------------------------------------------------- #
# narrative - templated, computed from the frame the chart is drawn from
# --------------------------------------------------------------------------- #

def say_anomalies(found: list[Anomaly], metric_key: str,
                  noun: str = "month") -> str:
    """One sentence about the flagged points, or about their absence."""
    unit = M.metric(metric_key).unit
    label = M.metric(metric_key).label.lower()
    if not found:
        return ("No " + noun + " in this series departs from the median by more "
                "than " + format(Z_THRESHOLD, ".1f") + " robust standard "
                "deviations, so nothing is flagged.")
    worst = found[0]
    text = (worst.label + " is the outlier: " + label + " of "
            + M.fmt(worst.value, unit, compact=True) + " against a median of "
            + M.fmt(worst.median, unit, compact=True))
    pct = worst.pct_from_median
    if pct is not None:
        text += " (" + M.fmt_delta(pct, M.PERCENT) + ")"
    text += ", a robust z of " + format(worst.z, "+.1f") + "."
    if len(found) > 1:
        others = ", ".join(a.label for a in found[1:4])
        text += (" " + format(len(found) - 1, ",") + " other "
                 + (noun if len(found) == 2 else noun + "s") + " also flagged: "
                 + others + ".")
    return text


def say_comparison(comp: Comparison) -> str:
    met = M.metric(comp.metric)
    if comp.previous is None:
        return (met.label + " is " + M.fmt(comp.current, met.unit, compact=True)
                + " over " + comp.window + ". No prior window of the same length "
                "is available, so no change is shown.")
    direction = "up" if (comp.delta or 0) >= 0 else "down"
    text = (met.label + " is " + direction + " "
            + M.fmt_change(abs(comp.delta or 0), met.unit, compact=True))
    if comp.delta_pct is not None:
        text += " (" + M.fmt_delta(comp.delta_pct, M.PERCENT) + ")"
    text += (" against " + comp.prior_window + ": "
             + M.fmt(comp.current, met.unit, compact=True) + " versus "
             + M.fmt(comp.previous, met.unit, compact=True) + ".")
    if comp.improved is False:
        text += " That is the wrong direction for this metric."
    return text


def say_ranking(df: pd.DataFrame, metric_key: str, dim_key: str,
                top: int = 3) -> str:
    """Names the leaders and their share, from the frame the chart plots."""
    if df.empty or metric_key not in df.columns:
        return "No rows match these filters."
    met, dim = M.metric(metric_key), M.dimension(dim_key)
    ranked = df.sort_values(metric_key, ascending=not met.higher_is_better)
    head = ranked.head(top)
    names = ", ".join(_short_label(str(v)) for v in head[dim_key])
    text = (_plural(dim.label) + " ranked by " + met.label.lower() + ": " + names
            + (" lead with " if len(head) > 1 else " leads with ")
            + M.fmt(head[metric_key].iloc[0], met.unit, compact=True) + " at the top")
    values = pd.to_numeric(ranked[metric_key], errors="coerce")
    total, head_total = values.sum(), pd.to_numeric(head[metric_key],
                                                    errors="coerce").sum()
    # "3 of 3 categories, 100% of the revenue" tells the reader nothing they
    # cannot see. The share clause only earns its place when something is left out.
    if met.unit in (M.MONEY, M.COUNT) and total and len(head) < len(ranked):
        if (values >= 0).all() and total > 0:
            text += (", " + format(100.0 * head_total / total, ".1f") + "% of the "
                     + met.label.lower() + " on this chart")
        elif (values <= 0).all():
            # A share of a negative total reads as a share of a positive one -
            # "100% of the profit" for a chart of losses. State the amount, and
            # unsigned, because "lost" already carries the direction.
            text += (", " + M.fmt(abs(head_total), met.unit, compact=True)
                     + " of the " + M.fmt(abs(total), met.unit, compact=True)
                     + " lost on this chart")
        # Mixed signs: a percentage of a total that partly cancels itself out is
        # not a share of anything, so no clause at all.
    return text + "."


def say_losses(df: pd.DataFrame, by: str = "product") -> str:
    """The deepest loss first - which is the opposite of what `say_ranking` names.

    `say_ranking` orders by the metric's own direction, so ranking `loss_makers()`
    by profit puts the *shallowest* loss at the top and captions a chart of the
    worst groups with the least bad of them. The losses view therefore gets its own
    sentence rather than a parameter on the other one.

    The amounts are stated unsigned, because "lost" and "deepest" already carry the
    direction and "-$100.00 lost" reads as a loss that was recovered.
    """
    if df.empty or "profit" not in df.columns:
        return "No " + M.dimension(by).label.lower() + " loses money under these filters."
    ranked = df.sort_values("profit")          # most negative first
    worst = ranked.iloc[0]
    total = float(pd.to_numeric(ranked["profit"], errors="coerce").sum())
    label = M.dimension(by).label.lower()
    name = _short_label(str(worst[by]))
    on_sales = ""
    if "revenue" in ranked.columns and _num(worst.get("revenue")):
        on_sales = " on " + M.fmt(worst["revenue"], M.MONEY, compact=True) + " of sales"
    if len(ranked) == 1:
        return (name + " is the only " + label + " losing money: "
                + M.fmt(abs(float(worst["profit"])), M.MONEY) + on_sales + ".")
    return (_count_of(len(ranked), label) + " lose money, "
            + M.fmt(abs(total), M.MONEY, compact=True) + " in all. " + name
            + " is the deepest at "
            + M.fmt(abs(float(worst["profit"])), M.MONEY) + on_sales + ".")


def say_benchmark(df: pd.DataFrame, metric_key: str, child: str = "category",
                  parent: str = "department") -> str:
    if df.empty or "gap" not in df.columns:
        return "No rows match these filters."
    met = M.metric(metric_key)
    best, worst = df.iloc[0], df.iloc[-1]
    beats = int(df["beats_parent"].sum())
    lead = (_short_label(str(best[child])) + " beats its "
            + M.dimension(parent).label.lower() + " by "
            + M.fmt_change(abs(best["gap"]), met.unit) + " ("
            + M.fmt(best[metric_key], met.unit) + " against "
            + M.fmt(best["parent_value"], met.unit) + ")")
    if len(df) == 1:
        # One row: best and worst are the same row, and naming it twice produces
        # a sentence that says it both beats and trails itself.
        return lead + "."
    return (lead + "; " + _short_label(str(worst[child])) + " trails its own by "
            + M.fmt_change(abs(worst["gap"]), met.unit) + ". "
            + format(beats, ",") + " of "
            + _count_of(len(df), M.dimension(child).label.lower())
            + " are on the right side of the line.")


def say_trend(df: pd.DataFrame, time_dim: str, group_dim: str,
              metric_key: str) -> str:
    """The largest group's own path: where it starts, where it ends, its peak.

    A ranking sentence under a multi-line chart repeats what the bar chart above it
    already said. What a series adds is shape, so this names the biggest line's
    endpoints and high point - the three values a reader would otherwise trace with
    a finger.

    "Largest" is by summed total, which is how `charts.line_grouped` decides which
    lines to draw, so the group named is always one that is on screen.
    """
    if df.empty or metric_key not in df.columns:
        return "No rows match these filters."
    met = M.metric(metric_key)
    totals = df.groupby(group_dim, dropna=False)[metric_key].sum()
    top = totals.sort_values(ascending=False).index[0]
    part = df[df[group_dim] == top].sort_values(time_dim)
    values = pd.to_numeric(part[metric_key], errors="coerce")
    if values.notna().sum() == 0:
        return "No " + met.label.lower() + " is recorded over this window."
    at = lambda row: M.fmt_dim(time_dim, row[time_dim])                  # noqa: E731
    first, last = part.iloc[0], part.iloc[-1]
    peak = part.loc[values.idxmax()]
    text = (_short_label(str(top)) + " is the largest: " + met.label.lower() + " of "
            + M.fmt(first[metric_key], met.unit, compact=True) + " in " + at(first))
    if len(part) > 1:
        text += (" against " + M.fmt(last[metric_key], met.unit, compact=True)
                 + " in " + at(last))
    if len(part) > 2 and at(peak) not in (at(first), at(last)):
        text += (", peaking at " + M.fmt(peak[metric_key], met.unit, compact=True)
                 + " in " + at(peak))
    periods = df[time_dim].nunique()
    return (text + ". " + _count_of(int(totals.size),
                                    M.dimension(group_dim).label.lower())
            + " over " + _count_of(int(periods),
                                   M.dimension(time_dim).label.lower()) + ".")


def say_spread(df: pd.DataFrame, dim_key: str, count_key: str,
               cut: float = 0.0) -> str:
    """The commonest value of an ordered dimension, and the share at or below `cut`.

    A distribution over seven integers has two readings a bar chart does not state:
    which bar is tallest, and how much of the mass sits on the good side of zero.
    Both come from the frame the chart plots, so neither can disagree with it.
    """
    if df.empty or count_key not in df.columns:
        return "No rows match these filters."
    counts = pd.to_numeric(df[count_key], errors="coerce").fillna(0.0)
    total = float(counts.sum())
    if not total:
        return "No rows match these filters."
    values = pd.to_numeric(df[dim_key], errors="coerce")
    top = int(counts.to_numpy().argmax())
    dim, met = M.dimension(dim_key), M.metric(count_key)
    text = ("Most " + met.label.lower() + " sit at "
            + M.fmt_dim(dim_key, df[dim_key].iloc[top]) + " "
            + dim.label.split(" (")[0].lower() + " ("
            + M.fmt(counts.iloc[top], M.COUNT) + " of " + M.fmt(total, M.COUNT)
            + ")")
    within = float(counts[values <= cut].sum())
    return (text + "; " + format(100.0 * within / total, ".1f")
            + "% are at " + format(cut, "g") + " or below.")


def say_movers(df: pd.DataFrame, metric_key: str, by: str) -> str:
    """The largest move between two windows, named and signed.

    `movers()` returns `delta` and `delta_pct`, which are not registry metrics, so
    the sentence cannot be assembled by the generic ranking template - the change
    is the subject, not the level.
    """
    if df.empty or "delta" not in df.columns:
        return ("No " + M.dimension(by).label.lower()
                + " moved between these two windows.")
    met, dim = M.metric(metric_key), M.dimension(by)
    top = df.iloc[0]
    direction = "up" if float(top["delta"]) >= 0 else "down"
    text = (_short_label(str(top[by])) + " moved most: " + met.label.lower() + " "
            + direction + " "
            + M.fmt(abs(float(top["delta"])), met.unit, compact=True))
    pct = _num(top.get("delta_pct"))
    if pct is not None:
        text += " (" + M.fmt_delta(pct, M.PERCENT) + ")"
    # A group present in only one window is the largest move there is, and reads
    # as a defect unless it is named: "up $350.00" from nothing is an arrival.
    prior_col = metric_key + "_prior"
    if prior_col in df.columns and not _num(top.get(prior_col)):
        text += ", from nothing in the prior window"
    return (text + ", out of " + _count_of(len(df), dim.label.lower())
            + " compared.")


def say_grid(df: pd.DataFrame, row_dim: str, col_dim: str,
             metric_key: str) -> str:
    """The best and worst cell of a two-dimension grid, and how many are empty.

    A heatmap's reading is which cell is worst, and a reader should not have to
    find it by comparing shades. The empty count belongs in the same sentence
    because a blank cell is not a zero, and once the grid is more than a few rows
    the difference between "nothing shipped that way" and "all of it late" is
    invisible unless something says so.
    """
    if df.empty or metric_key not in df.columns:
        return "No rows match these filters."
    met = M.metric(metric_key)
    frame = df.dropna(subset=[metric_key])
    if frame.empty:
        return "No " + met.label.lower() + " can be computed for these cells."
    ranked = frame.sort_values(metric_key, ascending=not met.higher_is_better)
    cell = (lambda row: _short_label(str(row[col_dim])) + " in "
            + _short_label(str(row[row_dim])))
    best, worst = ranked.iloc[0], ranked.iloc[-1]
    text = met.label + " is best for " + cell(best) + " at " + M.fmt(
        best[metric_key], met.unit)
    if len(ranked) > 1:
        text += (", worst for " + cell(worst) + " at "
                 + M.fmt(worst[metric_key], met.unit))
    cells = int(df[row_dim].nunique()) * int(df[col_dim].nunique())
    empty = cells - len(df)
    if empty > 0:
        text += ("; " + format(empty, ",") + " of " + format(cells, ",")
                 + " combinations have no rows at all")
    return text + "."


def say_funnel(df: pd.DataFrame, by: str = "product",
               totals: dict[str, Any] | None = None) -> str:
    """The funnel sentence carries its window, because the ratio is only true in it.

    `totals` comes from `metrics.funnel_totals()`. Without it the sentence describes
    the frame and says so, rather than summing a column of per-group distinct order
    counts into a headline that overstates orders by a third.
    """
    if df.empty:
        return "No page views join to orders in this window."
    window = None
    if totals and totals.get("window"):
        window = totals["window"][0] + " to " + totals["window"][1]
    if totals:
        views = int(totals.get("views") or 0)
        orders = int(totals.get("orders") or 0)
        rate = totals.get("view_to_order_pct")
        text = (format(views, ",") + " page views and " + format(orders, ",")
                + " gated orders")
        if window:
            text += " over " + window
    else:
        views = int(pd.to_numeric(df["views"], errors="coerce").sum())
        rate = None
        text = (format(views, ",") + " page views across "
                + format(len(df), ",") + " ranked "
                + _form(len(df), M.dimension(by).label.lower()))
    if rate is not None:
        text += ", a view-to-order rate of " + format(float(rate), ".2f") + "%"
    text += "."
    dead = df[(df["views"] > 0) & (df["orders"] == 0)]
    if len(dead):
        top = _short_label(str(dead.iloc[0][by]))
        text += (" " + _count_of(len(dead), by) + " "
                 + ("was" if len(dead) == 1 else "were")
                 + " viewed and never ordered, " + top + " most of all ("
                 + format(int(dead.iloc[0]["views"]), ",") + " views).")
    return text


def say_gate(totals: dict[str, Any]) -> str:
    """The one place the ungated figure is stated, as a delta and nothing else."""
    gated, raw = totals.get("revenue"), totals.get("revenue_ungated")
    if gated is None or raw is None or not raw:
        return ""
    excluded = float(raw) - float(gated)
    return ("Cancelled and suspected-fraud lines carry " + M.fmt(excluded, M.MONEY)
            + " of sales that never converted - " + format(100.0 * excluded / float(raw), ".2f")
            + "% of the " + M.fmt(raw, M.MONEY, compact=True) + " raw total. Every "
            "figure here excludes them.")


def _short_label(text: str, limit: int = 44) -> str:
    """Product names in this data run to 60 characters; a caption cannot."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
