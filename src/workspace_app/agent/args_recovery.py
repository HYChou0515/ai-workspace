"""Tool-args recovery — defend against malformed model tool-call args.

A local LLM is a probabilistic model: it will sometimes emit tool-call
``arguments`` that aren't usable. There are three distinct ways it goes
wrong, and the harness must (a) never crash the turn on any of them and
(b) hand the model an ACCURATE, actionable reason so it can self-correct
(issue #76 — previously every case was reported as "you combined multiple
tool calls", which is wrong for two of the three):

1. **Malformed JSON** — the ``arguments`` string can't be parsed at all
   (unquoted keys, unterminated string, trailing junk).
2. **Non-object root** — valid JSON, but the model sent a list / scalar
   instead of an object.
3. **Concatenated objects** — LiteLLM / openai-agents-sdk's streaming layer
   sometimes merges two parallel ``tool_call`` deltas into a single
   tool_call whose ``arguments`` is two JSON objects concatenated::

       {"path": "wafer_map.csv"}{"path": "process_parameters.csv"}

   Empirically Qwen3:14B falls into this
   on its first turn — ignoring the "one tool call per response" rule.

This module provides:

- ``peel_first_json(args)`` — extract the first complete JSON value from a
  possibly-concatenated args string. Returns ``(value, leftover)`` where
  ``leftover`` is the un-consumed tail (empty when args was a single clean
  value). Shape-agnostic — the caller decides whether a non-object is OK.

- ``wrap_with_args_recovery(tool)`` — return a FunctionTool that classifies
  the args through ``peel_first_json`` before delegating to the original.
  **For all three failure classes the wrap REFUSES to run the underlying
  tool and answers IN-BAND** with the reason, so the model corrects itself
  on its next call (docs/plan-friendly-errors.md D2). It used to raise, to
  end the run so the whole turn could restart from a clean conversation —
  the bad ``arguments`` would otherwise reach LiteLLM's Ollama transform,
  which ``json.loads`` the history and dies. That is now settled where the
  args are made: ``RepairingModel`` hands the SDK valid JSON on both the
  streamed and the whole-response path, so there is nothing to discard and
  no turn to restart. The first object of a merged call is still never run:
  its shape often belongs to a DIFFERENT tool.

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

from .arg_repair import AMBIGUOUS_CALL_KEY, malformed_raw
from .context import AgentToolContext

_LOGGER = logging.getLogger(__name__)


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


_SDK_VALIDATION_ERROR = "Invalid JSON input for tool"

# The SDK's `default_tool_error_function` always opens with this. Requiring it —
# rather than matching the phrase above anywhere — is what keeps a tool whose
# OUTPUT happens to quote the error (a `read_file` of a log about this very
# defect) from raising a false operator warning.
_SDK_TOOL_ERROR_PREFIX = "An error occurred while running the tool."


def _arg_shapes(args: dict[str, Any]) -> str:
    """``name=type`` per argument — enough to identify which one the model got
    wrong and in what spelling, with none of its content.

    The values themselves are workspace data: a ``write_file`` body, an ``exec``
    command line, a KB query. Every other log line in this module keeps raw
    payloads out (see the comments on the parse-error paths), and an operator
    diagnosing a schema rejection needs the SHAPE, not the secret. Strings short
    enough to be a stand-in for "nothing" are shown verbatim, because that is
    precisely the spelling we are trying to learn."""
    parts: list[str] = []
    for name, value in args.items():
        if isinstance(value, str) and len(value) <= 8:
            parts.append(f"{name}={value!r}")
        else:
            parts.append(f"{name}=<{type(value).__name__}>")
    return ", ".join(parts)


def _log_if_schema_rejected(tool_name: str, args: dict[str, Any], result: Any) -> None:
    """Record a schema rejection the SDK swallowed, naming the offending argument.

    We cannot intercept the exception — it is caught before control returns to us —
    but the result carries the SDK's own signature, so this recognises it and logs
    which arguments were sent and in what shape. Without this the only symptom is
    "the first call is always slow", with no way to tell WHICH argument or WHICH
    spelling was at fault, which is how this defect survived unnoticed in
    production.

    A tool's output is NOT necessarily text — the SDK types this seam as
    ``Awaitable[Any]`` and documents structured returns, which is how ``read_image``
    (#517) hands a VLM main model the pixels as a ``ToolOutputImage``. A schema
    rejection can only ever arrive as the SDK's plain error STRING, so anything
    else is simply not the thing being looked for. Reading it as text anyway raised
    inside the invoke, and since this wrap is applied to every tool in the runner
    the model got "error running tool read_image: ... has no attribute
    'startswith'" instead of the image, on every call (#584).

    The result itself is returned to the model unchanged: the feedback it needs to
    self-correct is not ours to edit."""
    if not isinstance(result, str):
        return
    if not result.startswith(_SDK_TOOL_ERROR_PREFIX) or _SDK_VALIDATION_ERROR not in result:
        return
    _LOGGER.warning(
        "args_recovery: %s rejected the model's args by schema; args=(%s) reason=%s",
        tool_name,
        _arg_shapes(args),
        result.split(_SDK_VALIDATION_ERROR, 1)[1][:300],
    )


def wrap_with_args_recovery(tool: FunctionTool) -> FunctionTool:
    """Return a FunctionTool that classifies bad model args before running.

    Four input cases:

    - Clean single-object args → pass through to the original tool.
    - Unparseable args, a non-object root, or concatenated objects (leftover
      present) → the tool is NOT run; the model is told why, in-band.

    See the module docstring for why in-band is safe.
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
    #
    # The RETURN, by contrast, is `Any` — exactly as the SDK types the seam
    # (`on_invoke_tool: Callable[..., Awaitable[Any]]`), because a tool may answer
    # with a structured output (`ToolOutputImage`/`ToolOutputText`/
    # `ToolOutputFileContent`, or a list of them) and this wrap only classifies the
    # ARGUMENTS. Narrowing it to `str` here was a claim about other people's tools
    # that was not ours to make, and it is what turned `read_image` into an error.
    async def safer(ctx: ToolContext[AgentToolContext], args_json: str) -> Any:
        try:
            value, leftover = peel_first_json(args_json)
        except json.JSONDecodeError as e:
            # The parser's internals go to the log, not the reply: "line 1
            # column 1" tells the model nothing it can act on.
            _LOGGER.info("args_recovery: unparseable args on %s: %s", tool.name, e)
            return (
                f"Error: the arguments you sent for `{tool.name}` were not valid JSON. "
                "Re-send the call as exactly ONE complete, valid JSON object — quote every "
                "key and string value, and write nothing after the closing brace."
            )
        if not isinstance(value, dict):
            _LOGGER.info(
                "args_recovery: non-object args on %s: %s", tool.name, type(value).__name__
            )
            return (
                f"Error: the arguments for `{tool.name}` must be a single JSON object "
                f'(like {{"path": "file.csv"}}), not a {type(value).__name__}. Re-send one '
                "JSON object matching the tool's schema."
            )
        # An old name two third-party commands answer to (`ToolAliasModel`):
        # that call fails in-band and names the commands to call instead.
        if (reply := ambiguous_call_reply(value)) is not None:
            return reply
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
            # What got merged goes to the log, not the reply: the payload may
            # belong to a different tool, and repeating it invites a re-run.
            _LOGGER.info(
                "args_recovery: merged parallel tool_calls on %s; first=%s leftover=%r",
                tool.name,
                json.dumps(value),
                leftover,
            )
            return (
                f"Error: `{tool.name}` received more than one tool call merged into one "
                "response, so none of them ran. Send exactly ONE tool call per response "
                "and wait for its result before issuing the next one."
            )
        # `peel_first_json` returns `object`; the non-dict case returned above, so
        # this cast records what the control flow already guarantees.
        args = cast("dict[str, Any]", value)
        result = await original(ctx, json.dumps(args))
        _log_if_schema_rejected(tool.name, args, result)
        return result

    # `FunctionTool` is a dataclass with ~20 fields (including private
    # SDK ones like `_failure_error_function`, `_use_default_failure_error_function`,
    # `_tool_origin`, ...). Reconstructing with only the 5 public fields
    # silently dropped those — broke the model's tool emission. Use
    # `dataclasses.replace` so every other field is preserved verbatim.
    return dataclasses.replace(tool, on_invoke_tool=safer)


def ambiguous_call_reply(args: dict) -> str | None:
    """The reply to a call `ToolAliasModel` found ambiguous — today's collision
    message, then the names to call instead — or ``None`` for ordinary args."""
    if set(args) != {AMBIGUOUS_CALL_KEY}:
        return None
    detail = args[AMBIGUOUS_CALL_KEY]
    if not isinstance(detail, dict):
        return None
    called = detail.get("called")
    listed = detail.get("candidates")
    candidates = [
        (str(c[0]), str(c[1]))
        for c in (listed if isinstance(listed, list) else [])
        if isinstance(c, list) and len(c) == 2
    ]
    if not isinstance(called, str) or not candidates:
        return None
    from ..tooling.registry import ambiguous_name_message

    return ambiguous_name_message(called, candidates)
