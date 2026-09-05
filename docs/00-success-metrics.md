# Success Metrics (Phase 0)

The roadmap requires these to be "numerically defined, not vague". The numbers
below are **proposals with a stated basis**, not agreed targets — every one is
marked with what it costs to hit and what happens if it slips. Replace the
proposal column with a signed-off number, or accept the proposal explicitly.

## Data quality (gates Phase 2)

| Metric | Proposed | Basis | Measured by |
|---|---|---|---|
| Invalid values in clean output | **0%** | Anything unparseable is quarantined into a reject table, never coerced to null. "Some invalid" has no defensible threshold once wrong numbers are the failure mode. | validation rule set; count of rows in the reject table |
| Missing values in columns a view depends on | **< 5%**, and coverage shown on the chart | Below 5% an average is defensible with a footnote; above it the chart misleads. | `dtp.cli profile` per-column `missing_pct` |
| Duplicate rows after cleaning | **0**, verified on a re-profile | Roadmap 1.2 already states this. Case/whitespace-only duplicates count. | `n_normalised_duplicate_rows` |
| Unresolved BLOCKER risks | **0** before Phase 2 build starts | A blocker is defined as "produces a confidently wrong number". | `reports/risk-summary.md` |
| Rows lost between raw and clean | **accounted for, not zero** | Rows *should* be dropped (empty rows, duplicates). The requirement is that every dropped row is counted and attributed to a rule. | pipeline run log |

## Dashboard (gates Phase 4)

Phase 2 is built, so the first two rows now carry a measurement beside the
proposal. Both were taken against snapshot `20260903T224815` (647,247 rows, local
disk) through `streamlit.testing.v1.AppTest`, which excludes browser paint;
`docs/02-dashboard-design.md` (27–28) has the per-view table and the trade-off
behind the one miss.

| Metric | Proposed | Measured | Basis |
|---|---|---|---|
| First meaningful render | **< 2 s** on the demo machine | 1.19 s | Below ~2 s an interaction feels immediate; a judge or stakeholder clicking through will notice anything slower. |
| Filter / drill-down response | **< 500 ms** | 128 ms to filter, 25–189 ms to switch view, **525 ms** to switch to the funnel at product grain | Interactions should not need a spinner. Achievable because DuckDB queries local Parquet. |
| Concurrent readers | *not proposed* | eight readers: overview p95 341 ms, one question p95 167 ms, 3.2x one reader | Added in Phase 4 because the roadmap asks for load testing and nothing had measured it. It found a real defect: one DuckDB connection is not thread-safe, and the dashboard shares one. |
| Every stakeholder question answerable | **100% of the agreed question list** | 6 of the 6 mapped questions have a view, and a test fails if a view's question and the map disagree — but the list itself is still unconfirmed | The question-to-view map in Phase 2.1 defines the list; anything unmapped is out of scope, not a partial pass. |
| Known-anomaly test | **flags the seeded anomaly, 0 false positives on the fixture** | both hold: `test_the_injected_spike_is_flagged_and_named_as_a_month` finds exactly one, `test_a_flat_window_flags_nothing` finds none | Roadmap 2.3 "done when". |

## AI agent (gates Phase 4)

Phase 3 is built, so these carry measurements too. The important line is the first
one: it is split, because "correct answers" bundles two different questions and only
one of them can be answered without a key. `docs/04-qa-and-launch.md` has the full
argument and `reports/qa-report.md` is the run.

| Metric | Proposed | Measured | Basis |
|---|---|---|---|
| Correct answers on a held-out question set | **>= 90% of 20+ questions**, no human correction | **given the plan, 32 of 32**; the *model's own* reading of a question is **not measured** — no key has ever been used here. The keyless keyword matcher reads 14 of the 30 questions that state an outcome (47%), which is a floor: filters, thin-group thresholds and a grouping alongside a grain are exactly what it cannot do and what the model is for | Roadmap 3.2/3.3 asks for 10+; 20 is the minimum that makes a percentage meaningful. Below 90% the agent cannot be trusted unsupervised in a demo. |
| Stated number vs actual query result | **0 mismatches** | **0, structurally**: no figure the model emits is rendered, and any sentence carrying a figure or a group name the frame does not hold is dropped whole. Four fabricated summaries in the set must be caught and are | Non-negotiable. A hallucinated figure next to a real chart is worse than no agent. Enforced by the Phase 3.3 validation layer, not by prompt wording. |
| Out-of-scope questions | **100% get the defined fallback**, never a guess | **100%**: 21 screened questions each get their documented code, all 12 codes carry suggestions, and every suggestion is itself asked so a refusal cannot hand out a second one | Roadmap 3.1 guardrail spec. |
| Response time per query | **< 8 s** end to end | the platform's own share is **median 28 ms, max 499 ms** over the whole set, leaving ~7.5 s for the two API calls. Under eight concurrent readers, p95 167 ms | Slower than this and users stop asking follow-ups, which defeats the session-memory feature. |

## What is deliberately not measured

- **Adoption / usage rate.** In the roadmap's cross-phase list, but meaningless
  before launch and outside this team's control after it. Track post-launch if
  someone owns it.
- **Model or query cost per question.** Worth watching, but it is a budget
  question for whoever owns the API spend, not a project success gate.

## Sign-off

| Metric group | Agreed by | Date | Notes |
|---|---|---|---|
| Data quality | | | |
| Dashboard | | | |
| AI agent | | | |
