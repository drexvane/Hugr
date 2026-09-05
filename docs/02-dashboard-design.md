# Dashboard Design (Phase 2.1)

Status: **decided, and being built against.** Two Phase 0/1 items were explicitly
left "revisit at Phase 2 kickoff"; both are resolved here, in the two tables
below. Raise objections against the numbered reasons rather than the conclusion.

The stakeholder input Phase 0 wanted — *who reads this and what decisions it
drives* — never arrived, and it blocks roadmap 2.1's first task ("Map questions to
views", input: "Audience & decision-use doc"). Rather than stall the build, the
audience below is **assumed from what the data can actually answer** and written
down so a stakeholder can correct one row instead of commissioning a document.
Every question in the map is answerable from the 46 clean columns; nothing is
aspirational.

## Assumed audience

| Role | The decision they own | What they ask this dashboard |
|---|---|---|
| Operations / fulfilment lead | carrier and shipping-mode policy, promised delivery windows | Where are we late, by how much, and is it getting worse? |
| Commercial lead | pricing, discounting, assortment | What earns money as opposed to merely selling, and where is discount eating margin? |
| Market / growth analyst | where to push spend and attention | Which regions and segments are growing, and does traffic convert into orders? |
| Whoever is on call for the data | whether today's numbers can be trusted | Did the last load pass, and what changed? |

That fourth row is not conventional and is deliberate: Phase 1 produces a
validation verdict, an alert list and a snapshot history, and a dashboard that
hides them invites someone to present a number from a failed load.

## Resolved at kickoff

| Left open | Resolved | Why this, not the alternative |
|---|---|---|
| Streamlit + Plotly vs React over FastAPI (`00-tech-stack.md`, reason 8) | **Streamlit + Plotly**, with the chart layer as a plain Python module that returns Plotly figures and knows nothing about Streamlit | 1. Roadmap 3.2 requires the agent to *reuse* the dashboard's chart generation ("Same module powers both dashboard and agent output"), and the agent is Python. A React chart layer would put the charts behind an HTTP contract the agent then has to reimplement or call, which is the duplication the roadmap's own risk list warns about. 2. The escape hatch survives: a Plotly figure serialises to JSON that `plotly.js` renders natively, so moving to React later is a rendering change, not a rewrite of the analysis. 3. Phase 2's innovation layer is anomaly detection, comparison and narrative — data work, which is where the effort should go. |
| A normalised join key for the case-mismatched labels (`01-cleaning-and-pipeline-decisions.md`, consequences) | **Yes, but in the query layer, not the clean data**: `warehouse.py` registers views that add `*_key` columns alongside the originals | 4. The mismatch is not a defect in the source — the log genuinely emits lowercase — so folding it in the cleaning layer would overwrite what the system emitted, and the clean tables would stop being a faithful typed copy. 5. It cannot be left to each query either: an LLM generating SQL in Phase 3 will write `a.department_name = b.department_name`, get **zero rows**, and report "no data" rather than an error. A silent empty join is worse than a loud failure. 6. Putting it in the warehouse view means one definition, applied to every consumer, with the snapshots untouched — no schema change, no drift alert, no re-running Phase 1. |

## The gate every view inherits

Phase 1 found two traps that make the obvious query wrong. They are not warnings
in a document here; they are conditions the metric layer applies for you, so a
view cannot opt out by forgetting:

| Metric family | Forced filter | What it costs if omitted |
|---|---|---|
| revenue, profit, margin, discount | `is_revenue_recognised` | 7,754 cancelled / suspected-fraud lines carry full money values. Gating removes **$1,570,305.33** — 4.27% of the raw total ($36,784,734.31 → $35,214,428.98) — and 4.05% of profit. |
| on-time rate, delay, shipping days | `is_shipment_valid` | The same 7,754 rows still carry `days_shipping_real`. 4,423 read as *late* and 1,774 as *early* for shipments that never happened. |
| anything joining orders to page views | the `*_key` columns, and the Sep 2017 → Jan 2018 window | A literal label join returns zero rows. The log covers only the last 5 of 37 months, so a funnel rate over the full order window is meaningless. |

