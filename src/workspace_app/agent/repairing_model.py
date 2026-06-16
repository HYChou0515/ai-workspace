"""RepairingModel — repair malformed tool-call JSON at the model-output boundary.

A small local model sometimes emits tool-call ``arguments`` that aren't valid
JSON (a value missing its quote, an unquoted key, a dropped brace). If we only
fixed that at tool-invocation time, the model's RAW string would still sit in
the SDK's conversation and poison the NEXT LiteLLM request (its Ollama transform
does a strict ``json.loads`` on the historical tool_call and dies). So we repair
HERE — wrapping the underlying Model so the repaired args replace the raw ones
*before the SDK records them*. Then the tool runs on the intended args AND the
recorded conversation stays valid. #76.

Wired in via a SINGLE line in ``litellm_runner._agent_for`` that can be
commented out to fall back to "diagnose only, no self-repair" — no config / env
knob, just the one line.

Only single JSON *objects* are repaired; concatenated objects / arrays / pure
garbage are left untouched so the existing ``args_recovery`` classification
still owns them (e.g. concatenated → "one tool call per turn").
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from agents import Model

from .arg_repair import make_backstop_sentinel, repair_tool_args

_LOGGER = logging.getLogger(__name__)


def _safe_args(raw: str) -> str | None:
    """A valid-JSON replacement for ``raw`` when it isn't already valid JSON,
    else ``None`` (leave it as-is).

    Two layers: try to recover the model's INTENT (self-repair, the toggleable
    line), then ALWAYS fall back to a backstop sentinel so the args handed
    downstream are valid JSON no matter what the model emitted — the SDK and
    LiteLLM both strict-parse tool-call args, so anything non-JSON crashes the
    turn / poisons the next request unless we sanitize here."""
    try:
        json.loads(raw)
        return None  # already valid JSON — nothing to do
    except (json.JSONDecodeError, ValueError):
        pass
    # --- self-repair (probability reduction) ------------------------------
    repaired = None
    repaired = repair_tool_args(raw)  # ← #76 self-repair: comment THIS line to disable repair
    if repaired is not None:
        return repaired
    # --- backstop (ALWAYS ON) ---------------------------------------------
    # Repair is off or couldn't recover the intent. Hand downstream a valid
    # JSON sentinel so nothing crashes/poisons; the tool wrap (args_recovery)
    # turns it into a clean in-band error so the turn continues and the model
    # retries — instead of aborting / giving up.
    return make_backstop_sentinel(raw)


def _sanitize_item(item: Any) -> None:
    """If ``item`` is a function_call with non-valid-JSON args, replace its
    ``arguments`` IN PLACE with a repaired/sentinel valid-JSON string (the
    openai item models are mutable)."""
    if getattr(item, "type", None) != "function_call":
        return
    raw = item.arguments
    fixed = _safe_args(raw)
    if fixed is None:
        return
    item.arguments = fixed
    _LOGGER.info(
        "repairing_model: sanitized tool args for %s: %r -> %r",
        getattr(item, "name", "?"),
        raw,
        fixed,
    )


def _repair_event(event: Any) -> Any:
    """Sanitize tool-call args carried by a stream event, in place. Returns the
    same event so callers can ``yield _repair_event(chunk)``. Handles both the
    per-item ``response.output_item.done`` and the final ``response.completed``
    (whichever the SDK builds its run items from — items are sanitized either
    way)."""
    kind = getattr(event, "type", None)
    if kind == "response.output_item.done":
        _sanitize_item(getattr(event, "item", None))
    elif kind == "response.completed":
        response = getattr(event, "response", None)
        for item in getattr(response, "output", None) or []:
            _sanitize_item(item)
    return event


class RepairingModel(Model):
    """Wrap a Model so malformed tool-call JSON is repaired before the SDK sees
    it. Delegates everything to ``inner``; only tool-call ``arguments`` on the
    streamed output items are touched."""

    def __init__(self, inner: Model) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        # Transparent passthrough for everything we don't override (e.g. the
        # `.model` id the #69 trace reads, base_url, …). Only fires for missing
        # attributes, so `_inner` / get_response / stream_response are unaffected.
        return getattr(self._inner, name)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        # We always stream (run_streamed), so the repair lives on stream_response.
        # get_response is pure delegation — non-streaming isn't a path we use.
        return await self._inner.get_response(*args, **kwargs)

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async for chunk in self._inner.stream_response(*args, **kwargs):
            yield _repair_event(chunk)
