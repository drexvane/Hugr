# Audience & Decisions (Phase 0)

**Status: blocked on stakeholder input.** This is the one Phase 0 artefact that
cannot be inferred from the data or the code — it has to come from the people who
will use the dashboard. It is first in the roadmap for a reason: Phase 2.1 maps
questions to views, and without the questions there is nothing to map.

## Who uses this

| Role | How often | What they do with it | Reads a chart, or asks a question? |
|---|---|---|---|
| _TBD_ | | | |

The last column decides how much of Phase 3 matters. Users who arrive with a
specific question are the AI agent's audience; users who scan a fixed view every
Monday are the dashboard's.

## Decisions this platform must support

For each, record the decision — not the topic. "Regional performance" is a topic;
"which two regions get next quarter's headcount" is a decision.

| Decision | Who makes it | How often | What they need to see to make it | Consequence of a wrong number |
|---|---|---|---|---|
| _TBD_ | | | | |

That last column feeds severity ranking directly: a defect in a column driving a
budget decision is a blocker, the same defect in a cosmetic column is not.

## Questions the dashboard must answer

Numbered, because Phase 2.1 maps each to a view and Phase 3 uses them as the
agent's test set. Aim for 10-20.

1. _TBD_

## Explicitly out of scope

Recording this prevents scope creep later and gives the agent's guardrail spec
(Phase 3.1) its list of questions to decline.

- _TBD_

## How to unblock this

A 30-minute conversation per role covers it. Three questions do most of the work:

1. "Last time you needed this data, what were you trying to decide?"
2. "What did you have to ask someone else for, because the existing reports
   didn't show it?"
3. "If one number on this dashboard were wrong, which one would cause the most
   damage before anyone noticed?"

Answer 3 is worth capturing verbatim — it sets the priority order for cleaning.
