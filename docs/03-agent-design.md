# AI Query Agent Design (Phase 3.1)

Status: **decided, and being built against.** Raise objections against the numbered
reasons rather than the conclusion. Roadmap 3.1 wants this reviewed before the
build starts; no reviewer has been available for Phase 0's audience question or
Phase 2's wireframes either, so it is written to be *correctable a row at a time*
rather than held as a gate. What is not open to correction later without a rewrite
is reason 1, so it is first.

## What this is

Natural language in, a chart and a verified sentence out, over the same snapshot
the dashboard reads:

```
  "margin by category in Europe last year"
        |
        v
  intent (the model)  ->  a Plan: metrics, dimensions, filters, grain
        |                        |
        |                        v
        |                 validate against the registry  --> Refusal + fallback
        |                        |
        |                        v
        |                 metrics.aggregate / timeseries  (gates applied here)
        |                        |
        v                        v
  prose (the model)  ->  verify every number against the frame --> drop the prose
                                 |
                                 v
                        charts.choose -> charts.auto_figure -> Answer
```

`Answer` is a value — tiles, a figure, a frame, a sentence, the plan that produced
it — exactly like `dtp.dashboard.views` returns. The CLI prints it, the dashboard
renders it, a test asserts on it.

## The decision that shapes everything else

1. **The model does not write SQL. It fills in a plan.** The tool it is given has
one shape: a list of registry metric keys, a list of registry dimension keys,
optional filters, an optional time grain, a sort and a limit. Code turns that into
`metrics.aggregate(...)`. Four reasons, in the order they matter:

   1. **The gates.** Phase 1 found that `SUM(Sales)` over this data overstates
      revenue by $1,570,305.33, and that cancelled rows carry a delivery delay for
      shipments that never happened. A model writing SQL reproduces both, because
      the obvious query *is* the wrong one here. A plan cannot: `revenue` means
      `sum(order_item_sales) FILTER (WHERE is_revenue_recognised)` and there is no
      key that means the ungated figure except `revenue_ungated`, which is labelled
      as not-for-reporting in the registry itself.
   2. **A plan is checkable before it runs.** Every key is looked up in the
      registry, so an unknown one becomes a sentence naming the alternatives rather
      than a DuckDB error or, worse, a plausible number from a column that means
      something else. Checking generated SQL to the same standard means parsing it.
   3. **Injection has nowhere to go.** The snapshot carries 466,728 rows of
      access-log data and 180,519 order lines, including free-text product names.
      If any of it ever contains "ignore your instructions and …", the worst a
      compromised plan can do is name a different registry key. Filter *values* are
      bound by DuckDB (`$f0`), never interpolated, which `warehouse.sql` already
      enforces and `test_parameters_are_bound_not_formatted` already pins.
   4. **The tool schema cannot drift from the semantic layer**, because it is
      generated from `METRICS` and `DIMENSIONS` at call time rather than written out
      by hand. A metric added in Phase 4 is askable the same day; a metric renamed
      cannot leave a stale name in a prompt.

2. The cost is real and worth naming: **the agent can only answer questions the
registry covers**. It cannot invent a metric, self-join, window-function its way to
a running total, or answer "which customers ordered twice in March". That is a
smaller surface than free SQL and a much better-defined one, and roadmap 3.1's own
"done when" asks for correct results on sample questions plus *a defined fallback
for every out-of-scope type* — not for arbitrary SQL. Section "Guardrails" below is
that list. If the answer to "what can it not do" ever needs to shrink, the fix is a
new registry entry with a gate and a test, not a loosened prompt.

## The one tool

The model is given a single tool, `query_data`, whose schema is built from the
registry each time it is called:

| Field | Type | Constrained to |
|---|---|---|
| `metrics` | list of keys | the 19 registry keys, of which 17 are aggregatable |
| `by` | list of keys | the 15 dimension keys; 0, 1 or 2 of them |
| `grain` | key or null | `year`, `quarter`, `month` — the time drill path |
| `date_from`, `date_to` | `YYYY-MM-DD` | clamped to the snapshot's own window |
| `where` | key → list of values | dimension keys; values bound, not interpolated |
| `order_by` | key, `-` for descending | must be one of the keys this plan selects |
| `limit` | integer | capped, because a 200-row bar chart is not an answer |
| `min_lines` | integer | the thin-group control the dashboard exposes too |

3. **`by` is capped at two dimensions** because `charts.choose` has an honest chart
for zero, one and two and returns `"table"` beyond that. A three-dimension result
is not refused — it renders as rows — but the model is not encouraged to ask for one.

