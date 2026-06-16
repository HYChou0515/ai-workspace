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
        return await original(ctx, json.dumps(value))

    # `FunctionTool` is a dataclass with ~20 fields (including private
    # SDK ones like `_failure_error_function`, `_use_default_failure_error_function`,
    # `_tool_origin`, ...). Reconstructing with only the 5 public fields
    # silently dropped those — broke the model's tool emission. Use
    # `dataclasses.replace` so every other field is preserved verbatim.
    return dataclasses.replace(tool, on_invoke_tool=safer)