7. The overview view states the ungated figure **once**, as an explicit delta,
rather than never. Someone who has previously totalled the raw CSV needs to see
why this dashboard disagrees with them by $1.57M, or they will assume it is broken.
The percentage is always quoted as *a share of the raw total* — the direction the
reader travels, raw down to gated — because "overstates by 4.27%" and "raw is
4.46% above gated" are both true of the same pair of numbers and only one of them
can be on the tile.

8. The figures above are the **clean** totals, which is what the dashboard shows.
`config/cleaning_rules.yml` quotes the raw-string equivalents ($36,784,735.01 →
$35,214,429.65) because it is describing the source rows, and the two differ by
the $0.70 of float32 noise that `docs/01` argues out. Both are correct; the
dashboard's job is to agree with the clean column it queries.

## Question → view map

| # | View | Question it answers | Primary marks | Grain |
|---|---|---|---|---|
| 1 | **Overview** | Are we growing, and can I trust this number? | gated KPI row; monthly revenue and profit line with anomaly flags; the ungated-vs-gated delta | month |
| 2 | **Delivery** | Where are we late, by how much, and is it worsening? | on-time rate by shipping mode × market; delay distribution; monthly late-rate trend | order line |
| 3 | **Profitability** | What earns money rather than merely selling? | profit vs revenue by department and category; discount-rate vs margin scatter; ranked loss-making products | category / product |
| 4 | **Geography & segment** | Which markets and segments deserve attention? | revenue and margin by market → region → country drill-down; segment mix | region |
| 5 | **Funnel** | Does traffic convert, and what gets looked at but not bought? | views vs orders per product; view-to-order ratio; never-viewed and never-ordered lists | product, Sep 2017 → Jan 2018 |
| 6 | **Data health** | Did the last load pass, and what changed? | latest snapshot and validation verdict; alert table; snapshot history with row deltas | snapshot |

9. Views 1–4 read `order_items` alone, so they are defined over the whole
2015-01 → 2018-01 window. View 5 is the only one that joins, and it carries its
window in its own title rather than inheriting the global filter — a funnel
silently computed over 37 months when its numerator covers 5 is off by 7×.

## Wireframes (low-fi)

```
VIEW 1 - OVERVIEW
+----------------------------------------------------------------------+
| [ market v ] [ segment v ] [ date range .......... ] [ snapshot v ]  |
+----------------------------------------------------------------------+
| REVENUE      | PROFIT       | MARGIN       | ORDERS   | ON-TIME      |
| $35.21M      | $3.81M       | 10.8%        | 62,897   | 42.7%        |
| gated: -4.27% vs raw                                                 |
+----------------------------------------------------------------------+
| monthly revenue + profit, anomaly months ringed and labelled         |
|   [ line chart ................................................. ]  |
| > templated sentence naming the flagged month, its value and how    |
|   far it sits from the trailing median                              |
+----------------------------------------------------------------------+
```

10. The KPI figures above are the real gated totals from snapshot
`20260903T224815`, not placeholders. Two of them are worth naming because the
ungated reading is the one a reader is likely to have seen: profit is **$3.81M**
gated against $3.97M ungated, and orders are **62,897** gated against 65,752. A
tile that mixes the two — gated revenue beside an ungated order count — produces
an average order value that is wrong in a way no single tile reveals, which is
why `aov` is defined as `revenue / orders` over the registry's own gated metrics
rather than as its own SQL expression.

```
VIEW 2 - DELIVERY                        VIEW 3 - PROFITABILITY
+-----------------------------+          +-----------------------------+
| ON-TIME | LATE | AVG DELAY  |          | scatter: discount rate x    |
+-----------------------------+          | margin, one dot per category|
| heatmap: shipping mode x    |          | size = revenue              |
| market, cell = on-time rate |          +-----------------------------+
+-----------------------------+          | bar: profit by department   |
| bar per delay day, 0 marked, |          | with revenue as a ghost bar |
| negative = early            |          +-----------------------------+
+-----------------------------+          | table: loss-making lines,   |
| monthly late-rate trend     |          | ranked by profit lost       |
+-----------------------------+          +-----------------------------+

VIEW 5 - FUNNEL (Sep 2017 - Jan 2018)    VIEW 6 - DATA HEALTH
+-----------------------------+          +-----------------------------+
| VIEWS | ORDERS | VIEW->ORDER|          | snapshot 20260903T224815    |
+-----------------------------+          | validation PASS - 51 rules  |
| scatter: views x orders per |          +-----------------------------+
| product, diagonal = parity  |          | alerts, ranked crit->info   |
+-----------------------------+          +-----------------------------+
| lists: viewed-never-ordered |          | history: rows per snapshot, |
|        ordered-never-viewed |          | delta vs previous, hash     |
+-----------------------------+          +-----------------------------+
```