4. **`grain` and `by` are separate fields even though a grain is a dimension.** It
reads as the distinction users make ("monthly revenue by market" is a grain plus a
dimension) and it lets the plan validator route to `timeseries()`, which is the only
call that guarantees one row per period.

5. **One tool, not one per question type.** A second tool ("compare two periods",
"rank groups") would put the same registry keys in two schemas and let the model
pick the wrong one; period-over-period and ranking are properties of a plan
(`grain` set, or `order_by` set with a `limit`), not different questions. The
purpose-built queries that genuinely cannot be expressed as an `aggregate` call —
the funnel, which spans two tables over its own window — get their own tool rather
than a flag, because their shape and their window are different.

## Guardrails: what it refuses, and with what

6. Roadmap 3.1 asks for "every out-of-scope question type has a defined fallback".
Each row below is a `Refusal` with a machine-readable `reason` code, and a test
enumerates the codes so a new class of question cannot be added without one.

| Code | Question like | What comes back |
|---|---|---|
| `unknown_metric` | "how many returns did we get?" | Names the nearest registry metrics and says returns are not in this extract. |
| `unknown_dimension` | "revenue by salesperson" | Lists the 15 dimensions. The data has no such column; guessing `customer_segment` would be worse than refusing. |
| `unknown_value` | "revenue in Wakanda" | Filter values are case-folded against the column first, so `europe` resolves to `Europe`; a value that matches nothing names the ones that do. Left unchecked it would return an empty frame and a fluent summary of nothing. |
| `empty_result` | "margin by product, groups over 5,000 lines" | A well-formed plan that matched no rows. States which part emptied it — the thin-group cut-off, the filter combination, or the window — because a blank chart beside a confident sentence is the shape a hallucination takes here. |
| `personal_data` | "who is our biggest customer?", "which IP hit us most?" | Refuses by design, not by omission: customer names, streets and client IPs are in the clean data and are deliberately not dimensions. Offers segment, country-level geography, or product — the geography path stops at country, so a fallback naming a city would itself be refused. |
| `out_of_window` | "Q3 2019", "yesterday" | States the snapshot's actual window (2015-01-01 → 2018-01-31 in the shipped extract, read from the data, not hardcoded) and offers the nearest period it holds. |
| `funnel_window` | "conversion rate in 2016" | The access log covers 2017-09 → 2018-01 only. A rate over the full order window understates by ~7×, so the window is stated and the plan is clamped rather than silently answered. |
| `causal` | "why did revenue drop in October?" | The platform reports what happened, not why. It offers what it *can* prove: the Oct 2017 composition break (1.00 lines per order against 3.00 before), the confounding warnings, and the movers list. |
| `forecast` | "what will next quarter be?" | No model is fitted, and a trend line extended by an LLM is a guess wearing a number. Refused with the trend it can show instead. |
| `write` | "delete the cancelled rows", "update the target" | The warehouse is views over Parquet; nothing here can write. Stated as a property, not a policy. |
| `raw_sql` | "run `SELECT * FROM …`" | Not offered — reason 1. Points at `dtp` CLI and the snapshot path for anyone who wants SQL; DuckDB over the Parquet is two lines. |
| `unparseable` | anything the model maps to no plan | The fallback message: what it can answer, in one paragraph, with three examples. Never a guess. |

7. **`personal_data` is the row worth arguing about.** The refusal is not "I can't
see that" — the columns exist in the snapshot the agent queries. It is that naming a
customer is a different act from summarising a segment, and the dashboard already
took that decision (`docs/02`, and `test_no_view_carries_a_personal_column`). The
agent inherits it structurally: those columns are not in `DIMENSIONS`, so no plan
can reference one, and the refusal explains *why* rather than pretending the data is
absent. An operator who wants that answer has SQL and a snapshot path.

8. **Refusals are not the model's decision.** The structural codes —
`unknown_metric`, `unknown_dimension`, `unknown_value`, `out_of_window`,
`funnel_window`, `empty_result`, `unparseable` — are raised by the validator or the
executor, from the registry and from the data, so they hold whatever the model does.
The five that describe a *kind* of question — `personal_data`, `causal`, `forecast`,
`write`, `raw_sql` — are screened by keyword before a token is sent anywhere, and
may also be declared by the model, which is a fourth line of defence rather than the
first. A question the screen misses falls through to a structural code: a worse
message and an equally safe outcome, so a missed pattern costs clarity, never
safety. What holds regardless of which layer fires is the part that matters: no
personal column is a registry dimension, so no plan can name one; no key means the
ungated figure except `revenue_ungated`; and nothing reachable from here can write.

## Hallucination guardrail: two layers, and only one of them involves the model

