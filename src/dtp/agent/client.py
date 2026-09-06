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


def is_ollama_available(host: str | None = None) -> bool:
    """Check if local Ollama server is running and accessible."""
    import urllib.request
    resolved = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    try:
        req = urllib.request.Request(f"{resolved}/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


class OllamaModel:
    """Local Ollama client that drives both structured plans and narrative summaries."""

    def __init__(self, model: str | None = None, host: str | None = None) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL") or "gemma3:4b"
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.name = f"ollama-{self.model}"

    def respond(self, system: str, user: str,
                tools: Sequence[dict[str, Any]] | None = None,
                max_tokens: int = PLAN_TOKENS) -> Reply:
        import json
        import urllib.request
        from .tools import TOOL_NAME, DECLINE_TAG

        if tools is None:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.0},
            }
            try:
                req = urllib.request.Request(
                    f"{self.host}/api/chat",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = json.loads(resp.read().decode())
                    text = data.get("message", {}).get("content", "").strip()
                    if text.startswith("```"):
                        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
                        text = re.sub(r"\s*```$", "", text).strip()
                    return Reply(text=text)
            except Exception:
                return Reply(text="")

        prompt_system = (
            system
            + f"\nYou must respond ONLY with a JSON object representing a tool call to {TOOL_NAME}: "
            + '{"name": "query_data", "input": {"metrics": [...], "by": [...], "limit": ..., "order_by": ..., "where": {...}, "grain": ...}} '
            + f"or if the question cannot be answered: "
            + f'{{"decline": "{DECLINE_TAG}: <reason>"}}'
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.0},
        }
        try:
            req = urllib.request.Request(
                f"{self.host}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode())
                content = data.get("message", {}).get("content", "").strip()
                if content.startswith("```"):
                    content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
                    content = re.sub(r"\s*```$", "", content).strip()
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    if "decline" in parsed:
                        return Reply(text=parsed["decline"])
                    if "name" in parsed and "input" in parsed and isinstance(parsed["input"], dict):
                        return Reply(tool_call=ToolCall(name=parsed["name"], input=dict(parsed["input"])))
                    if "metrics" in parsed:
                        return Reply(tool_call=ToolCall(name=TOOL_NAME, input=parsed))
                    if "input" in parsed and isinstance(parsed["input"], dict):
                        return Reply(tool_call=ToolCall(name=TOOL_NAME, input=parsed["input"]))
                return Reply(text=content)
        except Exception:
            fields = read_keywords(user)
            if fields.get("metrics"):
                return plan_reply(**fields)
            return Reply(text=f"{DECLINE_TAG}: could not parse plan from Ollama")


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
    """

    name = "keyword-stub"
    FOLLOW_UP = "This is a follow-up"

    def __init__(self, catalog: Any = None) -> None:
        self.catalog = catalog

    def respond(self, system: str, user: str,
                tools: Sequence[dict[str, Any]] | None = None,
                max_tokens: int = PLAN_TOKENS) -> Reply:
        if tools is None:
            return Reply(text="")          # the summary call: no sentence to offer
        fields = read_keywords(user, catalog=self.catalog)
        is_follow_up = user.strip().lower().startswith(
            ("by ", "and by ", "now by ", "break down by ", "split by ", "where ", "top ", "bottom ", "in ", "for ")
        ) or not user.lower().partition(" by ")[0].strip()
        patching = self.FOLLOW_UP in system and bool(fields) and is_follow_up
        if not fields.get("metrics") and not patching:
            from .tools import DECLINE_TAG
            return Reply(text=(
                DECLINE_TAG + ": This is the keyless stub, which only recognises a "
                "question naming a metric it knows - try asking for a known measure "
                "or group. Set " + KEY_ENV + " or run local Ollama for full language parsing."))
        return plan_reply(**fields)


def _needles(registry: dict[str, Any]) -> list[tuple[str, str]]:
    """Every key and label as a searchable phrase, longest first."""
    pairs: list[tuple[str, str]] = []
    prefix_map = {
        "sum_": ["sum", "total"],
        "avg_": ["avg", "average"],
        "min_": ["min", "minimum"],
        "max_": ["max", "maximum"],
    }
    for key, item in registry.items():
        label_text = getattr(item, "label", str(item))
        phrases = {key, key.replace("_", " "), label_text.lower(), label_text.lower().replace(" ", "_")}
        phrases |= {p[:-1] + "ies" for p in list(phrases) if p.endswith("y")}
        for prefix, aliases in prefix_map.items():
            if key.startswith(prefix):
                base = key[len(prefix):]
                bases = {base, base.replace("_", " ")}
                for p_alias in aliases:
                    for b in bases:
                        phrases.add(f"{p_alias} {b}")
                        phrases.add(f"{p_alias}_{b}")
                phrases.update(bases)
        pairs += [(phrase, key) for phrase in phrases]
    return sorted(pairs, key=lambda p: -len(p[0]))


def _found(text: str, registry: dict[str, Any]) -> list[str]:
    """Keys whose phrase appears, in the order the question named them."""
    hits: list[tuple[int, str]] = []
    for phrase, key in _needles(registry):
        if any(key == seen for _, seen in hits):
            continue
        if key in _ON_REQUEST and key.replace("_", " ") not in text:
            continue
        pattern = re.compile(r"\b" + re.escape(phrase) + r"(?:s|es)?\b", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            at = match.start()
            hits.append((at, key))
            text = text[:at] + " " * len(match.group(0)) + text[at + len(match.group(0)):]
    return [key for _, key in sorted(hits)]


# A trend word to the grain it asks for. Checked before `by`, because "monthly
# revenue" is a time series and `by: [month]` is a bar chart of twelve bars.
_GRAINS = (("month", ("monthly", "by month", "per month", "month over month",
                      "each month", "over the months")),
           ("quarter", ("quarterly", "by quarter", "per quarter", "each quarter")),
           ("year", ("yearly", "annually", "by year", "per year", "each year",
                     "year over year")),
           ("day", ("daily", "by day", "per day", "each day")))


def read_keywords(question: str, catalog: Any = None) -> dict[str, Any]:
    """A question to a plan's fields, by matching registry keys and labels."""
    from .. import metrics as M
    from . import plan as P

    cat = catalog or M.get_active_catalog()
    metrics_map = cat.metrics if cat else M.METRICS
    dims_map = cat.dimensions if cat else M.DIMENSIONS

    raw_text = (question or "").strip()
    text = " " + raw_text.lower() + " "
    head, sep, tail = text.partition(" by ")
    fields: dict[str, Any] = {}
    metrics = _found(head, metrics_map) or _found(text, metrics_map)
    if metrics:
        fields["metrics"] = metrics

    time_grains = tuple(cat.drill_paths.get("time", P.TIME_GRAINS)) if cat else P.TIME_GRAINS
    grain = next((key for key, words in _GRAINS
                  if any(word in text for word in words)), None)
    if grain and grain in time_grains:
        fields["grain"] = grain
    else:
        dimensions = _found(tail, dims_map) or _found(head, dims_map)
        if dimensions:
            fields["by"] = dimensions

    ordinal = _ORDINALS.search(text)
    if ordinal:
        word = ordinal.group(1) or ordinal.group(2)
        count = int(word) if word.isdigit() else _COUNTS.get(word)
        if count:
            fields["limit"] = count
    if any(word in text for word in _ASCENDING) and metrics:
        fields["order_by"] = metrics[0]

    # Where clause extraction: "where <dim> is/=/in <val>" supporting quotes or multi-word values
    where_match = re.search(
        r"\bwhere\s+([\w\-]+)\s+(?:is|=|in)\s+(?:['\"]([^'\"]+)['\"]|([a-zA-Z0-9_\- ]+?))(?:\s+(?:and|or|by|limit|order)|$)",
        raw_text,
        re.IGNORECASE,
    )
    if where_match:
        dim_cand = where_match.group(1).lower()
        val_cand = (where_match.group(2) or where_match.group(3) or "").strip()
        dim_key = next((k for k in dims_map if k.lower() == dim_cand or dims_map[k].label.lower() == dim_cand), None)
        if dim_key and val_cand:
            fields["where"] = {dim_key: [val_cand]}

    year = _YEAR.search(text)
    if year:
        fields["date_from"] = year.group(1) + "-01-01"
        fields["date_to"] = year.group(1) + "-12-31"
    return fields
