"""args_recovery — peel concatenated tool_call args + warn the model
so it self-corrects instead of just dying on `Extra data`."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from agents import FunctionTool
from agents.tool_context import ToolContext

from workspace_app.agent.args_recovery import (
    ConcatenatedToolCallsError,
    MalformedToolArgsError,
    NonObjectToolArgsError,
    peel_first_json,
    wrap_with_args_recovery,
)

# ─── test helpers ──────────────────────────────────────────────────────


def _tool_ctx() -> ToolContext[Any]:
    """Build a real ToolContext for invoking wrapped tools in tests.

    `RunContextWrapper(None)` was the old shape but it doesn't carry
    `tool_name` / `tool_call_id`, and the SDK's downgrade path treats
    it differently from `ToolContext` (which is what production passes).
    Test through the real type so the path covered matches prod."""
    from agents.usage import Usage

    return ToolContext(
        context=None,
        tool_name="t",
        tool_call_id="id",
        tool_arguments="{}",
        usage=Usage(),
    )


async def _noop_invoke(_ctx: ToolContext[Any], _args: str) -> str:
    """Typed stub on_invoke for FunctionTools whose invoke we don't care
    about — used by metadata-preservation tests."""
    return ""


# ─── peel_first_json (pure helper) ────────────────────────────────────


def test_peel_returns_clean_object_with_empty_leftover():
    obj, leftover = peel_first_json('{"path": "x.md"}')
    assert obj == {"path": "x.md"}
    assert leftover == ""


def test_peel_handles_leading_whitespace():
    obj, leftover = peel_first_json('  \n  {"a": 1}  ')
    assert obj == {"a": 1}
    assert leftover == ""


def test_peel_extracts_first_of_two_concatenated_objects():
    """The streaming-bug case: agents SDK merged two parallel tool_calls
    into one args string. We peel off the first."""
    args = '{"path": "wafer_map.csv"}{"path": "process_parameters.csv"}'
    obj, leftover = peel_first_json(args)
    assert obj == {"path": "wafer_map.csv"}
    assert leftover == '{"path": "process_parameters.csv"}'


def test_peel_extracts_first_of_three_concatenated_objects():
    """N>2 also works — we just keep the first."""
    args = '{"a": 1}{"b": 2}{"c": 3}'
    obj, leftover = peel_first_json(args)
    assert obj == {"a": 1}
    # leftover keeps the rest as-is (not re-parsed; the model gets a hint
    # but we don't try to recursively pull the rest out).
    assert leftover == '{"b": 2}{"c": 3}'


def test_peel_is_shape_agnostic_returns_non_object_values():
    """#76: peel no longer enforces object shape — it returns whatever JSON
    value it decoded so the *caller* (which knows the tool name) can raise a
    precise NonObjectToolArgsError. A bare array round-trips with no leftover."""
    value, leftover = peel_first_json("[1, 2, 3]")
    assert value == [1, 2, 3]
    assert leftover == ""


def test_peel_raises_on_unparseable_input():
    with pytest.raises(json.JSONDecodeError):
        peel_first_json("not even json")


# ─── wrap_with_args_recovery (FunctionTool wrapper) ────────────────────


def _echo_tool() -> FunctionTool:
    """A tool that echoes the args it got back as a string — lets the
    tests verify what the wrapper actually passed through."""

    async def echo(_ctx: ToolContext[Any], args_json: str) -> str:
        return f"echo: {args_json}"

    return FunctionTool(
        name="echo",
        description="echoes args",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=echo,
        strict_json_schema=False,
    )


async def test_wrap_passes_clean_args_through_unchanged():
    wrapped = wrap_with_args_recovery(_echo_tool())
    out = await wrapped.on_invoke_tool(_tool_ctx(), '{"x": 1}')
    assert out == 'echo: {"x": 1}'


async def test_wrap_raises_when_leftover_present_so_run_can_restart():
    """Cascade-defence: when args has multiple concatenated JSON objects,
    the wrap MUST raise (not return). Returning an error string lets
    the SDK treat it as a normal tool result — yields ToolEnd, sets
    progress_made=True in the runner, blocks retry, AND leaves the
    bad tool_call (with concat `arguments`) sitting in the SDK's
    internal conversation. The very next iteration sends that
    conversation back to LiteLLM, and LiteLLM's
    `ollama/chat/transformation.py` does `json.loads(arguments)` →
    `Extra data: line 1 column N` → APIConnectionError, run dies.

    Raising instead: no ToolEnd, no progress_made, the SDK's
    Runner.run_streamed exits with the exception, our runner catches
    it via `diagnose_error` (matches 'extra data') → hint to emit
    one tool_call at a time → fresh retry with a clean convo."""
    invoked: list[str] = []

    async def trace(_ctx: ToolContext[Any], args_json: str) -> str:
        invoked.append(args_json)
        return "ran"

    base = FunctionTool(
        name="plot",
        description="d",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=trace,
        strict_json_schema=False,
    )
    wrapped = wrap_with_args_recovery(base)
    # Mimic the production fail: model meant write_file + plot in
    # parallel; the aggregator merged into one tool_call with
    # cross-tool args.
    args = '{"path": "x.csv", "content": "hello"}{"csv": "x.csv"}'
    with pytest.raises(ConcatenatedToolCallsError) as exc:
        await wrapped.on_invoke_tool(_tool_ctx(), args)
    assert invoked == []  # tool stayed uninvoked — no spurious exec
    # The message names the affected tool + tells the model the fix, but
    # keeps the merged raw data out (that goes to the log) so it's safe to
    # surface in the user-facing failure banner.
    msg = str(exc.value)
    assert "plot" in msg
    assert "ONE tool" in msg
    assert "csv" not in msg  # the merged leftover payload must not leak


async def test_wrap_raises_malformed_error_on_unparseable_json():
    """#76: when the model's args can't parse as JSON at all, the wrap
    raises the dedicated MalformedToolArgsError (a ValueError subclass) so
    diagnose_error can hand back an accurate "your JSON was invalid" hint
    — NOT the misleading "you combined multiple tool calls" hint. The
    message is clean (names the tool, no Python json internals) because it
    can surface in the user-facing failure banner."""
    wrapped = wrap_with_args_recovery(_echo_tool())
    with pytest.raises(MalformedToolArgsError) as exc:
        await wrapped.on_invoke_tool(_tool_ctx(), "not json")
    msg = str(exc.value)
    assert "echo" in msg
    # the raw "Expecting value: line 1 column 1" json-internals must NOT leak
    assert "line 1" not in msg.lower()
    assert "expecting" not in msg.lower()


async def test_wrap_returns_in_band_error_for_backstop_sentinel():
    """#76 backstop: when the model-output boundary couldn't parse/repair args
    it hands the tool a valid-JSON sentinel. The wrap RETURNS a clean in-band
    error (NOT raise) carrying the raw, so the turn continues and the model can
    retry — and the underlying tool is never run on the sentinel."""
    from workspace_app.agent.arg_repair import make_backstop_sentinel

    invoked: list[str] = []

    async def trace(_ctx: ToolContext[Any], args_json: str) -> str:
        invoked.append(args_json)
        return "ran"

    base = FunctionTool(
        name="write_file",
        description="d",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=trace,
        strict_json_schema=False,
    )
    wrapped = wrap_with_args_recovery(base)
    out = await wrapped.on_invoke_tool(_tool_ctx(), make_backstop_sentinel('{"path": ./x"}'))
    assert invoked == []  # the real tool was NOT run on the sentinel
    assert "not valid JSON" in out
    assert '{"path": ./x"}' in out  # the model's raw emission surfaced in-band


async def test_wrap_raises_non_object_error_on_array_root():
    """#76: valid JSON whose root is a list/scalar (not an object) is a
    distinct failure from malformed JSON — the model sent the wrong shape,
    not invalid syntax. The wrap raises NonObjectToolArgsError naming the
    received type so diagnose_error can say 'send a single JSON object'."""
    wrapped = wrap_with_args_recovery(_echo_tool())
    with pytest.raises(NonObjectToolArgsError) as exc:
        await wrapped.on_invoke_tool(_tool_ctx(), "[1, 2, 3]")
    msg = str(exc.value)
    assert "echo" in msg
    assert "list" in msg  # names the wrong type the model sent


async def test_wrap_unparseable_error_is_a_value_error_for_sdk_propagation():
    """All three ToolArgsError kinds subclass ValueError, so they still
    satisfy the SDK / runner's broad `except (json.JSONDecodeError,
    ValueError)` and propagate cleanly out of Runner.run_streamed to
    diagnose_error (same cascade reasoning as the concat case: the bad
    tool_call is already in the SDK convo, so raising — not returning — is
    what discards the poison on retry)."""
    wrapped = wrap_with_args_recovery(_echo_tool())
    with pytest.raises(ValueError):
        await wrapped.on_invoke_tool(_tool_ctx(), "not json")


async def test_wrap_does_not_call_underlying_tool_on_parse_failure():
    """Unparseable args ⇒ tool stays uninvoked even though we raise —
    avoids calling the tool with garbage that the SDK would otherwise
    surface as a tool error attributable to the tool itself."""
    invoked: list[str] = []

    async def trace(_ctx: ToolContext[Any], args_json: str) -> str:
        invoked.append(args_json)
        return "ran"

    base = FunctionTool(
        name="trace",
        description="d",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=trace,
        strict_json_schema=False,
    )
    wrapped = wrap_with_args_recovery(base)
    with pytest.raises(ValueError):
        await wrapped.on_invoke_tool(_tool_ctx(), "junk")
    assert invoked == []


def test_wrap_preserves_name_description_and_schema():
    """The wrap is transparent: every metadata field the LLM / SDK reads
    survives — only the on_invoke is replaced."""
    base = FunctionTool(
        name="my-tool",
        description="my description",
        params_json_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        on_invoke_tool=_noop_invoke,
        strict_json_schema=False,
    )
    wrapped = wrap_with_args_recovery(base)
    assert wrapped.name == base.name
    assert wrapped.description == base.description
    assert wrapped.params_json_schema == base.params_json_schema
    assert wrapped.strict_json_schema == base.strict_json_schema


def test_wrap_safer_is_annotated_with_tool_context_not_run_context_wrapper():
    """Regression: agents-SDK introspects the type annotation on
    `on_invoke_tool`'s first parameter to decide whether to *downgrade*
    the ToolContext before invoking (see agents/tool.py
    `_get_function_tool_invoke_context`). If we annotate safer's ctx as
    `RunContextWrapper`, the SDK strips `run_config` etc. via
    `context._fork_with_tool_input(...)` — and the call then explodes
    inside the wrapped `_FailureHandlingFunctionToolInvoker` which
    needs `run_config`. The annotation MUST be `ToolContext`."""
    import inspect

    base = FunctionTool(
        name="t",
        description="d",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=_noop_invoke,
        strict_json_schema=False,
    )
    wrapped = wrap_with_args_recovery(base)
    sig = inspect.signature(wrapped.on_invoke_tool)
    first = next(iter(sig.parameters.values()))
    hints = inspect.get_annotations(wrapped.on_invoke_tool, eval_str=True)
    ann = hints.get(first.name)
    # `ann` is a generic alias like ToolContext[AgentToolContext]; check
    # its origin — that's what the SDK introspects for the downgrade
    # decision.
    origin = getattr(ann, "__origin__", ann)
    assert origin is ToolContext, (
        f"safer's first param must be annotated `ToolContext[...]` so the "
        f"SDK doesn't strip the context — got {ann!r}"
    )


def test_wrap_preserves_all_private_sdk_fields():
    """Regression: the previous version of this wrap reconstructed
    FunctionTool with only its 5 public ctor args, silently dropping
    every other field — including SDK-internal ones like
    `_failure_error_function`, `_use_default_failure_error_function`,
    `_tool_origin`, `is_enabled`, `tool_input_guardrails`, etc. The
    result was the model stopped emitting tool_calls altogether for
    wrapped tools.

    Lock in: every field on the original FunctionTool survives the
    wrap (only `on_invoke_tool` is replaced)."""
    import dataclasses as _dc

    base = FunctionTool(
        name="my-tool",
        description="my description",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=_noop_invoke,
        strict_json_schema=False,
    )
    wrapped = wrap_with_args_recovery(base)
    for f in _dc.fields(FunctionTool):
        if f.name == "on_invoke_tool":
            continue  # the one field we deliberately replace
        assert getattr(wrapped, f.name) == getattr(base, f.name), (
            f"field {f.name!r} dropped / changed by the wrap; SDK likely depends on it"
        )


# ─── nullable-arg coercion (the kb_search `page_from='None'` defect) ───


def _kb_shaped_tool(seen: list[dict[str, Any]]) -> FunctionTool:
    """A tool shaped like `kb_search`: one required string plus optional params
    that are `X | null` — the shape strict mode turns into "every property is
    required", which is why the model has to put SOMETHING in each of them."""

    async def capture(_ctx: ToolContext[Any], args: str) -> str:
        seen.append(json.loads(args))
        return "ok"

    nullable = lambda t: {"anyOf": [{"type": t}, {"type": "null"}]}  # noqa: E731
    return FunctionTool(
        name="kb_search",
        description="d",
        params_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "page_from": nullable("integer"),
                "rerank": nullable("boolean"),
                "document": nullable("string"),
            },
            "required": ["query", "page_from", "rerank", "document"],
        },
        on_invoke_tool=capture,
        strict_json_schema=True,
    )


async def test_impossible_string_for_a_nullable_number_or_bool_becomes_null():
    """A `"None"` that reaches a param typed `integer | null` cannot be what the
    model meant — no integer is ever spelled "None" — so it is read as "not set"
    instead of being bounced back as a validation error the model has to guess
    its way out of.

    This is deliberately a SECOND line of defence. `arg_repair` already
    translates a bare Python `None`; this catches the case where the quoted form
    arrives instead, which we cannot observe from here because the model that
    does it runs in production. Making the fix correct for BOTH spellings is what
    lets it ship without first reproducing the exact one."""
    seen: list[dict[str, Any]] = []
    wrapped = wrap_with_args_recovery(_kb_shaped_tool(seen))
    await wrapped.on_invoke_tool(
        _tool_ctx(),
        json.dumps({"query": "x", "page_from": "None", "rerank": "None", "document": "None"}),
    )
    assert seen[0]["page_from"] is None
    assert seen[0]["rerank"] is None


async def test_a_nullable_string_param_is_never_second_guessed():
    """`document` is `string | null`, so `"None"` is a value the model may have
    chosen on purpose — a file really can be called that. Coercing it would swap
    one silent wrong answer for another, so it is passed through untouched and
    the ambiguity stays with the caller who can actually resolve it."""
    seen: list[dict[str, Any]] = []
    wrapped = wrap_with_args_recovery(_kb_shaped_tool(seen))
    await wrapped.on_invoke_tool(
        _tool_ctx(), json.dumps({"query": "x", "page_from": None, "document": "None"})
    )
    assert seen[0]["document"] == "None"


async def test_real_values_for_nullable_params_survive():
    """The guard must only fire on values that CANNOT be the declared type. A
    genuine page number — including the numeric string pydantic would coerce
    anyway — has to reach the tool unchanged."""
    seen: list[dict[str, Any]] = []
    wrapped = wrap_with_args_recovery(_kb_shaped_tool(seen))
    await wrapped.on_invoke_tool(
        _tool_ctx(),
        json.dumps({"query": "x", "page_from": 30, "rerank": True, "document": "report.pdf"}),
    )
    assert seen[0] == {"query": "x", "page_from": 30, "rerank": True, "document": "report.pdf"}

    seen.clear()
    await wrapped.on_invoke_tool(_tool_ctx(), json.dumps({"query": "x", "page_from": "30"}))
    assert seen[0]["page_from"] == "30"  # left for pydantic's own int coercion


async def test_a_schema_validation_failure_is_logged_with_the_offending_args(caplog):
    """This failure is invisible today, and that is why it survived in production.

    The SDK catches a pydantic ValidationError inside the tool invoker and turns
    it into a tool RESULT string, so it never reaches our runner's error path,
    never becomes a `ToolCallParseError` event, and never lands in a log an
    operator would read. The model quietly retries and the turn eventually
    succeeds — so from the outside it looks only "slow", and nobody can tell WHICH
    argument the model got wrong.

    We cannot intercept the exception (it is caught before it reaches us), but the
    result carries the SDK's own signature, so the wrap recognises it and records
    the tool, the args it was called with, and the reason. That log line is how a
    production model tells us which spelling it actually emits — the thing this
    fix could not be verified against locally."""

    async def sdk_style_validation_failure(_ctx: ToolContext[Any], _args: str) -> str:
        return (
            "An error occurred while running the tool. Please try again. Error: "
            "Invalid JSON input for tool kb_search: 1 validation error for kb_search_args\n"
            "page_from\n  Input should be a valid integer, unable to parse string as an "
            "integer [type=int_parsing, input_value='None', input_type=str]"
        )

    tool = FunctionTool(
        name="kb_search",
        description="d",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=sdk_style_validation_failure,
        strict_json_schema=True,
    )
    wrapped = wrap_with_args_recovery(tool)
    with caplog.at_level(logging.WARNING, logger="workspace_app.agent.args_recovery"):
        out = await wrapped.on_invoke_tool(_tool_ctx(), json.dumps({"page_from": "None"}))

    assert "Invalid JSON input" in out  # the model still gets its feedback, unchanged
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "kb_search" in logged
    assert "page_from" in logged  # WHICH argument
    assert "None" in logged  # and what it actually sent


async def test_never_nulls_a_value_the_validator_would_have_accepted():
    """The guard is only allowed to be WRONG in one direction.

    Missing a case costs an error message the model was getting anyway. Nulling a
    value the validator WOULD have taken silently replaces the model's choice with
    the operator's default — `rerank="off"` becoming `null` turns reranking back
    ON, which is the opposite of what was asked, with nothing to show for it. So
    the accepted-token set must mirror the validator's, not a hand-picked subset
    of it: pydantic's lax bool parser takes on/off, y/n, t/f as well as
    true/false, yes/no and 1/0."""
    seen: list[dict[str, Any]] = []
    wrapped = wrap_with_args_recovery(_kb_shaped_tool(seen))
    for value in ("on", "off", "y", "n", "t", "f", "TRUE", "No", "1", "0"):
        seen.clear()
        await wrapped.on_invoke_tool(_tool_ctx(), json.dumps({"query": "x", "rerank": value}))
        assert seen[0]["rerank"] == value, f"{value!r} was second-guessed"


async def test_non_finite_numbers_are_read_as_unset():
    """`float()` accepts `NaN` and `inf`, so they used to slip through to the very
    validation error this guard exists to remove — no integer is ever any of
    them. Closing this is safe in a way tightening the numeric check generally is
    not: there is no page 'inf' a user could have meant."""
    seen: list[dict[str, Any]] = []
    wrapped = wrap_with_args_recovery(_kb_shaped_tool(seen))
    for value in ("NaN", "nan", "inf", "-inf", "Infinity"):
        seen.clear()
        await wrapped.on_invoke_tool(_tool_ctx(), json.dumps({"query": "x", "page_from": value}))
        assert seen[0]["page_from"] is None, f"{value!r} should read as unset"


async def test_tool_output_that_merely_quotes_the_error_phrase_is_not_logged(caplog):
    """The detector matches the SDK's error text against the tool RESULT, and for
    `read_file` / `exec` / `list_files` that result is arbitrary workspace content.
    A file that happens to quote the phrase — a log of this very defect, say —
    would otherwise raise a false operator warning. Only a result that is actually
    shaped like the SDK's failure counts."""

    async def echo_content(_ctx: ToolContext[Any], _args: str) -> str:
        return "docs say: Invalid JSON input for tool foo — beware of that error."

    tool = FunctionTool(
        name="read_file",
        description="d",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=echo_content,
        strict_json_schema=True,
    )
    wrapped = wrap_with_args_recovery(tool)
    with caplog.at_level(logging.WARNING, logger="workspace_app.agent.args_recovery"):
        await wrapped.on_invoke_tool(_tool_ctx(), json.dumps({"path": "notes.md"}))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_the_rejection_log_names_the_args_without_dumping_their_content(caplog):
    """Operator logs are not a place to spill workspace data. Every other log line
    in this module deliberately keeps raw payloads out; this one records WHICH
    arguments were sent and their types — enough to identify the offending
    spelling — without copying a `write_file` body or an `exec` command line into
    the log."""

    async def sdk_failure(_ctx: ToolContext[Any], _args: str) -> str:
        return (
            "An error occurred while running the tool. Please try again. Error: "
            "Invalid JSON input for tool write_file: 1 validation error"
        )

    tool = FunctionTool(
        name="write_file",
        description="d",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=sdk_failure,
        strict_json_schema=True,
    )
    wrapped = wrap_with_args_recovery(tool)
    secret = "AWS_SECRET=hunter2"
    with caplog.at_level(logging.WARNING, logger="workspace_app.agent.args_recovery"):
        await wrapped.on_invoke_tool(
            _tool_ctx(), json.dumps({"path": "notes.md", "content": secret})
        )
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "write_file" in logged
    assert "content" in logged  # which argument
    assert secret not in logged  # but never its value
