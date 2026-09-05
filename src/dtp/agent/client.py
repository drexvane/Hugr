"""The model, behind a protocol thin enough that the tests do not need one.

Everything else in `agent/` is offline: the validator, the executor, the verifier,
the refusals and the session patching all run against frames and the registry. This
module is the seam. It exists so that:

* **no credential is needed to build or test** (design reason 18) - `ScriptedModel`
  is a full implementation of the protocol, so every guardrail can be tested against
  a model that lies, refuses, or returns nonsense, and `KeywordModel` drives the
  whole pipeline from a question with no network at all;
* **the SDK is imported lazily**, inside the adapter, so `import dtp.agent` works in
  an environment where `anthropic` is not installed at all;
* the two calls a question makes are both *stateless* - a system prompt and one user
  message. Session memory is a plan in the prompt, not a transcript, so nothing here
  accumulates.

`ANTHROPIC_API_KEY` is read from the environment or a gitignored `.env`. Nothing in
this repository contains a key, and `.env.example` documents the name and nothing
else.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from .. import ROOT

DEFAULT_MODEL = "claude-sonnet-5"
MODEL_ENV = "DTP_AGENT_MODEL"
KEY_ENV = "ANTHROPIC_API_KEY"

PLAN_TOKENS = 1024      # a tool call, not prose
SUMMARY_TOKENS = 300    # one sentence, two at most


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    """What one model turn produced: prose, a tool call, or both.

    A value rather than the SDK's response object so that `session.py` never
    touches a vendor type, and so a scripted reply and a real one are the same
    thing to everything downstream.
    """

    text: str = ""
    tool_call: ToolCall | None = None


class Model(Protocol):
    """Two calls' worth of surface, and no more."""

    name: str

    def respond(self, system: str, user: str,
                tools: Sequence[dict[str, Any]] | None = None,
                max_tokens: int = PLAN_TOKENS) -> Reply:
        ...


def load_dotenv(path: Path | None = None) -> None:
    """Read `.env` into the environment without overriding what is already set.

    Deliberately tiny and dependency-free: it handles `KEY=value`, quotes and
    comments, and nothing else. An existing environment variable always wins, so a
    stale `.env` cannot silently override a key exported for one run.
    """
    path = path or ROOT / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def api_key() -> str | None:
    load_dotenv()
    return os.environ.get(KEY_ENV) or None


def model_id() -> str:
    return os.environ.get(MODEL_ENV) or DEFAULT_MODEL


