"""Tool-args recovery — defend against malformed model tool-call args.

A local LLM is a probabilistic model: it will sometimes emit tool-call
``arguments`` that aren't usable. There are three distinct ways it goes
wrong, and the harness must (a) never crash the turn on any of them and
(b) hand the model an ACCURATE, actionable reason so it can self-correct
(issue #76 — previously every case was reported as "you combined multiple
tool calls", which is wrong for two of the three):

1. **Malformed JSON** — the ``arguments`` string can't be parsed at all
   (unquoted keys, unterminated string, trailing junk). →
   ``MalformedToolArgsError``.
2. **Non-object root** — valid JSON, but the model sent a list / scalar
   instead of an object. → ``NonObjectToolArgsError``.
3. **Concatenated objects** — LiteLLM / openai-agents-sdk's streaming layer
   sometimes merges two parallel ``tool_call`` deltas into a single
   tool_call whose ``arguments`` is two JSON objects concatenated::

       {"path": "wafer_map.csv"}{"path": "process_parameters.csv"}

   → ``ConcatenatedToolCallsError``. Empirically Qwen3:14B falls into this
   on its first turn — ignoring the "one tool call per response" rule.

This module provides:

- ``peel_first_json(args)`` — extract the first complete JSON value from a
  possibly-concatenated args string. Returns ``(value, leftover)`` where
  ``leftover`` is the un-consumed tail (empty when args was a single clean
  value). Shape-agnostic — the caller decides whether a non-object is OK.

- ``wrap_with_args_recovery(tool)`` — return a FunctionTool that classifies
  the args through ``peel_first_json`` before delegating to the original.
  **For all three failure classes the wrap REFUSES to run the underlying
  tool and RAISES** the matching ``ToolArgsError``. Two reasons it must
  raise (not return an error string):

  * The merged/parallel tool_call's name comes from one of the parallel
    calls, so the FIRST object's shape often belongs to a DIFFERENT tool;
    running it produces a confusing ValidationError from a tool the model
    didn't choose.
  * The SDK has already appended the bad tool_call (with its unparseable
    ``arguments`` string) to its in-flight conversation. If we returned a
    tool result the SDK would yield ToolEnd (setting the runner's
    ``progress_made``, blocking retry) AND send the poisoned conversation
    back to LiteLLM, where ``ollama/chat/transformation.py`` does
    ``json.loads(arguments)`` and dies with ``Extra data`` →
    APIConnectionError. Raising exits ``Runner.run_streamed`` so the
    runner's ``diagnose_error`` maps the exception to a hint and retries
    from the CLEAN persisted history (the poisoned in-flight call is
    discarded).

This wrap is the SOLE defence against the concatenated-args streaming bug.
We used to also force ``ModelSettings(parallel_tool_calls=False)`` as
belt-and-braces, but that flag was the only wire-level difference from the
(reliable) Replay path and made some models emit tool calls as plain text —
and litellm rejects it on providers like ``ollama_chat`` — so it was
dropped (#69). Recovery here still catches a parallel emission if a
provider produces one.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, cast

from agents import FunctionTool
from agents.tool_context import ToolContext

from .arg_repair import malformed_raw
from .context import AgentToolContext

_LOGGER = logging.getLogger(__name__)


class ToolArgsError(ValueError):
    """The model's tool-call arguments couldn't be used.

    Subclasses ``ValueError`` so it still satisfies the SDK / runner's
    broad ``except (json.JSONDecodeError, ValueError)`` / ``except
    Exception`` handling and propagates cleanly out of
    ``Runner.run_streamed`` to the runner's ``diagnose_error``. The
    message is kept clean and user-safe (it can surface in the failure
    banner) — no raw Python json internals. Subclasses tag the *kind* of
    failure so ``diagnose_error`` can route an accurate retry hint.

    Carries the affected ``tool_name``, the model's actual ``raw_args``
    string, and the ``call_id`` as structured attributes (NOT in the
    message) so the runner can surface *what the model emitted* to the FE
    via ``ToolCallParseError`` — the user has a right to see the mistake
    (#76) — while the message itself stays clean."""

    def __init__(
        self, message: str, *, tool_name: str = "", raw_args: str = "", call_id: str = ""
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.raw_args = raw_args
        self.call_id = call_id


class MalformedToolArgsError(ToolArgsError):
    """The ``arguments`` string could not be parsed as JSON at all."""


class NonObjectToolArgsError(ToolArgsError):
    """The args parsed as valid JSON, but the root is not an object — the
    model sent a list / scalar instead of ``{...}``."""


class ConcatenatedToolCallsError(ToolArgsError):
    """The args held more than one JSON object concatenated — the streaming
    aggregator merged parallel tool_calls into one. The model must emit
    exactly ONE tool call per response."""


def peel_first_json(args_json: str) -> tuple[object, str]:
    """Parse the first complete JSON value out of ``args_json``.

    Returns ``(value, leftover)`` — ``leftover`` is the un-consumed tail
    after the first value (empty string when args was a single clean
    value). Shape-agnostic: ``value`` may be any JSON type; the caller
    decides whether a non-object is acceptable (so it can attach the tool
    name to the error it raises). Raises ``json.JSONDecodeError`` only when
    there is no valid JSON at the very start."""
    decoder = json.JSONDecoder()
    stripped = args_json.lstrip()
    leading_ws = len(args_json) - len(stripped)
    value, end = decoder.raw_decode(stripped)
    leftover = args_json[leading_ws + end :].strip()
    return value, leftover


# ─── nullable-arg coercion ────────────────────────────────────────────


def _nullable_non_string_types(schema: dict[str, Any], name: str) -> set[str] | None:
    """The non-null JSON types declared for ``name``, IF the param is nullable and
    none of its types is ``string``; else ``None`` (meaning: hands off).

    Only nullable params qualify, because turning a value into ``null`` has to be
    a legal outcome for that param. And a param that can be a string is excluded
    on purpose — for `document: string | null` the text ``"None"`` is a value the
    model may have meant (a file really can be called that), so second-guessing it
    would trade a loud error for a silent wrong answer."""
    prop = (schema.get("properties") or {}).get(name)
    if not isinstance(prop, dict):
        return None
    variants = prop.get("anyOf") or prop.get("oneOf")
    types: set[str] = set()
    if isinstance(variants, list):
        for v in variants:
            t = v.get("type") if isinstance(v, dict) else None
            if isinstance(t, str):
                types.add(t)
    else:
        t = prop.get("type")
        if isinstance(t, str):
            types.add(t)
        elif isinstance(t, list):
            types.update(x for x in t if isinstance(x, str))
    if "null" not in types or "string" in types:
        return None
    return types - {"null"}


def _impossible(value: object, types: set[str]) -> bool:
    """Whether ``value`` cannot be any of ``types`` under JSON's own rules.

    Deliberately narrow: only a STRING that no reading could parse into the
    declared type counts. A numeric string like ``"30"`` is left alone — pydantic
    already coerces it, and silently nulling a page the user asked for would be
    far worse than the error this guard exists to remove."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if "integer" in types or "number" in types:
        try:
            float(text)
        except ValueError:
            return True
        return False
    if "boolean" in types:
        return text.lower() not in {"true", "false", "1", "0", "yes", "no"}
    return False


def _null_impossible_values(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Read a value that CANNOT be its declared type as "not set" (#kb-search).

    Strict mode marks every property required, so a model calling a tool with
    seven optional params must put something in each one. When what it puts is a
    stand-in for "nothing" — `"None"`, `"null"`, `""` — bouncing it back as a
    validation error tells the model only that it was wrong, not what to do, and
    it burns turns guessing. Since no integer is ever spelled "None", reading it
    as absent is the only interpretation available, and it is the one the model
    meant.

    `arg_repair` already handles the bare Python `None` upstream; this covers the
    quoted spelling, which we cannot observe locally because the model producing
    it runs in production. Being correct for both is what makes the fix shippable
    without first reproducing the exact one."""
    out = dict(args)
    for name, value in args.items():
        types = _nullable_non_string_types(schema, name)
        if types and _impossible(value, types):
            _LOGGER.info(
                "args_recovery: %s=%r cannot be %s; reading it as null",
                name,
                value,
                "/".join(sorted(types)),
            )
            out[name] = None
    return out


# The agents-SDK catches a pydantic ValidationError inside its tool invoker and
# returns this shape as a tool RESULT (see `default_tool_error_function`). That
# is why an argument the model got wrong never reaches our runner's error path,
# never becomes a `ToolCallParseError`, and never lands in an operator's log —
# the model just retries in-band and the turn looks merely slow.
_SDK_VALIDATION_ERROR = "Invalid JSON input for tool"


def _log_if_schema_rejected(tool_name: str, args: dict[str, Any], result: str) -> None:
    """Record a schema rejection the SDK swallowed, with the args that caused it.

    We cannot intercept the exception — it is caught before control returns to us —
    but the result carries the SDK's own signature, so this recognises it and logs
    what the model actually sent. Without this the only symptom is "the first call
    is always slow", with no way to tell WHICH argument or WHICH spelling was at
    fault, which is exactly how this defect survived unnoticed in production.

    The result itself is returned to the model unchanged: the feedback it needs to
    self-correct is not ours to edit."""
    if _SDK_VALIDATION_ERROR not in result:
        return
    _LOGGER.warning(
        "args_recovery: %s rejected the model's args by schema; args=%s reason=%s",
        tool_name,
        json.dumps(args, ensure_ascii=False)[:500],
        result.split(_SDK_VALIDATION_ERROR, 1)[1][:500],
    )


def wrap_with_args_recovery(tool: FunctionTool) -> FunctionTool:
    """Return a FunctionTool that classifies bad model args before running.

    Four input cases:

    - Clean single-object args → pass through to the original tool.
    - Unparseable args → raise ``MalformedToolArgsError``.
    - Valid JSON but a non-object root → raise ``NonObjectToolArgsError``.
    - Concatenated objects (leftover present) → raise
      ``ConcatenatedToolCallsError``.

    See the module docstring for why the failure cases MUST raise rather
    than return a tool-result string.
    """
    original = tool.on_invoke_tool
    schema = tool.params_json_schema or {}

    # Annotate as `ToolContext` (NOT the narrower `RunContextWrapper`):
    # agents-SDK introspects the annotation on `on_invoke_tool` to decide
    # whether to downgrade the context before invoking. Declaring
    # `RunContextWrapper` triggers `context._fork_with_tool_input(...)`,
    # stripping `run_config` etc. — which then explodes when we forward
    # `ctx` to the wrapped `_FailureHandlingFunctionToolInvoker` (which
    # IS `function_tool(...)`-decorated tools' on_invoke_tool, and
    # depends on the full ToolContext). See agents/tool.py
    # `_get_function_tool_invoke_context`.
    #
    # Concretely typed `ToolContext[AgentToolContext]` instead of
    # `ToolContext[Any]` — every tool wired through this app's runner
    # carries an AgentToolContext as its run-context state, so the wrap
    # only needs to support that one shape. `Any` would silently bypass
    # ty for callers; the concrete type catches mismatches.
    async def safer(ctx: ToolContext[AgentToolContext], args_json: str) -> str:
        # The model's actual emission + call id travel on the raised error so
        # the runner can show the user WHAT went wrong (#76), without putting
        # raw payloads into the user-safe message.
        call_id = ctx.tool_call_id
        try:
            value, leftover = peel_first_json(args_json)
        except json.JSONDecodeError as e:
            # Keep the raw parse error out of the message (it can reach the
            # user banner) but log it for the operator.
            _LOGGER.info("args_recovery: unparseable args on %s: %s", tool.name, e)
            raise MalformedToolArgsError(
                f"The arguments for tool `{tool.name}` were not valid JSON.",
                tool_name=tool.name,
                raw_args=args_json,
                call_id=call_id,
            ) from e
        if not isinstance(value, dict):
            _LOGGER.info(
                "args_recovery: non-object args on %s: %s", tool.name, type(value).__name__
            )
            raise NonObjectToolArgsError(
                f"The arguments for tool `{tool.name}` must be a single JSON object, "
                f"not a {type(value).__name__}.",
                tool_name=tool.name,
                raw_args=args_json,
                call_id=call_id,
            )
        # Backstop sentinel (set at the model-output boundary when the model's
        # args couldn't be parsed/repaired): RETURN a clean in-band error rather
        # than raise. The conversation already holds the valid sentinel, so this
        # neither poisons the next request nor aborts the turn — the model sees
        # the error as a normal tool result and retries in-band. #76.
        raw = malformed_raw(value)
        if raw is not None:
            _LOGGER.info("args_recovery: in-band malformed-args error on %s: %r", tool.name, raw)
            return (
                f"Error: the arguments you sent for `{tool.name}` were not valid JSON "
                f"({raw!r}). Re-send the call as exactly ONE complete, valid JSON object — "
                f"quote every key and string value, and write nothing after the closing brace."
            )
        if leftover:
            # The operator-facing detail (what got merged) goes to the log,
            # NOT the exception message — the message can surface in the
            # user banner, so it stays clean.
            _LOGGER.info(
                "args_recovery: merged parallel tool_calls on %s; first=%s leftover=%r",
                tool.name,
                json.dumps(value),
                leftover,
            )
            raise ConcatenatedToolCallsError(
                f"Tool `{tool.name}` received more than one tool call merged into one "
                "response. Send exactly ONE tool call per response.",
                tool_name=tool.name,
                raw_args=args_json,
                call_id=call_id,
            )
        # `peel_first_json` returns `object`; the non-dict case raised above, so
        # this cast records what the control flow already guarantees.
        args = cast("dict[str, Any]", value)
        coerced = _null_impossible_values(args, schema)
        result = await original(ctx, json.dumps(coerced))
        _log_if_schema_rejected(tool.name, coerced, result)
        return result

    # `FunctionTool` is a dataclass with ~20 fields (including private
    # SDK ones like `_failure_error_function`, `_use_default_failure_error_function`,
    # `_tool_origin`, ...). Reconstructing with only the 5 public fields
    # silently dropped those — broke the model's tool emission. Use
    # `dataclasses.replace` so every other field is preserved verbatim.
    return dataclasses.replace(tool, on_invoke_tool=safer)
