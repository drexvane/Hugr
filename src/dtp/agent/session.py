"""The ask loop: a question in, an `Answer` out, with the guardrails in order.

Five steps, and the order *is* the design (`docs/03-agent-design.md` 6-12, 19-21):

1. **Screen the question.** `guard.scope_screen` refuses a naming, causal,
   forecast, write or SQL question before a token leaves the process. A question
   this platform does not answer never becomes an API call, which is a privacy
   property before it is a cost one.
2. **Ask for a plan.** One stateless call carrying the registry, the snapshot's
   real window, and - on a follow-up - the previous plan as JSON.
3. **Validate it.** `plan.validate` checks every key against the registry and every
   filter value against the column, so a refusal names the alternatives instead of
   raising a DuckDB error or answering from a column that means something else.
4. **Execute and render.** `metrics` computes, `charts` draws, `insights` writes the
   templated sentence. Every number the user will see is born in this step, and the
   model is not in it.
5. **Ask for a sentence, then check it.** The model's prose is matched against the
   frame and dropped *whole* on any figure that does not hold.

`Answer` carries the plan, the frame, the figure, the tiles, both sentences and
what was withheld, so the CLI, the dashboard and the tests read one value. Nothing
here imports Streamlit and nothing here writes SQL; two tests pin both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from .. import charts as C
from .. import insights as I
from .. import metrics as M
from ..warehouse import Warehouse
from . import plan as P
from . import tools as T
from .client import PLAN_TOKENS, SUMMARY_TOKENS, Model
from .guard import PlanError, Refusal, refuse, scope_screen, verify_summary

MAX_TILES = 5           # a KPI row wider than this is a table
LABEL_SCAN = 300        # values per dimension fetched for the fabrication check

FUNNEL_TILES = ("views", "orders", "view_to_order_pct", "revenue")

# A dimension key to the whole drill path it belongs to. The label check reads this:
# a question about markets is checked against regions and countries too, because a
# model that knows geography can name one it was not shown.
DRILL_SIBLINGS: dict[str, tuple[str, ...]] = {
    key: path for path in M.DRILL_PATHS.values() for key in path}

# What an `unparseable` refusal offers. Roadmap 3.1 asks for a defined fallback per
# out-of-scope type, and a refusal that names nothing askable is the one that makes
# people stop asking.
EXAMPLES: tuple[str, ...] = (
    "revenue and margin by category",
    "monthly revenue for 2017",
    "the ten worst products by profit",
)


@dataclass(frozen=True)
class Tile:
    """A headline figure. The same fields as `dashboard.views.Tile`, on purpose.

    Defined here rather than imported because the dependency runs one way -
    `dtp.dashboard` may read `dtp.agent`, never the reverse - and named identically
    so one tile renderer serves both.
    """

    label: str
    value: str
    note: str = ""
    about: str = ""


@dataclass(frozen=True)
class Answer:
    """One question's whole outcome, as a value.

    Either `refusal` is set or `frame` is: a question is answered or it is
    explained, never half of each. `summary` is the sentence to show, `computed` is
    the templated line derived from the frame, and `verified` says which of the two
    `summary` currently is - so a caller can print "model summary withheld" rather
    than quietly showing the weaker sentence (design reason 11).
    """

    question: str
    plan: P.Plan | None = None
    frame: pd.DataFrame | None = None
    figure: go.Figure | None = None
    chart: str = ""
    tiles: tuple[Tile, ...] = ()
    summary: str = ""
    computed: str = ""
    verified: bool = False
    withheld: str = ""
    refusal: Refusal | None = None
    notes: tuple[str, ...] = ()
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.refusal is None

    @property
    def caption(self) -> str:
        """What actually ran. Printed beside every answer that has a plan.

        A user who cannot see the SQL can still see which metric, which grouping
        and which window produced the number - the only way a misread question is
        ever noticed.
        """
        return self.plan.describe() if self.plan is not None else ""

    def text(self) -> str:
        """The answer as the CLI prints it: everything but the figure."""
        if self.refusal is not None:
            return self.refusal.message
        lines = [self.caption]
        if self.tiles:
            lines.append("  ".join(t.label + ": " + t.value for t in self.tiles))
        lines += [self.summary, self.withheld]
        lines += ["note: " + n for n in self.notes]
        return "\n".join(line for line in lines if line)


class Session:
    """One process's worth of memory: the last plan, and the snapshot it belongs to.

    Memory is a plan, not a transcript (design reason 13). "Break that down by
    region" is `{"by": ["region"]}` against whatever was asked before, which is a
    much smaller thing to get right than replaying a conversation - and it is
    inspectable, because the plan can be printed.

    A plan is only meaningful inside one snapshot (reason 15), so pointing at a
    different one clears it rather than reinterpreting yesterday's keys against
    different data. Nothing persists to disk: what people asked about customers is
    not something this project stores.
    """

    def __init__(self, wh: Warehouse, model: Model,
                 snapshot: str | None = None) -> None:
        self.wh = wh
        self.model = model
        self.snapshot = snapshot or wh.version_id
        if getattr(wh, "catalog", None) is not None:
            M.set_active_catalog(wh.catalog)
        self.plan: P.Plan | None = None
        self.answer: Answer | None = None
        self.log: list[Answer] = []

    def reset(self) -> None:
        """Forget the plan: the next question is a new one, not a follow-up."""
        self.plan = None
        self.answer = None

    def use(self, wh: Warehouse, snapshot: str | None = None) -> None:
        """Point at another snapshot, clearing the plan if it is a different one."""
        changed = (snapshot or wh.version_id) != self.snapshot
        self.wh = wh
        self.snapshot = snapshot or wh.version_id
        if getattr(wh, "catalog", None) is not None:
            M.set_active_catalog(wh.catalog)
        if changed:
            self.reset()

    # --------------------------------------------------------------- the loop --
    def ask(self, question: str) -> Answer:
        """Answer one question, or explain why this platform will not."""
        if getattr(self.wh, "catalog", None) is not None:
            M.set_active_catalog(self.wh.catalog)
        question = (question or "").strip()
        if not question:
            return self._refuse(question, Refusal(
                code="unparseable", detail="Nothing was asked.",
                suggestions=EXAMPLES))
        screened = scope_screen(question)
        if screened is not None:
            return self._refuse(question, screened)

        plan: P.Plan | None = None
        try:
            plan = self._resolve(question)
            frame = P.execute(self.wh, plan)
        except PlanError as exc:
            # A refusal keeps the previous plan: "no, by region" after a refused
            # question is still a follow-up to the last thing that worked.
            return self._refuse(question, exc.refusal, plan)
        return self._answer(question, plan, frame)

    # ------------------------------------------------------------ step 2 and 3 --
    def _resolve(self, question: str) -> P.Plan:
        """The plan call, then the validator. Nothing has run against data yet."""
        cat = getattr(self.wh, "catalog", None) or M.get_active_catalog()
        reply = self.model.respond(
            T.system_prompt(self.wh, current=self.plan), question,
            tools=[T.tool_schema(cat)], max_tokens=PLAN_TOKENS)
        if reply.tool_call is None:
            raise _declined(reply.text)
        if reply.tool_call.name != T.TOOL_NAME:
            raise refuse("unparseable",
                         "The model called " + repr(reply.tool_call.name)
                         + ", which is not a tool this platform has.", EXAMPLES)

        payload = dict(reply.tool_call.input)
        fresh = P.from_tool_input(payload)      # coercion, and unknown keys refused
        if self.plan is None:
            return P.validate(fresh, self.wh)
        # Only the keys the model actually sent become a patch; everything else
        # stays as it was. `from_tool_input` has already rejected any key that is
        # not part of a plan, so `payload` and the plan's fields agree here.
        changes: dict[str, Any] = {name: getattr(fresh, name) for name in payload}
        if "metrics" in payload and "kind" not in payload:
            changes["kind"] = "aggregate"
        if "metrics" in payload and "order_by" not in payload:
            changes["order_by"] = None
        return P.validate(self.plan.patch(changes), self.wh)

    # ----------------------------------------------------------- step 4 and 5 --
    def _answer(self, question: str, plan: P.Plan,
                frame: pd.DataFrame) -> Answer:
        totals = self._totals(plan)
        dims, keys = _shape(plan, frame)
        chart, figure = _draw(plan, frame, dims, keys)
        computed = _computed(plan, frame, chart, dims, keys, totals)
        summary, verified, withheld = self._summarise(plan, frame, computed)
        answer = Answer(
            question=question, plan=plan, frame=frame, figure=figure, chart=chart,
            tiles=_tiles(plan, totals, getattr(self.wh, "catalog", None)),
            summary=summary, computed=computed,
            verified=verified, withheld=withheld, model=self.model.name,
            notes=tuple(plan.notes) + _tile_note(plan, frame))
        self.plan = plan
        self.answer = answer
        self.log.append(answer)
        return answer

    def _totals(self, plan: P.Plan) -> dict[str, Any]:
        """The headline figures, from their own query rather than from the frame.

        Summing a grouped `margin_pct` column averages a ratio, and a frame with a
        `limit` holds the leaders rather than the total. One query per answer, the
        way the dashboard's KPI row works, so no two numbers on screen disagree.
        """
        if plan.kind == "funnel":
            return M.funnel_totals(self.wh)
        return M.totals(self.wh, plan.metrics[:MAX_TILES], filters=plan.filters())

    def _summarise(self, plan: P.Plan, frame: pd.DataFrame,
                   computed: str) -> tuple[str, bool, str]:
        """The summary call, and the check that decides whether it survives.

        The frame's head is the only data that leaves this process (design reason
        20), formatted exactly as the screen shows it, so the verifier and the model
        are looking at the same numbers at the same precision.
        """
        reply = self.model.respond(T.SUMMARY_SYSTEM, T.frame_digest(frame, plan),
                                   max_tokens=SUMMARY_TOKENS)
        said = reply.text.strip()
        if not said:
            return computed, False, ""
        check = verify_summary(said, frame, self._absent_labels(plan, frame))
        if check.ok:
            return said, True, ""
        return computed, False, check.note

    def _absent_labels(self, plan: P.Plan,
                       frame: pd.DataFrame) -> tuple[str, ...]:
        """Every group name this plan's drill paths hold, for the strict half.

        `verify_summary` checks numbers permissively and labels strictly, and this
        is the strict half's input: "Africa leads" about a frame with no Africa row
        is fabrication even when every figure in the sentence is real. It skips the
        names the frame *does* carry, so only a name that exists in the data and not
        in this answer is a problem.

        The scan widens to each dimension's whole drill path, because that is where
        the plausible mistake is: a model shown five markets knows world geography
        and may name a country or a region it was never given. A path sibling is
        also the only kind of name confusable enough to be worth checking - the
        alternative, a stop list of English words, is a list that rots.

        Time grains are left out. A period is checked as a *window* by the
        validator, and a month's stored and formatted forms differ enough
        (`2018-01-01 00:00:00` against `Jan 2018`) that matching them here would
        flag true sentences.
        """
        cat = getattr(self.wh, "catalog", None) or M.get_active_catalog()
        drill_paths = cat.drill_paths if cat else M.DRILL_PATHS
        drill_siblings = {key: path for path in drill_paths.values() for key in path}
        time_grains = tuple(cat.drill_paths.get("time", P.TIME_GRAINS)) if cat else P.TIME_GRAINS

        wanted = ([plan.funnel_by] if plan.kind == "funnel"
                  else [d for d in plan.dims() if d not in time_grains])
        keys: list[str] = []
        for key in wanted:
            for sibling in drill_siblings.get(key, (key,)):
                if sibling not in keys and sibling not in time_grains:
                    keys.append(sibling)
        out: list[str] = []
        for key in keys:
            out += [str(v) for v in M.dimension_values(self.wh, key,
                                                       limit=LABEL_SCAN,
                                                       catalog=cat)]
        return tuple(out)

    def _refuse(self, question: str, refusal: Refusal,
                plan: P.Plan | None = None) -> Answer:
        answer = Answer(question=question, plan=plan, refusal=refusal,
                        model=self.model.name,
                        notes=tuple(plan.notes) if plan is not None else ())
        self.answer = answer
        self.log.append(answer)
        return answer


# --------------------------------------------------------------------------- #
# the model's own refusal
# --------------------------------------------------------------------------- #

def _declined(text: str) -> PlanError:
    """The model's "I can't answer that", mapped onto a code this platform owns.

    Design reason 8: the model's refusal is the *fourth* line of defence, so the
    code is decided here rather than taken from it. Running the scope screen over
    the model's own sentence usually classifies it - a decline that says "why" or
    "forecast" is one of ours, with a fallback already written - and anything else
    is `unparseable` carrying the sentence, which at least names what was missing.
    """
    said = text.replace(T.DECLINE_TAG, "").strip().strip(":-— ").strip()
    screened = scope_screen(said) if said else None
    if screened is not None:
        return PlanError(screened)
    return refuse("unparseable", said, EXAMPLES)


# --------------------------------------------------------------------------- #
# rendering: shape, chart, tiles, and the sentence that is true by construction
# --------------------------------------------------------------------------- #

def _shape(plan: P.Plan, frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    """The dimension and metric keys the chart selector reads.

    Intersected with the frame's columns rather than taken from the plan alone,
    because the funnel's columns are its own and because a metric the validator
    added for a sort has to be present to be plotted.
    """
    if plan.kind == "funnel":
        return ([plan.funnel_by],
                [k for k in ("views", "orders") if k in frame.columns])
    return ([d for d in plan.dims() if d in frame.columns],
            [k for k in plan.metrics if k in frame.columns])


def _draw(plan: P.Plan, frame: pd.DataFrame, dims: list[str],
          keys: list[str]) -> tuple[str, go.Figure | None]:
    """`charts.choose` picks, `charts.auto_figure` draws, None means "show rows"."""
    if plan.kind == "funnel":
        # The funnel's chart is the parity scatter the dashboard already draws: the
        # reading is distance below one-order-per-view, which no group reaches.
        return "scatter", C.scatter(frame, plan.funnel_by, "views", "orders",
                                    size_key="revenue", parity=True,
                                    subtitle=plan.describe())
    chart = C.choose(dims, keys, len(frame))
    extra: dict[str, Any] = {}
    if chart == "line":
        # A flagged month is ringed on the chart and named in the sentence, from one
        # call, so the two cannot disagree about which month is the outlier.
        extra["anomalies"] = I.find_anomalies(frame, keys[0], label_col=dims[0])
    return chart, C.auto_figure(frame, dims, keys, subtitle=plan.describe(),
                                **extra)


def _tiles(plan: P.Plan, totals: dict[str, Any], catalog: M.Catalog | None = None) -> tuple[Tile, ...]:
    cat = catalog or M.get_active_catalog()
    keys = (FUNNEL_TILES if plan.kind == "funnel"
            else tuple(plan.metrics[:MAX_TILES]))
    return tuple(Tile(label=M.metric(k, cat).label,
                      value=M.fmt_metric(k, totals.get(k), compact=True, catalog=cat),
                      about=M.metric(k, cat).about or "")
                 for k in keys)


def _tile_note(plan: P.Plan, frame: pd.DataFrame) -> tuple[str, ...]:
    """Say so when the tiles and the chart are not measuring the same rows."""
    if plan.kind != "funnel" and (plan.limit or plan.min_lines):
        return ("the figures above are totals for the whole window, not only the "
                + str(len(frame)) + " rows shown",)
    return ()


def _computed(plan: P.Plan, frame: pd.DataFrame, chart: str, dims: list[str],
              keys: list[str], totals: dict[str, Any]) -> str:
    """The templated sentence: verified by construction, because it is derived.

    This is what a dropped model sentence falls back to (design reason 11), so it
    has to exist for every shape `charts.choose` can return. Each branch is the
    pairing the dashboard already uses for that chart - which is how the agent and
    the dashboard cannot caption the same shape two different ways.
    """
    if plan.kind == "funnel":
        return I.say_funnel(frame, by=plan.funnel_by, totals=totals)
    if not keys:
        return _say_shape(frame, dims, keys)
    if not dims:
        return _say_totals(frame, keys)
    if chart == "line":
        return I.say_anomalies(I.find_anomalies(frame, keys[0], label_col=dims[0]),
                               keys[0], noun=dims[0])
    if chart == "line_grouped":
        time_dims = C.get_time_dims()
        time_dim = next((d for d in dims if d in time_dims), dims[0])
        other_dim = next((d for d in dims if d != time_dim), dims[0])
        return I.say_trend(frame, time_dim, other_dim, keys[0])
    if chart == "heatmap":
        return I.say_grid(frame, dims[0], dims[1], keys[0])
    if len(dims) == 1 and dims[0] == "delay_days":
        # An ordered dimension is a distribution, not a ranking: which bar is
        # tallest and how much sits at or below zero days late.
        cat = M.get_active_catalog()
        count = next((k for k in keys if M.metric(k, cat).unit == M.COUNT), keys[0])
        return I.say_spread(frame, "delay_days", count)
    if len(dims) == 1:
        # The sort direction travels with the frame: a plan may rank a metric against
        # its own direction ("the ten worst products by profit"), and a sentence that
        # re-sorted it would name the other end of the chart.
        return I.say_ranking(frame, keys[0], dims[0],
                             ascending=_ascending(plan, keys[0]))
    return _say_shape(frame, dims, keys)


def _ascending(plan: P.Plan, metric_key: str) -> bool | None:
    """Which way the executed frame is ordered, or `None` when nothing said.

    `None` leaves `insights.say_ranking` on the metric's own direction, which is what
    an unsorted frame deserves. A sort on a *different* metric is also `None`: the
    sentence is about `metric_key`, and this frame's order says nothing about it.
    """
    if not plan.order_by:
        return None
    column = plan.order_by.lstrip("-")
    if column != metric_key:
        return None
    return not plan.order_by.startswith("-")


def _say_totals(frame: pd.DataFrame, keys: list[str]) -> str:
    """The ungrouped answer: each figure named, and nothing else claimed."""
    cat = M.get_active_catalog()
    row = frame.iloc[0]
    parts = [M.metric(k, cat).label + " " + M.fmt_metric(k, row[k], compact=True, catalog=cat)
             for k in keys if k in frame.columns]
    text = "; ".join(parts) if parts else "no measure in this result"
    lines = row.get("n_lines")
    if lines is not None and not pd.isna(lines):
        noun = "order lines" if cat is None or cat.name == "order_items" else "records"
        text += f", over {M.fmt(lines, M.COUNT)} {noun}"
    return text + "."


def _say_shape(frame: pd.DataFrame, dims: list[str], keys: list[str]) -> str:
    """The fallback for a shape no chart is honest about: say what the table holds."""
    cat = M.get_active_catalog()
    noun = "order lines" if cat is None or cat.name == "order_items" else "records"
    what = "; ".join(M.metric(k, cat).label for k in keys) or noun
    by = ", ".join(M.dimension(d, cat).label.lower() for d in dims)
    text = what + (" by " + by if by else "")
    return text + " - " + format(len(frame), ",") + " rows, shown as a table."


def ask(question: str, wh: Warehouse, model: Model | None = None) -> Answer:
    """One question, no memory - what the CLI's single-shot mode calls."""
    if model is None:
        from .client import AnthropicModel, OllamaModel, KeywordModel, api_key, is_ollama_available
        if api_key():
            model = AnthropicModel()
        elif is_ollama_available():
            model = OllamaModel()
        else:
            model = KeywordModel(catalog=getattr(wh, "catalog", None))
    return Session(wh, model).ask(question)
