# QA, Performance and Launch (Phase 4)

Phase 4 asks for five things: end-to-end QA, accuracy benchmarking, load testing,
user acceptance testing, and launch. **Three are done and two cannot be done by this
team** — UAT needs users and launch needs a hosting decision that carries an
access-control task with it. This file records what was measured, what it found, and
what the two open ones need from whom.

Everything below is reproducible:

```bash
python -m pytest                  # 923 tests, no network, no credential
python scripts/qa_report.py       # the journeys against the real snapshot
python scripts/agent_smoke.py     # the live model run, key-gated
```

## End-to-end QA

Two instruments, deliberately:

- **`tests/test_end_to_end.py`** — the four critical-path journeys as assertions,
  against the seven-row fixture, so they run in CI on a clone with no `dataset/`.
  Publish (raw → pipeline → snapshot, and the gate refusing to publish), read
  (snapshot → warehouse → all six views), ask (question → follow-up → refusal in one
  session), and fresh clone (no key, no SDK, no data).
- **`scripts/qa_report.py`** — the same journeys against the real snapshot, timed,
  writing `reports/qa-report.md`. Three things cannot be checked on a fixture and are
  the reason this exists: the timings, which only mean something at 180,519 rows; the
  privacy boundary, because the fixture has no personal columns at all; and the
  agent's reading of the reviewed question set, which needs the real dimension values
  behind a filter.

Last run against snapshot `20260903T224815`: **6 journeys, 57 checks, 0 failures.**

The journeys and what each is for:

| Journey | Proves |
|---|---|
| Open the published snapshot | both tables registered, the window readable, and **every registry key resolving against the real columns** — 17 metrics total, 15 dimensions group, and the funnel's two refuse as documented. A renamed clean column shows up here and nowhere else. |
| Read every view | all six build with tiles and drawn panels; a filter and a market → region drill apply |
| The privacy boundary, on the real data | the personal columns *are* in the clean table, no view surfaces one, none is a registry dimension, and a naming question is refused |
| Ask the reviewed question set | all 32 questions asked without raising, and the agreement number below |
| Hold a conversation | a two-word follow-up keeps the metric, a refusal does not cost the memory, reset clears it |
| Serve more than one reader at once | eight concurrent readers, and that two sessions do not share memory |

## What QA found

Two defects, both in code that already had tests, both now pinned:

1. **One DuckDB connection is not thread-safe, and the dashboard shares one.**
   `dashboard/app.py` caches the warehouse with `st.cache_resource`, and Streamlit
   serves concurrent sessions on threads — so two people clicking at the same time
   raised `Attempting to execute an unsuccessful or closed pending query result`.
   Eight readers reproduced it within a second. `Warehouse.sql` now hands each thread
   its own cursor onto the same database; the views are catalogue-level, so every
   cursor sees them. `test_one_warehouse_serves_concurrent_readers` and
   `test_every_thread_sees_the_same_catalogue` both fail without the fix.

   This is the defect that most justifies the phase: nothing single-threaded could
   have found it, and it would have shown up in front of an audience.

2. **A test overwrote the committed pipeline report.** `pipeline.write_report`
   defaults to the repository's own `reports/`, and one call in the new end-to-end
   module forgot `out_dir` — replacing the real 180,519-row report with a seven-row
   fixture's. Nothing failed; the only evidence was a diff nobody was reading. The
   `conftest` docstring had warned about exactly this, which is the lesson: a comment
   is not a guard. There is now a session fixture that hashes every git-tracked file
   under `reports/` and fails the run if one changed.

## Accuracy benchmarking

The target in `docs/00-success-metrics.md` is **≥ 90% of 20+ questions, no human
correction**. It is about a real model, and this project has never had a key. So the
honest position is three numbers, not one:

