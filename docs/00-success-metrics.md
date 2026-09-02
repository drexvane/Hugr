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

| Metric | Proposed | Basis |
|---|---|---|
| First meaningful render | **< 2 s** on the demo machine | Below ~2 s an interaction feels immediate; a judge or stakeholder clicking through will notice anything slower. |
| Filter / drill-down response | **< 500 ms** | Interactions should not need a spinner. Achievable because DuckDB queries local Parquet. |
| Every stakeholder question answerable | **100% of the agreed question list** | The question-to-view map in Phase 2.1 defines the list; anything unmapped is out of scope, not a partial pass. |
| Known-anomaly test | **flags the seeded anomaly, 0 false positives on the fixture** | Roadmap 2.3 "done when". |

## AI agent (gates Phase 4)

| Metric | Proposed | Basis |
|---|---|---|
| Correct answers on a held-out question set | **>= 90% of 20+ questions**, no human correction | Roadmap 3.2/3.3 asks for 10+; 20 is the minimum that makes a percentage meaningful. Below 90% the agent cannot be trusted unsupervised in a demo. |
| Stated number vs actual query result | **0 mismatches** | Non-negotiable. A hallucinated figure next to a real chart is worse than no agent. Enforced by the Phase 3.3 validation layer, not by prompt wording. |
| Out-of-scope questions | **100% get the defined fallback**, never a guess | Roadmap 3.1 guardrail spec. |
| Response time per query | **< 8 s** end to end | Slower than this and users stop asking follow-ups, which defeats the session-memory feature. |

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
