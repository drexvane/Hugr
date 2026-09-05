# Data-to-Insights Platform

Messy data → clean data → dashboard → AI query agent. Roadmap and phase gates in
[`project_roadmap.md`](project_roadmap.md).

**Phases 1, 2 and 3 are built, tested, and running against real data**: audit (1.1),
cleaning and validation (1.2), the automated pipeline with versioning, a data
dictionary and alerts (1.3), a six-view dashboard over a semantic metric layer
(2.1–2.3), and an AI query agent that turns a question into a validated plan rather
than into SQL (3.1–3.3). **Phase 4's QA, benchmarking and load testing are done** —
what remains needs people: user acceptance testing, and a hosting decision that
carries an access-control task with it
([`docs/04-qa-and-launch.md`](docs/04-qa-and-launch.md)). Every number below comes
from the DataCo Smart Supply Chain dataset in [`dataset/`](dataset) — 180,519 order
lines and 469,977 web access-log rows.

## Quick start

```bash
pip install -r requirements.txt
pip install -e .                    # puts `dtp` on PATH
```

```bash
dtp pipeline --raw dataset
```

```bash
streamlit run dashboard/app.py
```

```bash
dtp ask "revenue and margin by category"
```

Without installing, `PYTHONPATH=src python -m dtp.cli <command>` is equivalent.
The dashboard needs the `dashboard` extra (`pip install -e ".[dashboard]"`) if you
skipped `requirements.txt`, and a published snapshot to read. `ask` needs neither an
API key nor the `agent` extra to run — without a key it uses a keyless keyword
matcher and says so.

One command, five stages. A real run over the shipped dataset:

```
=== dtp pipeline ===
- clean [ok, 10.7s]: 2 table(s), 647,247 rows, 0 rejected cell(s)
- validate [ok, 1.5s]: PASS - every rule held
- snapshot [ok, 1.1s]: 20260903T012120 - 2 table(s), 647,247 rows
- dictionary [ok, 0.5s]: 53 field(s), 100.0% documented
- monitor [ok, 0.0s]: OK - nothing to report

PIPELINE OK - published snapshot 20260903T012120
```