| What | Measured | On what |
|---|---|---|
| Given the plan, is the answer right? | **32 of 32** | `tests/test_agent_questions.py` asserts each question's frame, chart type, caption, tiles and sentence against the plan the set states |
| Out-of-scope questions getting a defined fallback | **100%** | 21 screened questions each get their documented code; all 12 codes carry suggestions; and every suggestion is itself asked, so a refusal cannot hand the user a second refusal |
| Stated number vs actual query result | **0 mismatches, structurally** | no figure the model emits is ever rendered — tiles and axes are built from the frame — and the verifier drops any sentence carrying a figure or a group name the frame does not hold |
| **The model's own reading of a question** | **not measured** | needs `scripts/agent_smoke.py` and a key |

The keyless matcher gives a floor for the last row: **14 of the 30 questions that
state an outcome, 47%**. That number is useful precisely because it is low. What it
misses is the whole list of things a registry cannot infer from keywords:

- a filter — "revenue in europe by category", "revenue in Wakanda"
- a thin-group threshold — "ignoring modes under ten lines", "over 5,000 lines"
- a grouping *alongside* a grain — "monthly revenue by market"
- a metric the data does not have, named as though it did — "how many returns did we
  get?" earns `unknown_metric` from a model and `unparseable` from the matcher

So the gap between 47% and 90% is the model's job description, and the plan to close
it is one command by someone who owns the spend. Until then the platform is honest
about which one answered: every surface prints the model's name, and `keyword-stub` is
not a model.

## Performance

Local disk, snapshot `20260903T224815`, 647,247 rows, no browser paint.

| Interaction | Measured | Target | Verdict |
|---|---|---|---|
| Build the heaviest view (Funnel) | 505 ms | < 2 s first render | pass |
| Build the other five | 7–278 ms | < 2 s | pass |
| Apply a filter | 82 ms | < 500 ms | pass |
| Drill market → region | 112 ms | < 500 ms | pass |
| Agent, local path per question | median 28 ms, max 499 ms | — | leaves ~7.5 s of the 8 s budget for two API calls |
| Eight readers, overview | median 277 ms, p95 341 ms | < 500 ms | pass, 3.2× one reader |
| Eight readers, one question | median 113 ms, p95 167 ms | < 8 s | pass |

The one known miss is unchanged and already argued out in
`docs/02-dashboard-design.md` (27–28): *switching to* the funnel at product grain is
525 ms against a 500 ms interaction target, 423 ms of which is folding a join key
466,728 times because snapshots are registered as views rather than materialised.
Materialising fixes it and moves a second onto first render, so it was measured and
rejected rather than left unexamined. Whether "interaction" covers switching views at
all is one of the open questions in that document.

Concurrency scales sub-linearly rather than serially — eight readers cost 3.2× one
reader, not 8× — because DuckDB parallelises across cursors. Eight is a guess at peak
for a demo; `--readers N` measures any other number.

## What needs a person

| Item | Who | What is blocked, and what happens meanwhile |
|---|---|---|
| **User acceptance testing** | the intended users | Roadmap 4 wants UAT sessions and structured feedback. Nobody has watched a real user ask this agent a question. The reviewed question set is 32 questions written by the people who built it, which is a substitute for that, not a replacement. |
| **Launch** | whoever owns hosting | The dashboard has **no authentication**, by recorded decision (`docs/02-dashboard-design.md`): it is a local process reading local Parquet. Hosting it inherits an access-control task, because no view showing a personal column is a display choice, not a control. Nothing should be exposed to a network before that is built. |
| **The live model run** | whoever owns the API spend | `scripts/agent_smoke.py`, one command, writes a log. Fills in the one blank in the accuracy table. |
| **The success metrics themselves** | whoever signs off Phase 0 | Every number in `docs/00-success-metrics.md` is still a proposal with a basis, including the 90% this phase is measured against. |

## Not done, and why not

- **A staging environment.** Roadmap 4's load test names one. There is no environment
  to stage into until the hosting decision above, so the load test runs against the
  same local snapshot everything else does, and says so.
- **Monitoring in production.** `dtp.monitoring` exists and runs in the pipeline, but
  it has no network sink on purpose and a test enforces that. Alerting a person rather
  than a file is a launch decision, not a code one.
