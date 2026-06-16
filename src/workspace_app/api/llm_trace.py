"""LLM request/response trace (#69 observability).

When a turn's model emits a tool call as plain text instead of using the
structured tool-call channel, the chat just shows the text and there's no
way to see WHAT we sent or WHAT shape came back. This module turns the
runner's in-hand request + observed stream into one compact, secret-free
log line (gated by ``WORKSPACE_LLM_TRACE``) so an operator can compare a
live turn against a Replay and spot a config-induced difference.

Pure helpers here; the runner wires them in. Nothing logs a base URL with
credentials or a key — only ``host:port``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

TRACE_ENV = "WORKSPACE_LLM_TRACE"

# A brace pair holding a `key: value` — i.e. a JSON-object-shaped blob.
_JSON_OBJECT = re.compile(r"\{[^{}]*:.*?\}", re.DOTALL)


def trace_enabled() -> bool:
    """LLM tracing is opt-in: off unless ``WORKSPACE_LLM_TRACE`` is truthy
    (`1`/`true`/`yes`/`on`), so production logs stay quiet by default."""
    return os.environ.get(TRACE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def redact_endpoint(base_url: str | None) -> str:
    """``host:port`` of an LLM endpoint — scheme, path and any embedded
    credentials dropped. Empty/None → ``"default"`` (inherits provider /
    runner default)."""
    if not base_url:
        return "default"
    parts = urlsplit(base_url if "//" in base_url else f"//{base_url}")
    host = parts.hostname or ""
    if parts.port is not None:
        return f"{host}:{parts.port}"
    return host or "default"


def text_looks_like_tool_call(text: str, tool_names: list[str]) -> bool:
    """Heuristic for the #69 failure mode: the reply NAMES one of the turn's
    tools AND carries a JSON-object-shaped blob — the model describing a call
    in prose instead of emitting it through the tool-call channel. Plain
    prose, or JSON unrelated to any tool, is not flagged."""
    if not text or not tool_names:
        return False
    if not any(name in text for name in tool_names):
        return False
    return _JSON_OBJECT.search(text) is not None


def classify_outcome(*, tool_calls: int, content_chars: int, looks_like_tool_call: bool) -> str:
    """Name what the model did this turn:

    - ``tool_call``                — at least one tool actually fired.
    - ``empty``                    — nothing user-visible.
    - ``text-looks-like-tool-call``— no tool fired, but the text is a
      tool-call-shaped blob (the #69 smoking gun).
    - ``text``                     — a plain prose answer.
    """
    if tool_calls >= 1:
        return "tool_call"
    if content_chars == 0:
        return "empty"
    if looks_like_tool_call:
        return "text-looks-like-tool-call"
    return "text"


@dataclass(frozen=True)
class LlmTurnTrace:
    """One turn's request shape + the response shape it produced."""

    model: str
    endpoint: str
    tools: list[str]
    parallel_tool_calls: str  # "unset" / "true" / "false"
    tool_choice: str  # "auto (unset)" / explicit choice
    reasoning_effort: str  # "" when unset
    outcome: str  # see classify_outcome


def _norm_parallel(value: bool | None) -> str:
    if value is None:
        return "unset"
    return "true" if value else "false"


def build_trace(
    *,
    model: str,
    endpoint: str,
    tools: list[str],
    parallel_tool_calls: bool | None,
    tool_choice: str | None,
    reasoning_effort: str | None,
    tool_calls: int,
    content_text: str,
) -> LlmTurnTrace:
    """Assemble a turn's trace from the raw request pieces + observed stream.
    Normalises unset knobs to readable tokens and classifies the outcome."""
    outcome = classify_outcome(
        tool_calls=tool_calls,
        content_chars=len(content_text),
        looks_like_tool_call=text_looks_like_tool_call(content_text, tools),
    )
    return LlmTurnTrace(
        model=model,
        endpoint=endpoint,
        tools=list(tools),
        parallel_tool_calls=_norm_parallel(parallel_tool_calls),
        tool_choice=tool_choice or "auto (unset)",
        reasoning_effort=reasoning_effort or "",
        outcome=outcome,
    )


def format_trace_line(trace: LlmTurnTrace) -> str:
    """One grep-friendly line carrying every field an operator compares
    against a Replay."""
    return (
        f"LLM turn: model={trace.model} endpoint={trace.endpoint} "
        f"tools=[{','.join(trace.tools)}] "
        f"tool_choice={trace.tool_choice} "
        f"parallel_tool_calls={trace.parallel_tool_calls} "
        f"reasoning={trace.reasoning_effort or '-'} "
        f"outcome={trace.outcome}"
    )