Every command exits non-zero on failure, so each works as a CI gate: `audit` on a
BLOCKER, `validate` on a failed `error` rule, `dict` on an undocumented field,
`pipeline` on any of those. [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
runs them, plus the whole suite, on every push — no credential and no dataset needed,
because the fixture is generated and the agent's model is a stub. It also fails if a
run modifies a tracked file, since a test that quietly rewrites a committed report
makes the diff its own output.

## The one rule worth knowing

**A snapshot is published only if validation passed.** When an `error` rule
fails, `data/versions/` is left exactly as it was, the previous snapshot stays the
newest, and the run names the one downstream is still reading. Publishing anyway
needs `--force`, and a forced snapshot stamps `validation.forced` and a
`PUBLISHED WITH --force` note into its own manifest — so the escape hatch is
visible to whoever reads the data months later, not only to whoever typed the
flag. Forcing changes what is published, not the verdict: the run still exits 1.

## Commands

| Command | Does |
|---|---|
| `dtp pipeline` | clean → validate → snapshot → document → alert (this is the one you want) |
| `dtp clean` | apply `config/cleaning_rules.yml`, write `data/clean/*.parquet` |
| `dtp validate` | apply `config/validation_rules.yml` to the clean tables |
| `dtp dict` | rebuild `docs/data-dictionary.md` |
| `dtp versions` | list snapshots; `--diff OLD NEW` compares two |
| `dtp audit` | Phase 1.1: profile + schema matrix + ranked risks |
| `dtp profile` · `dtp schema` · `dtp risks` | one audit report each |
| `dtp synthetic` | regenerate the messy test fixture |
| `dtp ask "..."` | Phase 3: ask a question about a snapshot; `--repl` for follow-ups, `--stub` for no key |

`--raw`, `--clean`, `--versions`, `--config` and `--rules` override the defaults.
`--reports` and `--docs` redirect the generated output, which is what you want when
running against a source other than the usual one: the committed reports describe the
real extract, and a run over the fixture would otherwise replace them. Every report
names the source it described. `pipeline` also takes `--stop-after STAGE` (iterate on
rules without publishing), `--force`, and `--notes TEXT` for the manifest.

Two scripts sit outside the CLI because what they produce is evidence, not data:

```bash
python scripts/qa_report.py       # the critical-path journeys, timed, vs the targets
python scripts/agent_smoke.py     # the live model run; exits 2 without a key
```

## What gets written

| Output | Answers |
|---|---|
| `reports/cleaning-report.md` | every transformation applied, with a count and a reason per step |
| `reports/validation-report.md` | all 51 rules, pass/fail, and what each one protects |
| `reports/alerts.md` | ranked critical → warning → info, plus the thresholds in force |
| `reports/pipeline-report.md` | the stage list, timings, verdict |
| `docs/data-dictionary.md` | all 53 fields: type, nulls, range, rules, and who wrote the description |
| `reports/profiling/profiling_report.md` | per column: type, missing %, distinct count, every defect found |
| `reports/schema/schema_matrix.md` | which columns across sources mean the same thing, and whether they join |
| `reports/risk-summary.md` | Phase 1.1 findings ranked BLOCKER → LOW, each with impact and a fix |
| `reports/qa-report.md` | Phase 4: every critical-path journey against the real snapshot, timed against its target |

Each has a `.json` twin for programmatic use, except the QA report, which is a log.

## What cleaning the real data turned up

The four findings that change a number a stakeholder would otherwise read wrong:

1. **7,754 cancelled and suspected-fraud orders carry full sales and profit
   values.** An unfiltered `SUM(Sales)` overstates revenue by 4.27%
   ($36,784,735 → $35,214,430) and profit by 4.05%. The clean table carries
   `is_revenue_recognised` so the filter is explicit and hard to forget, rather
   than left to whoever writes the next query.
2. **The same trap in the shipping columns.** Those rows still hold
   `days_shipping_real`: 4,423 read as late and 1,774 as early for shipments that
   never happened. `late_delivery_risk` agrees with `delivery_status` on every
   row but disagrees with `real > scheduled` on exactly those 4,423, so
   `is_shipment_valid` gates any delay or on-time-rate metric.
3. **41.8% of customer zipcodes had lost their leading zeros** upstream, from
   being stored as a number. All 69,373 three-digit values are Puerto Rico, all
   6,135 four-digit ones are CT/MA/NJ/RI. Zero-padding to five is injective here
   — 995 distinct values before and after — so it reconstructs the original and
   nothing else could.
4. **Two stored ratio columns were rounded to 2dp at source**, off by up to 41.6%
   relatively ($9.30 on one line item). Dropped and recomputed from the
   primitives they were supposed to summarise.

Also: **12 columns dropped**, each with its justification in the report — 6
byte-identical duplicates (`Sales per customer` among them, which is a per-line
value despite the name, so anyone trusting the name double-counts), 2 columns
carrying no information at all, the 2 rounded ratios above, and `Customer Email` /
`Customer Password`, both pre-masked constants at source; the credential field
would go regardless. Then 3,249 byte-identical access-log rows removed and
counted; 3 rows whose city/state/zip had shifted one field repaired; 336,209
sentinel values recognised as missing; `EE. UU.` folded into `Estados Unidos`
across 111,146 rows; and two category labels disambiguated where one name spanned
two departments.

Nothing is silently imputed or dropped. A cell that will not coerce is nulled,
quarantined in `<table>__rejects.parquet` and counted — the row survives. Every
step lands in the cleaning report with its count and its reason.

## The rules are config, not code

Per-column policy lives in `config/cleaning_rules.yml`, the 51 checks in
`config/validation_rules.yml`, and the drift tolerances in
`config/monitoring.yml`. Someone who does not read Python can still review which
values count as missing, why a column was dropped, or how much row growth is
normal. Config expressions (`order_status not in [...]`, `discount / sales`) are
evaluated by a whitelisted AST walk, never `eval()`.

Severity is deliberate:

- `error` fails the run and blocks the snapshot; `warn` reports and does not.
- `expect_violations: 7754` pins a known count and fails in **both** directions —
  a defect quietly shrinking is news too. Every pinned count has to carry a
  `reason`, and a test enforces that.
- A rule that cannot run at all is escalated to `error` whatever it declared,
  because a check nobody is performing is worse than one that fails.
- Value checks use three-valued logic, like a SQL CHECK constraint: a null
  operand means *not checked*, not *failed*, and the exempted rows are excluded
  from the checked count and named in the report. `not_null` is the rule that
  owns nulls.

## Versioning

`data/versions/<timestamp>/` holds the Parquet files plus a `manifest.json`
recording, per table: rows, columns, dtypes, per-column null counts, and a
content hash. The hash comes from `pd.util.hash_pandas_object`, not the file
bytes — Parquet does not compress deterministically, so hashing the file would
report drift that is not there. Two runs over unchanged input produce identical
hashes; a changed value, a renamed column, a dtype change or reordered rows all
change it.

```bash
dtp versions --diff 20260903T005740 20260903T012120
```

reports added, removed, changed and unchanged tables, row deltas, schema changes,
and the columns where nulls appeared.

## Monitoring

Validation decides whether to publish; monitoring makes sure a failure is
*reported* rather than merely returned. Alerts come from four sources — failed
rules, cleaning (rejects, a row count disagreeing with config, a source file
nothing consumes), drift against the previous snapshot, and the thresholds — and
are ranked critical → warning → info. Only *critical* sets the exit code, so a
warning cannot turn a good run red.

Monitoring runs even when an earlier stage failed. Publishing stops at the gate;
reporting does not — a pipeline that skipped its own alerting on a bad run would
leave `reports/alerts.md` reading "nothing to report" at precisely the moment
there was something to report. `--stop-after` is the exception: a deliberate early
exit is not a failure, so it alerts nowhere.

There is deliberately **no network sink**. Alerts go to `reports/alerts.md`,
`.json`, stdout and the exit code, which is what CI and cron actually read.
Sending the contents of a dataset to a third party is a decision for whoever
operates this, not a default — and a test fails if `requests`, `urllib`,
`http.client`, `smtplib` or `socket` ever appears in that module.

## Known caveats

Two things documented rather than silently fixed:

- **Money is rounded to cents, which moves the grand total by $0.70.** Clean
  `SUM(order_item_sales)` is 36,784,734.31 against 36,784,735.01 summed from the
  raw strings. The difference is float32 noise being removed, not precision lost:
  the raw file carries up to 9 decimal places on money, and the digits are
  artefacts — `Sales` literally contains `119.9800034` for $119.98, on 155,013 of
  180,519 rows. Rounding recovers the intended value and makes aggregates
  reproducible. It is written down because someone will eventually reconcile
  against the raw CSV and find $0.70 missing.
- **The source taxonomy is odd, and left alone.** 'Cleats' sits in Apparel,
  'Cardio Equipment' in Footwear, 'Camping & Hiking' in Fan Shop, "Women's
  Apparel" in Golf. These are the vendor's own assignments and they are
  internally consistent, so re-parenting them would be inventing a taxonomy
  rather than cleaning one. The one genuinely ambiguous case *was* fixed: the
  label 'Electronics' meant two different things under two departments, and
  became `Electronics (Footwear)` and `Electronics (Outdoors)` — disambiguating a
  name, not moving a category.

Both are argued out in
[`docs/01-cleaning-and-pipeline-decisions.md`](docs/01-cleaning-and-pipeline-decisions.md),
along with the joinability consequence: **the two tables only join under
casefolding** — the access log is lowercase where the fact table is Title Case, so
a literal join returns zero rows. The referential rules fold case and prove the
join is sound. Phase 2 resolved where the fold belongs: `warehouse.py` registers
views that add `*_key` columns beside the originals, so one definition serves every
consumer and the snapshots stay a faithful typed copy of what the source emitted.

## Dashboard

```bash
streamlit run dashboard/app.py
```

Six views, each answering one question, over the newest snapshot (older ones are
selectable), plus a seventh screen that takes a question in words — see
[AI query agent](#ai-query-agent). Every figure is gated: cancelled and
suspected-fraud lines carry full money values and a delivery delay for shipments that
never happened, so the metric layer excludes them and the overview states the $1.57M
difference once, explicitly.

| View | Question | What is on it |
|---|---|---|
| Overview | Are we growing, and can I trust this number? | gated KPI row; revenue and profit by month with outlier months ringed; period-over-period; biggest movers |
| Delivery | Where are we late, by how much, is it worsening? | on-time rate as market × shipping mode; a bar per delay day; monthly late-rate trend |
| Profitability | What earns money rather than merely selling? | discount against margin, sized by revenue; profit by department with revenue behind it; each category against its own department; products losing money |
| Geography & segment | Which markets and segments deserve attention? | revenue and margin down market → region → country; revenue over time per place; segment mix |
| Funnel | Does traffic convert, and what is looked at but not bought? | views against orders per product; viewed-never-ordered and ordered-never-viewed |
| Data health | Did the last load pass, and what changed? | validation verdict, alerts ranked critical→info, snapshot history, diff against the previous snapshot |

Three things it does that a chart library does not give you for free:

- **Outlier months are found, not eyeballed** — robust z-score on median and MAD,
  flagged past 3.5. Not mean and σ, because one extreme month inflates σ and hides
  itself, which is the case the feature exists for.
- **Every panel carries one sentence computed from the frame that panel plots.** A
  caption cannot contradict the chart beside it. No model is involved on these six
  views: generated prose next to a chart is an unchecked claim until something
  verifies its numbers, which is what Phase 3's agent adds for the one screen that
  has a model on it.
- **It says when a comparison is not safe to make.** In this extract `market` is
  very nearly a period — 26 of 37 months hold exactly one — so the geography view
  says so, and from Oct 2017 an order carries one line where it previously carried
  three, so the overview warns that monthly totals fall across that boundary for a
  reason that is not commercial.

Measured on the shipped snapshot, without a browser in the loop: 1.19 s to first
render against the 2 s target in
[`docs/00-success-metrics.md`](docs/00-success-metrics.md), 128 ms to apply a
filter, 25–189 ms to switch view — and 525 ms to switch to the funnel at product
grain, which is the one number over target. 423 ms of it is folding a join key
466,728 times, per query, because snapshots are registered as DuckDB views rather
than materialised. Materialising fixes it and moves a second onto first render, so
it was measured and rejected rather than left unexamined.

No authentication, and that is a recorded decision rather than an oversight: this
is a local process reading local Parquet. No view surfaces a customer name, street
or client IP, and geography stops at city level — but that is a display choice, not
an access control, and the clean data carries all three. Hosting it is a Phase 4
decision that inherits an access-control task.
[`docs/02-dashboard-design.md`](docs/02-dashboard-design.md) has the whole
argument, including what the build changed.

The layering is the part worth copying: `warehouse.py` owns all SQL, `metrics.py`
is the only module that writes analytical SQL and carries the gate with each
metric, `insights.py` writes none, `charts.py` returns Plotly figures and imports
no Streamlit, `dtp.dashboard.views` returns a whole screen as values, and
`dashboard/app.py` only places them. Tests fail if the last two boundaries are
crossed — which is what lets Phase 3's agent reuse a view instead of reimplementing
it.

## AI query agent

```bash
dtp ask "the ten worst products by profit"
dtp ask --repl                        # a session: follow-ups patch the last plan
dtp ask --stub "revenue by market"    # no key, no network
```

**The model does not write SQL. It fills in a plan.** One tool, whose fields are
registry keys: which metrics, which dimensions, which window, which sort. Code turns
that into one `metrics.aggregate` call, so a question inherits the gate, the join and
the thin-group floor that Phase 2 already argued for. `SUM(Sales)` over this data
overstates revenue by $1.57M, and the obvious query is the wrong one here — a
text-to-SQL agent would write the obvious query.

```
$ dtp ask --stub "the ten worst products by profit"
snapshot 20260903T224815   model keyword-stub

Profit by product, lowest profit first, top 10
Profit: $3.81M
Products ranked by profit: SOLE E35 Elliptical, SOLE E25 Elliptical, GoPro
HERO3+ Black Edition Camera are the lowest at -$965.12.
note: the figures above are totals for the whole window, not only the 10 rows shown
```

That run used no key and made no network call — the plan came from the keyless
matcher, and everything after it is the same code a model's plan goes through.

Five steps per question, and the order is the design: screen the question, ask for a
plan, validate it against the registry, execute and render, then ask for one sentence
and check it. A question this platform does not answer never becomes an API call,
which is a privacy property before it is a cost one.

**Twelve refusals, each with something askable offered instead.** Naming a customer,
asking why, asking for a forecast, asking to change the data and asking for raw SQL
are refused before a token leaves the process; an unknown metric, an unknown
dimension, an unknown value, an empty result, a window outside the data and a funnel
outside the log window are refused by the validator, by name, with the near-misses
listed. `docs/03-agent-design.md` carries the table, and a test fails if a code exists
in one and not the other.

**No number on screen comes from the model.** Tiles, axes and tables are built from
the frame in code; the model is asked for prose only, and every figure in that prose
is matched against the frame it was shown. One that does not hold drops the whole
sentence — not the offending number, because editing prose to remove a figure leaves
grammar that reads as if it were checked — and the templated sentence takes its place,
with the drop stated rather than hidden. A name the frame does not carry counts as
fabrication too, even when every number in the sentence is real.

**Session memory is a plan, not a transcript.** "Break that down by region" is
`{"by": ["region"]}` applied to whatever was asked before — a much smaller thing to
get right than replaying a conversation, and inspectable, because `:plan` prints it.
Changing snapshot clears it rather than reinterpreting yesterday's keys against
different data. Nothing is written to disk: what people asked about customers is not
something this project stores, and a test asserts the process writes nothing.

**Two calls per question, both small**, and only the second carries data — the head of
an *aggregated* frame, group labels and metric values, truncated to 12 rows. No
row-level data and no personal column, because those are not dimensions and cannot be
in a plan. This is the one place in the project that sends anything anywhere;
`dtp.monitoring` has no network sink at all and a test enforces that.

**It runs without a key.** No `ANTHROPIC_API_KEY` means a keyless keyword matcher
that reads the plain shapes off the registry and declines the rest with the same
`CANNOT_ANSWER` reply a real model uses — so the pipeline behind the model is
demonstrable on a fresh clone, and what the stub cannot do it refuses rather than
guesses. The dashboard's seventh screen uses it the same way and names it on screen.

The reviewed question set is [`tests/agent_questions.yml`](tests/agent_questions.yml)
— 32 questions, each with the plan or refusal it should produce and why that is the
right reading, in a file a reviewer who does not read Python can argue with. It does
not call the API: a live run against the real model is
[`scripts/agent_smoke.py`](scripts/agent_smoke.py), which is key-gated, writes a log,
and treats a model that disagrees with the set as a measurement rather than a build
failure. That measurement belongs to whoever owns the spend.

Four defects this phase surfaced in code that was already tested and committed, each
now pinned from both sides: an ungrouped aggregate over an empty window returns one
row of nulls rather than no rows, so "revenue in LATAM in June 2017" became a tile
reading `n/a` beside a fluent sentence; the fabrication check flagged
`Women's Apparel` because `Apparel` is a department the frame did not hold, dropping a
true sentence; a refusal offered "orders by city" as the alternative to naming a
customer, when the geography path deliberately stops at country — a refusal handing
the user a second refusal; and `insights.say_ranking` re-sorted every frame by the
metric's own direction, so a worst-first chart was captioned with its *shallowest*
loss and the word "lead". The last one only shows up once a user picks the sort, which
is exactly what this phase added.

## Layout

```
config/          cleaning, validation and monitoring rules (YAML, reviewable)
dataset/         the DataCo source files
data/clean/      typed Parquet output + __rejects quarantine tables
data/versions/   timestamped snapshots, each with a manifest
data/_synthetic/ generated fixture with known defects
docs/            decision records + the generated data dictionary
reports/         generated cleaning, validation, alert, pipeline and audit output
src/dtp/         phase 1: clean, validate, versioning, dictionary, monitoring,
                 pipeline, profile, schema_map, risks, io_utils, cli
                 phase 2: warehouse, metrics, insights, charts, dashboard/views
                 phase 3: agent/ — plan, tools, guard, session, client
dashboard/       app.py — Streamlit placement only, importing the view layer
scripts/         make_synthetic_messy.py, agent_smoke.py (key-gated live run),
                 qa_report.py (the journeys against the real snapshot, timed)
tests/           925 tests
```

## Tests

```bash
python -m pytest
```

925 tests, no network, and no dependency on the real dataset — everything runs
against a seven-row fixture with deliberately injected defects or against
hand-built frames. By module: agent question set 137, agent guard 73, agent plan 61,
insights 77, metrics 58, charts 53, views 47, monitoring 44, agent session 42,
agent stub 39, clean 37, profile 37, pipeline 30, validate 27, dictionary 26,
versioning 22, warehouse 21, schema_map 20, smoke script 17, dashboard Ask screen 17,
`dtp ask` 16, end-to-end journeys 13, risks 11. Phase 2's own layers hold 256 of
them and Phase 3's 402, which is the ratio the layering was for: a boundary nobody
tests is a convention, not a boundary.

The end-to-end module is the one that runs the journeys rather than the layers:
raw files to a published snapshot and the gate that refuses to publish one; that
snapshot through the warehouse to all six views; a question, a follow-up and a
refusal in one session; and a fresh clone with no key, no `anthropic` install and no
`dataset/`. `scripts/qa_report.py` runs the same journeys against the real snapshot
and times them, because the timings, the privacy boundary and the agent's reading of
the question set are all things a seven-row fixture cannot measure.

The ones that assert judgement rather than plumbing:

- the snapshot gate refusing to publish, and leaving the previous snapshot as the
  newest thing downstream reads
- `--force` stamping the manifest, and the run still failing
- alerts still being written on a *refused* run — the failure has to reach
  `reports/alerts.md`, not just the exit code
- every checker failing by a known amount on a frame built to violate it
- content hashes surviving a Parquet round-trip and two independent writes
- `monitoring.py` containing no network client
- a vendor description that merely restates the column name counting as *absent*
- the profiler staying quiet where it should: no IQR outliers on a five-row
  column, no "looks like an identifier" on a numeric one

And the dashboard's, which are mostly about what a layer is *not* allowed to do:

- the gate travelling with the metric rather than the query, and every gated
  metric being a single aggregate call — DuckDB's `FILTER` binds to one call, so a
  composite expression would silently count ungated rows. The registry refuses
  that shape at construction rather than at read time.
- `charts.py` and `dtp.dashboard.views` importing no Streamlit, and `app.py`
  calling no aggregate, no `sql`, no `sum` — checked by walking the AST, because
  Phase 3's agent reuses these layers and cannot bring a web server with it
- no view exposing a personal column, under either the raw name or the prose
  heading it would be displayed with; and the same columns being unavailable as
  dimensions one layer down
- a figure surviving the JSON round trip the agent will use
- the funnel's totals coming from one query rather than a summed column, which
  overstates orders by 29% at product grain
- a chart declining to draw itself where no chart would be honest, an empty
  heatmap cell staying blank while a zero stays zero, and a truncated chart
  counting what it did not draw
- the snapshot picker opening on the newest snapshot, and the oldest one offering
  no diff rather than wrapping round to the newest
- a meta-test that fails if a new sentence builder is added without joining the
  sweep that checks all of them, and a test requiring each view's question to
  appear in `docs/02` in the same words

And the agent's, which are mostly about what a model is *not* allowed to reach:

- nothing in `agent/` containing the string `SELECT` or calling `sql`, and nothing
  importing Streamlit — the same AST walk Phase 2 uses, because one `Answer` has to
  serve a terminal, a browser and a test
- no personal column being nameable in a plan, and no personal column or value
  appearing in either call's payload — asserted against the recorded `(system, user)`
  pairs rather than described
- the screen firing on all 21 questions it is for and on none of 18 answerable
  near-misses: "drop groups with under 100 lines" is not a write, "percentage change
  month over month" is not a forecast, "update on the pipeline please" is not an
  update
- twelve true sentences surviving the verifier and four fabricated ones being caught,
  including a loss written either way round, a year in the prose not being read as a
  figure, and a decorated literal held to its own kind — 20 is a real percentage in
  the frame and not a real dollar amount
- every refusal code appearing in the design doc's table and in the code, every one
  being exercised by the question set, and one that is not in the table being
  impossible to construct
- a refused question costing exactly one API call and an answered one exactly two
- a follow-up receiving the previous *plan* and not the previous question, and a
  refused follow-up leaving the last working plan as the memory
- the session writing nothing to disk, and `--repl` saying so
- every fallback suggestion a refusal offers being asked for real, so a refusal
  cannot hand the user a second refusal

And Phase 4's, which found the two defects nothing single-threaded could:

- eight readers querying one warehouse at once — one DuckDB connection is not
  thread-safe and the dashboard caches one, so two people clicking at the same time
  used to raise. `Warehouse.sql` now gives each thread its own cursor, and both
  concurrency tests fail without it
- every git-tracked file under `reports/` hashed at session start and checked at the
  end, because `pipeline.write_report` defaults to the repository's own `reports/` and
  one test that forgot `out_dir` replaced the real 180,519-row report with a seven-row
  fixture's. Nothing failed at the time; the only evidence was a diff nobody read

## Environment notes

Python 3.12 with **pandas 3.x** — Copy-on-Write is default and the default string
dtype is Arrow-backed. Code written against pandas 2.x idioms will misbehave. See
[`docs/00-tech-stack.md`](docs/00-tech-stack.md) for the full stack decision and
what is still open.

## Status against the roadmap

| Phase | State |
|---|---|
| 0 — Discovery & planning | tech stack decided; success metrics proposed pending sign-off; **audience/decisions still want stakeholder input** |
| 1.1 — Audit & assessment | done, tested |
| 1.2 — Cleaning & standardization | done, tested; 51 rules, all passing on the real data |
| 1.3 — Pipeline & documentation | done, tested; one command, versioned snapshots, 100% field coverage, alerts |
| 2.1–2.3 — Dashboard: architecture, build, innovation layer | done, tested; six views, 251 tests over the Phase 2 layers |
| 2.4 — Polish & review | measured: 1.19 s first render against a 2 s target, 25–525 ms per interaction against 500 ms. **Design review and stakeholder sign-off want a stakeholder** |
| 3.1 — Architecture & query layer | done, tested; one tool over the metric registry rather than generated SQL, twelve refusal codes each with a fallback |
| 3.2 — Agent build | done, tested; 32 reviewed questions in `tests/agent_questions.yml`, the dashboard's own chart selection reused rather than reimplemented |
| 3.3 — Refinement | done, tested; session memory as a plan patch, a two-layer hallucination guardrail, and `dtp ask` / a dashboard Ask screen over one `Answer`. **The end-to-end log against the real model is `scripts/agent_smoke.py` and wants whoever owns the API spend** |
| 4 — Testing, polish & launch | QA, accuracy benchmarking and load testing done — 6 journeys, 57 checks, 0 failures against the real snapshot; see [`docs/04-qa-and-launch.md`](docs/04-qa-and-launch.md). **UAT wants users and launch wants a hosting decision, which carries the access-control task with it** |