Roadmap 3.3's "done when" is *zero* mismatches between a stated number and the
query result. That is achievable only if the stated number and the query result are
the same object, so:

9. **Layer 1 — structural.** Every number the user sees comes from the executed
frame, formatted by `metrics.fmt_metric`, the same function the dashboard's tiles
use. Tiles, axes and the table are built from the frame in code. The model is never
asked for a figure and no figure it emits is rendered. This is what makes the
guarantee hold rather than merely be tested.

10. **Layer 2 — verification of the prose.** The model *is* asked for one sentence,
because "Europe's margin is 6.7 points below Asia's, and the gap widened in the last
two quarters" is worth more than a template. So every numeric literal in that
sentence is extracted and matched against the frame: the metric values, their
formatted forms, the differences between any two of them, and the group labels. A
literal that matches nothing is a mismatch, and a mismatch **drops the whole
sentence** and substitutes the computed `insights.say_*` line, which is verified by
construction because it is derived from the frame rather than described from it.

11. Dropping the sentence rather than the number is deliberate. Editing prose to
remove one figure leaves grammar that reads as if it were checked. Replacing it with
the templated sentence is a visible, testable downgrade — and the `Answer` records
`verified: bool` plus what failed, so the CLI can print "model summary withheld
(unverifiable figure: 41.2%)" instead of quietly showing something weaker.

12. Tolerance is the formatter's, not a guess: a number matches if it equals a frame
value at the precision `fmt_metric` would print, so "10.8%" verifies against
10.7999… and "$35.21M" against 35,214,428.98. Percentages are checked in both
directions — a share and a point difference are both legitimate readings of the same
pair — and integers under 100 that appear in the frame as a row count or a group size
also count, because "the top 5 categories" is prose about the shape, not a claim
about a value.

## Session memory: a patch, not a transcript

13. Roadmap 3.3's test is a follow-up: *"break that down by region"*. The
implementation is that the model receives the **current plan as JSON** in its system
prompt and returns a plan whose unset fields mean "unchanged". `Plan.patch()` merges,
so "break that down by region" is `{"by": ["region"]}` against whatever was asked
before, and "and just for Europe" is `{"where": {"market": ["Europe"]}}`.

14. This is a smaller thing to get right than replaying a transcript, and it is
inspectable: the session holds the last plan, the last frame and the last answer, so
`dtp ask --repl` can print the plan it resolved to. A follow-up that resolves to a
plan the validator refuses gets the refusal, not the previous answer again.

15. **A plan is only reusable within one snapshot.** The session records the
snapshot id; changing it clears the plan rather than reinterpreting keys against
different data. Nothing here persists to disk — a session is one process. Persisting
question history would mean storing what users asked about customers, which is a
privacy decision nobody has taken.

## The model, and what it costs

16. `docs/00-tech-stack.md` deliberately deferred the model id to this point rather
than hardcoding one that would go stale. **Default: `claude-sonnet-5`**, overridable
by `DTP_AGENT_MODEL`. Intent parsing here is a small structured task — a fixed tool
schema, a short question, no long-context reasoning — which is where Sonnet sits
best; the registry does the domain work that a larger model would otherwise have to
infer. Opus is available by env var for anyone who measures a difference on the test
set, and the test set is exactly how that argument should be settled.

17. **Two calls per question, both small**: one to produce the plan, one to write the
sentence over the frame's head rows. The frame is truncated before it is shown to the
model — a summary needs the shape and the extremes, not 200 rows — which keeps the
second call cheap and bounds what leaves the process.

18. **No key is committed, and none is required to build or test this.** The model
is reached through a `Model` protocol with three implementations: the `anthropic`
SDK, a scripted stub the tests use, and a keyless keyword matcher. Every guardrail,
the validator, the verifier, the session patching and the chart selection are
testable offline, so CI needs no credential and no spend. `ANTHROPIC_API_KEY` is read
from the environment or `.env` (gitignored); `.env.example` documents that name and
the optional `DTP_AGENT_MODEL`, and holds no value for either.

Without a key the platform still runs, and it declines rather than guesses.
`KeywordModel` matches registry keys and labels against the question's own words —
"the ten worst products by profit" is a plan — and answers anything else with the same
`CANNOT_ANSWER` reply a real model uses. It writes no prose at all, so the sentence is
always the templated one derived from the frame. Two reasons that is worth having: the
pipeline behind the model is the part worth demonstrating and none of it needs a
credential, and a stub that guessed would answer a question nobody asked, which is the
failure every guardrail here exists to prevent. `dtp ask --stub` and the dashboard's
Ask screen both use it, and both name it on screen so nobody mistakes it for the
model.

## Security boundary

