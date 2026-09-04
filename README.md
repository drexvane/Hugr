# Data-to-Insights Platform

Messy data → clean data → dashboard → AI query agent. Roadmap and phase gates in
[`project_roadmap.md`](project_roadmap.md).

**Phases 1 and 2 are built, tested, and running against real data**: audit (1.1),
cleaning and validation (1.2), the automated pipeline with versioning, a data
dictionary and alerts (1.3), and a six-view dashboard over a semantic metric layer
(2.1–2.3). Every number below comes from the DataCo Smart Supply Chain dataset in
[`dataset/`](dataset) — 180,519 order lines and 469,977 web access-log rows.

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

Without installing, `PYTHONPATH=src python -m dtp.cli <command>` is equivalent.
The dashboard needs the `dashboard` extra (`pip install -e ".[dashboard]"`) if you
skipped `requirements.txt`, and a published snapshot to read.

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
`pipeline` on any of those.

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

`--raw`, `--clean`, `--versions`, `--config` and `--rules` override the defaults.
`pipeline` also takes `--stop-after STAGE` (iterate on rules without publishing),
`--force`, and `--notes TEXT` for the manifest.

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

Each has a `.json` twin for programmatic use.

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
selectable). Every figure is gated: cancelled and suspected-fraud lines carry full
money values and a delivery delay for shipments that never happened, so the metric
layer excludes them and the overview states the $1.57M difference once, explicitly.

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
  caption cannot contradict the chart beside it. No model is involved in Phase 2:
  generated prose next to a chart is an unchecked claim until Phase 3 builds the
  guardrail that verifies its numbers.
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
dashboard/       app.py — Streamlit placement only, importing the view layer
scripts/         make_synthetic_messy.py
tests/           503 tests
```

## Tests

```bash
python -m pytest
```

503 tests, no network, and no dependency on the real dataset — everything runs
against a seven-row fixture with deliberately injected defects or against
hand-built frames. By module: insights 74, metrics 58, charts 53, views 47,
monitoring 44, clean 37, profile 37, pipeline 28, validate 27, dictionary 26,
versioning 22, schema_map 20, warehouse 19, risks 11. Phase 2's own layers hold
251 of them, which is the ratio the layering was for: a boundary nobody tests is a
convention, not a boundary.

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
| 3 — AI query agent | not started |
| 4 — Testing, polish & launch | not started |
