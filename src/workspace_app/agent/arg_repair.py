"""Best-effort repair of malformed tool-call arguments (#76).

A small local model sometimes emits tool-call ``arguments`` that aren't valid
JSON — a value missing its quote (``{"path": ./hello.md"}``), an unquoted key,
a dropped closing brace. Rather than give up the turn, we try to recover the
model's intended object so the tool can still run.

Self-repair is intent-guessing by nature, so it's the toggleable layer: comment
the marked ``repair_tool_args(...)`` line in ``repairing_model._safe_args`` to
disable it (no config / env knob — just the one line). The BACKSTOP below stays
on regardless.

``repair_tool_args`` returns ``None`` for anything it can't coerce into a single
JSON object (concatenated objects / arrays / pure garbage); the always-on
backstop then replaces those with a ``make_backstop_sentinel`` value so the args
handed downstream are ALWAYS valid JSON — the SDK and LiteLLM both strict-parse
tool-call args, so anything non-JSON would crash the turn / poison the next
request. The tool wrap recognises the sentinel and returns a clean in-band
error instead of aborting.
"""

from __future__ import annotations

import json
import logging

from json_repair import repair_json

logger = logging.getLogger(__name__)


def repair_tool_args(raw: str) -> str | None:
    """Try to coerce ``raw`` into a canonical JSON-object string.

    Returns the repaired JSON string when ``raw`` can be recovered as a JSON
    object, else ``None`` — in which case the caller keeps the raw args and the
    normal malformed-args handling takes over."""
    try:
        obj = repair_json(raw, return_objects=True)
    except Exception:  # noqa: BLE001 — repair must never raise into the caller
        logger.warning(
            "arg_repair: json_repair raised on %r; giving up self-repair", raw, exc_info=True
        )
        return None
    if isinstance(obj, dict):
        return json.dumps(obj)
    logger.debug("arg_repair: json_repair produced non-object for %r; giving up self-repair", raw)
    return None


# ─── backstop sentinel ────────────────────────────────────────────────
# When args can't be repaired (or repair is disabled), the model-output
# boundary replaces them with this sentinel — a VALID JSON object — so nothing
# downstream chokes (the SDK does `json.loads(tool_call.arguments)` before it
# even invokes the tool, and LiteLLM's Ollama transform re-parses historical
# tool_calls on the next turn; both would crash on raw malformed JSON). The
# tool wrap recognises the sentinel and returns a clean in-band error carrying
# the original raw, instead of raising/aborting the turn. #76.
MALFORMED_ARGS_KEY = "__malformed_tool_args__"


def make_backstop_sentinel(raw: str) -> str:
    """A valid JSON-object string encoding unrepairable ``raw`` tool args."""
    return json.dumps({MALFORMED_ARGS_KEY: raw})


def malformed_raw(parsed: dict) -> str | None:
    """If ``parsed`` is a backstop sentinel, return the original raw args string;
    otherwise ``None`` (it's normal tool args)."""
    if set(parsed) == {MALFORMED_ARGS_KEY}:
        raw = parsed[MALFORMED_ARGS_KEY]
        if isinstance(raw, str):
            return raw
    return None