19. What the model can influence: which registry keys are selected, which bound
values are filtered on, the sentence (subject to verification). What it cannot
influence: the SQL text, the gates, the snapshot, whether a refusal happens, or any
number on screen. That is the whole boundary, and it is narrow on purpose.

20. Data leaves the process only in the second call, and only as the head of an
aggregated frame — group labels and metric values. No row-level data, no customer
name, street or IP, because those are not dimensions and cannot be in a plan. This is
worth stating plainly because it is the one place this project sends anything
anywhere: `dtp.monitoring` has no network sink at all and a test enforces that, while
the agent is a deliberate, single, documented exception.

21. Prompt-injection reachability is bounded by construction, but it is not zero:
group *labels* from the data appear in the second call. A product named
"ignore previous instructions" cannot change the plan (already executed) or the
numbers (rendered from the frame), but it could influence the sentence — which is
then number-checked and, on any unverifiable figure, discarded. Two residuals are
worth naming rather than implying:

   - **The label check is a membership test, not a name detector.** It reads each
     grouping's whole drill path, so a sentence about five markets that names a sixth
     market, a region or a country it was never shown is caught — those names are in
     the data, and one absent from *this* frame is fabrication however real its
     figures are. A name that appears nowhere in the data has no list to be missing
     from and is not caught. The alternative, flagging every capitalised word not in
     the frame, needs a stop list of English words, and a stop list rots.
   - **Prose containing only true numbers can still mislead.**

   Both are why `Answer.computed` — the templated sentence, derived from the frame
   rather than described from it — is always populated beside the model's, and why
   `verified` is a recorded field rather than an assumption.

## The test set

22. Roadmap 3.2 wants 10+ sample questions parsed correctly and 3.3 wants an
end-to-end log; `docs/00-success-metrics.md` proposes ≥90% on 20+ questions. The set
lives in `tests/agent_questions.yml` so it is reviewable by someone who does not read
Python, and each entry carries the question, the expected plan (or refusal code) and
why that is the right reading. Two things it deliberately does *not* do:

- **It does not call the API.** Each question's expected plan is asserted against the
  validator and the executor, so the set proves that *if* the model produces this
  plan, the answer is correct and the chart type is right. A live run against the
  real model is a separate, key-gated script (`scripts/agent_smoke.py`) whose output
  is a log, because that measurement belongs to whoever owns the spend.
- **It does not grade prose.** Sentence quality is not a pass/fail; the verifier's
  behaviour on a *lying* model is, and the set includes fabricated summaries that must
  be caught.

## Layout

```
src/dtp/agent/
  __init__.py  the package docstring and the re-exports the CLI and tests read
  plan.py      Plan, validation, execution through metrics.aggregate/timeseries
  tools.py     the tool schema and system prompt, generated from the registry
  guard.py     Refusal codes, scope checks, and the numeric verifier
  session.py   the ask loop and the follow-up patch
  client.py    the Model protocol, the anthropic adapter, and two keyless stand-ins
```

The surfaces that read an `Answer`, and nothing else that does:

```
src/dtp/cli.py           `dtp ask "..."`, `--repl` for a session, `--stub` for no key
dashboard/app.py         the Ask screen: a seventh nav entry, not a seventh view
scripts/agent_smoke.py   the key-gated live run, whose output is a log
```

The Ask screen is deliberately outside `views.CATALOGUE`. The six views there are
built from the sidebar's filters; this one is built from a question, and the sidebar's
date range, filters and thin-group floor are hidden on it because the plan carries its
own. A control that silently does nothing is worse than one that is absent.

Nothing in `agent/` writes SQL — `plan.py` calls `metrics`, which is still the only
module that does. Nothing in `agent/` imports Streamlit, so the same `Answer` serves
the CLI and the dashboard, and a test pins both boundaries the way Phase 2's do.
`Tile` is defined in `session.py` rather than imported from `dashboard.views`, with
the same field names, because the dependency runs one way: the dashboard may read the
agent, never the reverse.

## Still open

| Question | Who should decide | Consequence of leaving it |
|---|---|---|
| Model tier and per-question budget | whoever owns the API spend | Default is `claude-sonnet-5` with two small calls per question. Without a key the keyless matcher answers the plain shapes and declines the rest, so silence leaves the platform demonstrable but not conversational. |
| Is the registry's coverage the right scope? | a stakeholder | Twelve refusal codes are defined. If real users mostly ask something in one of them, the fix is registry entries, not prompt tuning — but nobody has watched a real user ask yet. |
| Whether question history may be stored | whoever owns privacy | Sessions are in-memory only. Persisting them would make usage analysis possible and would record what people asked about customers. |







