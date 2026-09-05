"""The AI query agent: natural language in, a chart and a checked sentence out.

`docs/03-agent-design.md` argues the design; the short version is that the model
fills in a **plan** - registry metric keys, dimension keys, a window, a sort - and
code turns that into one `metrics.aggregate` call. It never writes SQL and never
states a figure, because `SUM(Sales)` over this data overstates revenue by
$1,570,305.33 and the obvious query is the wrong one here.

Five modules, in the order a question passes through them:

    session   the ask loop, the `Answer` value, and the follow-up patch
    tools     the tool schema and both prompts, generated from the registry
    plan      `Plan`, validation against the registry, execution through `metrics`
    guard     refusal codes, the scope screen, and the numeric verifier
    client    the `Model` protocol, the anthropic adapter, and two keyless stand-ins

Two boundaries hold by construction and are pinned by tests: nothing here writes
SQL (`plan.execute` calls `metrics`, still the only module that does), and nothing
here imports Streamlit - so one `Answer` serves the CLI, the dashboard and a test.

Importing this package needs no credential and no `anthropic` install: the SDK is
imported inside `client.AnthropicModel`, and `client.ScriptedModel` is a full
implementation of the protocol for everything else. `client.KeywordModel` is the
third, for a keyless demo: it matches registry keys against the question's own words
and declines anything it does not recognise, which is what `dtp ask --stub` runs.
"""

from dtp.agent import client, guard, plan, session, tools
from dtp.agent.client import (AnthropicModel, KeywordModel, Model, Reply,
                              ScriptedModel, ToolCall)
from dtp.agent.guard import REFUSALS, PlanError, Refusal, verify_summary
from dtp.agent.plan import Plan, execute, validate
from dtp.agent.session import Answer, Session, Tile, ask

__all__ = [
    "client", "guard", "plan", "session", "tools",
    "AnthropicModel", "KeywordModel", "Model", "Reply", "ScriptedModel", "ToolCall",
    "REFUSALS", "PlanError", "Refusal", "verify_summary",
    "Plan", "execute", "validate",
    "Answer", "Session", "Tile", "ask",
]