class AnthropicModel:
    """The real client. Imported lazily so the package works without the SDK."""

    def __init__(self, model: str | None = None, key: str | None = None) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:      # pragma: no cover - environment-dependent
            raise RuntimeError(
                "the anthropic package is not installed. `pip install -e "
                "\".[agent]\"`, or use the CLI's --stub for a keyless run."
            ) from exc
        resolved = key or api_key()
        if not resolved:
            raise RuntimeError(
                KEY_ENV + " is not set. Put it in the environment or in a .env "
                "file (see .env.example); nothing in this repository holds a key.")
        self.name = model or model_id()
        self._client = Anthropic(api_key=resolved)

    def respond(self, system: str, user: str,
                tools: Sequence[dict[str, Any]] | None = None,
                max_tokens: int = PLAN_TOKENS) -> Reply:
        kwargs: dict[str, Any] = {
            "model": self.name,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if tools:
            kwargs["tools"] = list(tools)
        message = self._client.messages.create(**kwargs)
        text, call = "", None
        for block in message.content:
            kind = getattr(block, "type", "")
            if kind == "text":
                text += block.text
            elif kind == "tool_use" and call is None:
                call = ToolCall(name=block.name, input=dict(block.input or {}))
        return Reply(text=text.strip(), tool_call=call)


@dataclass
class ScriptedModel:
    """A model whose answers are written by the test, not by a network call.

    Two ways to script it, because tests want both:

    * a **sequence** of replies, consumed in order - for a conversation, including
      a follow-up whose second reply patches the first plan;
    * a **mapping** from a substring of the user message to a reply - for a table of
      questions where order is an implementation detail.

    `calls` records every (system, user) pair, which is how the boundary tests
    assert what actually left the process: no personal column, and only the head of
    an aggregated frame.
    """

    replies: Sequence[Reply] | dict[str, Reply] = ()
    name: str = "scripted"
    default: Reply = field(default_factory=Reply)
    calls: list[tuple[str, str]] = field(default_factory=list)
    _index: int = 0

    def respond(self, system: str, user: str,
                tools: Sequence[dict[str, Any]] | None = None,
                max_tokens: int = PLAN_TOKENS) -> Reply:
        self.calls.append((system, user))
        if isinstance(self.replies, dict):
            for needle, reply in self.replies.items():
                if needle.lower() in user.lower():
                    return reply
            return self.default
        if self._index < len(self.replies):
            reply = self.replies[self._index]
            self._index += 1
            return reply
        return self.default


def plan_reply(**fields: Any) -> Reply:
    """`plan_reply(metrics=["revenue"], by=["market"])` - a scripted tool call."""
    from .tools import TOOL_NAME
    return Reply(tool_call=ToolCall(name=TOOL_NAME, input=dict(fields)))


# --------------------------------------------------------------------------- #
# the keyless stub
# --------------------------------------------------------------------------- #

_ORDINALS = re.compile(r"\b(?:top|best|worst|bottom|lowest|highest)\s+(\w+)\b"
                       r"|\b(\w+)\s+(?:worst|best|lowest|highest)\b")
_YEAR = re.compile(r"\b(20\d{2})\b")
_ASCENDING = ("worst", "bottom", "lowest", "least", "slowest")
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20}

# Named only when the question names it: it is the trap figure, and matching it on
# the word "revenue" would make the stub answer with the number the whole registry
# exists to avoid (`SUM(Sales)` overstates revenue by $1,570,305.33).
_ON_REQUEST = ("revenue_ungated",)


class KeywordModel:
    """A keyless stand-in: registry keys matched against the question's own words.

    Not a model and not pretending to be one. It exists so that `dtp ask --stub` and
    the dashboard's Ask tab work with no credential and no network - the whole
    pipeline behind the model (validate, execute, chart, verify, refuse) is the part
    worth demonstrating, and none of it needs prose to be generated.

    Two consequences, both deliberate:

    * it recognises the plain shapes - "revenue by market", "the 5 worst products by
      profit", "margin by category in 2017" - and **declines everything else**, with
      the same `CANNOT_ANSWER` tag a real model uses. A stub that guessed would
      answer a question nobody asked, which is the failure the guardrails are for;
    * it writes **no summary**, so the answer falls back to the templated sentence
      derived from the frame. That is the same downgrade a withheld sentence gets,
      and it is honest: there is no model here to have written one.

    A follow-up is the one case where naming no metric is fine - "by region" is a
    patch, and the metric is already in the plan the prompt carries. It reads that
    from the prompt the same way a model would, which is what makes `--repl` work
    without a key.
    """

    name = "keyword-stub"
    FOLLOW_UP = "This is a follow-up"

    def respond(self, system: str, user: str,
                tools: Sequence[dict[str, Any]] | None = None,
                max_tokens: int = PLAN_TOKENS) -> Reply:
        if tools is None:
            return Reply(text="")          # the summary call: no sentence to offer
        fields = read_keywords(user)
        patching = self.FOLLOW_UP in system and bool(fields)
        if not fields.get("metrics") and not patching:
            from .tools import DECLINE_TAG
            return Reply(text=(
                DECLINE_TAG + ": This is the keyless stub, which only recognises a "
                "question naming a metric it knows - try \"revenue by market\". "
                "Set " + KEY_ENV + " for the real thing."))
        return plan_reply(**fields)