## Innovation elements, per view

Roadmap 2.3 asks for three things. Assigning them per view rather than sprinkling
them everywhere:

| Feature | Where it applies | How it is decided | Done when |
|---|---|---|---|
| **Anomaly highlighting** | views 1, 2 (monthly series) | robust z-score: median and MAD over the series, flag \|z\| > 3.5. Not mean/σ — 11. a single extreme month inflates σ and hides itself, which is the one case the feature exists for | a month injected at 3× the median is flagged; a flat series flags nothing |
| **Comparative / trend** | views 1–4 | two comparisons, both explicit: period-over-period (same length, immediately prior) and benchmark-vs-parent (a category against its department's margin) | both reproduce a hand calculation on the fixture |
| **Narrative annotation** | views 1–5, one line per chart | templated from the numbers the chart already holds — no LLM in Phase 2. 12. An annotation generated by a model can disagree with the chart beside it; a template cannot, because it is computed from the same frame | the sentence names a real dimension value and a number that appears in the figure |

13. Narrative stays templated in Phase 2 on purpose. Phase 3 introduces the model
*and* the guardrail that checks its stated numbers against query results
(roadmap 3.3); until that guardrail exists, generated prose next to a chart is an
unchecked claim.

## Consequences accepted

- **No authentication by default, and a shared-secret gate when asked for.** The
  dashboard is a Streamlit process reading local Parquet; unset, it binds to
  localhost and ships no login, which is adequate for a demo on one machine.
  Setting `DTP_DASHBOARD_PASSWORD` (or `DTP_DASHBOARD_PASSWORD_SHA256`) turns on a
  password gate that runs before the sidebar, so nothing renders first — not the
  charts, and not the snapshot list and filter boxes, which are made of real market
  and product values.

  **That gate is not access control**, and `dtp.dashboard.auth` says so in the only
  place an operator will read. There are no users, so nothing can be revoked or
  attributed to one person; the per-session attempt cap slows a browser, not a
  script; and the clean data still carries customer names, street addresses and
  3,340 client IPs. Hosting *this* data for anyone outside the team wants a real
  identity provider in front of it. What the gate buys is that the demo can go on a
  host without being open, which is the smallest defensible step and the one Phase 4
  needed. A secret shorter than 12 characters refuses to serve rather than
  pretending: a gate the operator believes in and that accepts `1234` is worse than
  no gate.
- **Personal columns are not surfaced.** No view plots `customer_first_name`,
  `customer_last_name`, `customer_street` or `client_ip`. Geography stops at country
  level, matching the suppression the data dictionary already applies.
- **The dashboard reads a snapshot, not the live clean directory.** It picks the
  newest `data/versions/*` by default and lets you pin an older one. A dashboard
  reading `data/clean/` would change mid-presentation if a pipeline run started.
- **Load time is measured against a snapshot on local disk.** DuckDB over 647,247
  rows of Parquet answers every query here in well under a second, so no
  pre-aggregation layer is built. If a future extract is 100× larger, the
  aggregation belongs in the warehouse view definitions, which is the one place
  every consumer already goes through.

## What was built, and what the build changed

The design above survived contact with the data. These are the decisions taken
while building it, each with the figure that forced it. Numbering continues from
the reasons above.

### The renderer holds no numbers

`src/dtp/dashboard/views.py` computes what a view contains and returns it as
values — formatted tiles, Plotly figures, frames, one sentence per panel.
`dashboard/app.py` imports it and does nothing but place them.

14. This is the same split `charts.py` already makes for a single figure, extended
to a whole screen, and it is enforced rather than intended:
`test_the_view_layer_knows_nothing_about_streamlit` parses the module and fails on
any import outside `{__future__, typing, json, dataclasses, pathlib, pandas,
plotly, dtp}`, and `test_the_renderer_does_no_arithmetic` fails if the renderer
reaches past a view builder to `aggregate`, `totals`, `sql` or a formatter. What it
buys: "the delivery view names its worst cell" is an assertion about a string, and
roadmap 3.2's agent can call `overview()` for the figure the dashboard shows
instead of reimplementing the composition.

### The funnel headline is orders, not lines

| Over 2017-09-01 → 2018-01-31 | Figure |
|---|---|
| page views | 466,728 |
| gated orders | **9,683** |
| gated order *lines* | 13,124 |
| view-to-order rate | **2.07%** |
| revenue on those orders | $3,514,452.23 |

15. Orders and lines differ by 36% here, and the funnel is a question about
orders, so the tile is `count(DISTINCT order_id)` and the rate is 2.07% rather
than 2.81%. The headline comes from one query over the window rather than from
summing the frame the scatter plots: each row's order count is distinct *within*
that group, so an order spanning three categories is one order and three of those
counts — adding the column up overstates orders by 29% at product grain, in the
direction that flatters.

16. The view takes no filters at all, and the date control does not apply to it.
`test_the_funnel_ignores_the_date_control` asserts that by calling it with
`filters=` and requiring a `TypeError`: a five-month numerator over a
thirty-seven-month denominator understates conversion about sevenfold, and a
control that silently does that is worse than no control.

### Two findings that make an honest chart misleading

17. **A composition break at Oct 2017.** From that month an order carries 1.00
lines against 3.00 over the 33 months before it, and only 4 months sit after it.
Monthly revenue therefore falls by two thirds while revenue *per line* rises, and
the anomaly flag agrees with the fall — both are describing a change in the shape
of the data, not in trade. The overview carries this as a note above the series,
and it names the remedy: compare per-order or per-line figures across that
boundary, or filter to one side of it. The split is searched for rather than
hardcoded, so a different extract finds its own.

18. **Market is very nearly a period.** At monthly grain `market` scores 100.0 on
median top-value share and 26 of 37 months hold exactly one market; the next most
concentrated dimension, shipping mode, scores 59.7. So "revenue by market" over
the whole window is partly a chart of which months each market's block covers, and
a period-over-period comparison by market compares two different windows. The
geography view states this, which is the difference between a finding and an
artefact. The 90.0 threshold sits in the gap between 100.0 and 59.7 rather than in
the middle of a continuum.

### Smaller decisions, recorded because they are judgements

19. **The delay chart is a bar per value, not a histogram** — the wireframe's word
was wrong. `shipping_delay_days` takes exactly seven integers, −2 to 4, so there
are no bin edges to choose and nothing hidden inside a bucket. It also counts
`shipped_lines` rather than `lines`: the gated rows carry a real
`days_shipping_real` for deliveries that never happened, and the 4-day column
holds 6,983 raw lines of which 6,697 shipped.

20. **Thin groups are a control, not a constant.** A rate over a handful of lines
swings on rounding and tops any ranked chart, but how thin is too thin is a
judgement. `MIN_LINES_DEFAULT = 0` drops nothing, and the sidebar offers the
threshold so a reader sees the number rather than inheriting it.

21. **Order status is deliberately not a filter.** Every money metric already
excludes cancelled and suspected-fraud lines, so a control offering `CANCELED`
would return zeros from something that looks like it should return rows. The gate
owns that column.

22. **Filter options are ordered by frequency with an alphabetical tiebreak.** A
box that opens on a one-line category buries the one that matters; `ORDER BY n
DESC` alone leaves ties to the scan, so the list reshuffles between reruns and a
selection remembered by position reopens on a different value.

23. **`reports/alerts.json` is not part of a snapshot.** Monitoring overwrites one
file per run, so beside a snapshot the reader pinned, an undated alert list reads
as belonging to it when it may describe a different load. `checked_at` is carried
through and stated on the panel.

24. **The snapshot picker opened on the oldest snapshot.** `available_versions`
returns newest-first and the renderer reversed it. All four snapshots on disk are
of one source extract and total the same, so every figure on screen was correct
and only the id above them was wrong — which is precisely what a demo does not
reveal. Ordering the picker now lives in the view layer as `snapshot_ids()`, where
a test reaches it.

### Narrative: one sentence per panel, computed from that panel's frame

25. Four sentence templates were added for panels a ranking could not describe:
`say_grid` (the heatmap's best and worst cell, and how many combinations have no
rows — a blank cell is not a 0% on-time rate), `say_spread` (the commonest delay
and the share at or below zero), `say_movers` (the change as the subject, naming
an arrival because a group absent from the prior window has no percentage to
state), and `say_trend` (a line's endpoints and peak, because under a multi-line
chart a ranking just repeats the bar chart above it). Every one takes the frame its
panel plots, so a caption cannot contradict the chart beside it.

### Coverage

26. 503 tests pass. Phase 2's own layers hold 251 of them: 19 for the warehouse,
58 for the metric registry, 53 for the charts, 74 for the sentences and 47 for the
view layer. Three exist because of defects found while building: the snapshot
ordering above, a `data_health` diff that wrapped to the newest snapshot when the
oldest was pinned, and a filter option list that reshuffled between reruns. The
privacy promise is a test rather than a review note —
`test_no_view_carries_a_personal_column` walks every frame and every display table
of all six views and fails if a customer name, street or IP reaches one, under
either its column name or the prose heading the display layer would give it.

### Performance, measured rather than assumed

27. `docs/00-success-metrics.md` proposes **first render under 2 s** and
**interactions under 500 ms**. Measured against snapshot `20260903T224815` — 647,247
rows, local disk, `streamlit.testing.v1.AppTest` so no browser paint is included:

| | Measured | Proposed |
|---|---|---|
| First render (open + sidebar + overview) | **1,189 ms** | < 2,000 ms |
| Switch to delivery / geography / profitability | 124 / 149 / 189 ms | < 500 ms |
| Switch to data health | 25 ms | < 500 ms |
| Apply a market filter | 128 ms | < 500 ms |
| Switch to the funnel, product grain | **525 ms** | < 500 ms |

One number misses, by 5%, and it is worth naming because the cause is a design
decision rather than a slow query. Opening a snapshot registers DuckDB **views**
over the Parquet, so the join keys are folded per row, per query:
`trim(regexp_replace(lower(product_name), '[^a-z0-9]+', ' ', 'g'))` runs 466,728
times, which is 423 of those 525 ms. Nothing else in the funnel is expensive —
`funnel_totals` is 18 ms over both tables, and at category and department grain the
same view costs 165 ms and 83 ms.

28. **The fix is available and was rejected.** Materialising `access_logs` as a
table instead of a view folds once at open: the keyed query drops from 423 ms to
10 ms, but `open_warehouse` goes from 2 ms to 834 ms for that one table alone.
That trades 25 ms off an interaction for roughly a second on first render — moving
cost onto the metric with the tighter target, to fix the one with slack. A
distinct-label lookup table is worse on both counts (683 ms open, 78 ms query).
Folding at pipeline time instead would be faster still and is the wrong place: it
puts a derived column into snapshots whose value is being a faithful typed copy of
what the source emitted, and it would change every content hash and the field count
in the data dictionary to save 400 ms on one panel.

So the fold stays in the view definitions, and `test_snapshot_is_read_only` already
pins that — it asserts `DELETE FROM order_items` raises, which only holds while
these are views. The revisit trigger is a materially larger extract, not a faster
machine: at 100× these rows the fold belongs in the view definitions as a
materialised key, which is the same conclusion the consequences section reached
about pre-aggregation.

## Still open

| Question | Who should decide | Consequence of leaving it |
|---|---|---|
| Is the assumed audience right? | a stakeholder | Every view answers a question nobody confirmed is asked. One corrected row is cheaper than a rebuild. |
| Hosting, and whether a shared secret is enough | whoever owns the data | A password gate exists and is off by default (see the consequences section). It keeps a passer-by out of a hosted demo. It is not identity, so it cannot be enough for anyone outside the team while the clean data carries customer names, street addresses and 3,340 client IPs. Choosing an identity provider, or choosing not to host this data at all, is the decision still open. |
| Is the Oct 2017 break an extract artefact or a real change in how orders were placed? | whoever owns the source system | The dashboard warns and suggests per-order figures. It cannot tell the reader which of the two it is. |
| Does the 500 ms interaction target cover switching *to* a view, or only filtering within one? | whoever signs off the Phase 0 metrics | Under the second reading every measured number passes. Under the first, the funnel's 525 ms at product grain is a 5% miss, and closing it costs a second on first render (28). |