def _needles(registry: dict[str, Any]) -> list[tuple[str, str]]:
    """Every key and label as a searchable phrase, longest first.

    Longest first is what keeps `discount_pct` ("discount rate") from being eaten by
    `discount`, and `view_to_order_pct` from being read as `views`. Most plurals fall
    out of substring matching - "products" contains "product" - so only the `y/ies`
    ones need spelling out.
    """
    pairs: list[tuple[str, str]] = []
    for key, item in registry.items():
        phrases = {key.replace("_", " "), item.label.lower()}
        phrases |= {p[:-1] + "ies" for p in list(phrases) if p.endswith("y")}
        pairs += [(phrase, key) for phrase in phrases]
    return sorted(pairs, key=lambda p: -len(p[0]))


def _found(text: str, registry: dict[str, Any]) -> list[str]:
    """Keys whose phrase appears, in the order the question named them.

    Matching is longest-first so `discount_pct` is not eaten by `discount`, and each
    match is blanked out so no two keys claim the same words. The result is then
    re-sorted by position, because "views and orders" is a different funnel from
    "orders and views" once it is on screen.
    """
    hits: list[tuple[int, str]] = []
    for phrase, key in _needles(registry):
        if any(key == seen for _, seen in hits):
            continue
        if key in _ON_REQUEST and key.replace("_", " ") not in text:
            continue
        at = text.find(phrase)
        if at >= 0:
            hits.append((at, key))
            text = text[:at] + " " * len(phrase) + text[at + len(phrase):]
    return [key for _, key in sorted(hits)]


# A trend word to the grain it asks for. Checked before `by`, because "monthly
# revenue" is a time series and `by: [month]` is a bar chart of twelve bars.
_GRAINS = (("month", ("monthly", "by month", "per month", "month over month",
                      "each month", "over the months")),
           ("quarter", ("quarterly", "by quarter", "per quarter", "each quarter")),
           ("year", ("yearly", "annually", "by year", "per year", "each year",
                     "year over year")))


def read_keywords(question: str) -> dict[str, Any]:
    """A question to a plan's fields, by matching registry keys and labels.

    Exposed because the CLI's `--stub` and the dashboard both want to say what was
    understood, and a caller that can only see the answer cannot.
    """
    from .. import metrics as M

    text = " " + (question or "").lower().strip() + " "
    head, _, tail = text.partition(" by ")
    fields: dict[str, Any] = {}
    metrics = _found(head, M.METRICS) or _found(text, M.METRICS)
    if metrics:
        fields["metrics"] = metrics
    grain = next((key for key, words in _GRAINS
                  if any(word in text for word in words)), None)
    if grain:
        fields["grain"] = grain
    else:
        # A grouping can be named on either side of "by": "revenue by market", but
        # also "the ten worst products by profit". Never both, so the ranking case
        # does not group by the metric it ranks on.
        dimensions = _found(tail, M.DIMENSIONS) or _found(head, M.DIMENSIONS)
        if dimensions:
            fields["by"] = dimensions
    ordinal = _ORDINALS.search(text)
    if ordinal:
        # "top 10" and "the ten worst" both name a count; a word that is neither a
        # digit nor a number leaves the ranking unlimited rather than guessing one.
        word = ordinal.group(1) or ordinal.group(2)
        count = int(word) if word.isdigit() else _COUNTS.get(word)
        if count:
            fields["limit"] = count
    if any(word in text for word in _ASCENDING) and metrics:
        # A bare key is ascending; `-` prefixed is descending, and the validator
        # already defaults to `-<first metric>` when a limit arrives without a sort.
        fields["order_by"] = metrics[0]
    year = _YEAR.search(text)
    if year:
        fields["date_from"] = year.group(1) + "-01-01"
        fields["date_to"] = year.group(1) + "-12-31"
    return fields
