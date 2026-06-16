"""Tests for LitellmAgentRunner.

The runner depends on Ollama for the live path; those tests skip when
the daemon isn't reachable or the qwen-coder model isn't pulled. The
non-live tests cover construction and the pure event-mapping logic
with fabricated stream events, so the SSE-bound surface stays
exercised even on a CI box without a GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from workspace_app.agent import AgentToolContext
from workspace_app.api.events import MessageDelta, RunDone, RunError, ToolEnd, ToolStart
from workspace_app.api.litellm_runner import (
    LitellmAgentRunner,
    ThinkSplitter,
    _approx_tokens,
    _delta_channel,
    _exact_usage,
    _final_tokens,
    _map_event,
    _should_retry,
    classify_retry_event,
    diagnose_error,
)
from workspace_app.resources import AgentConfig, make_spec


def test_final_tokens_prefers_exact_but_falls_back_when_zero_or_absent():
    # exact usage present and non-zero → use it
    assert _final_tokens((118, 51), 120, 200) == (118, 51)
    # Ollama reports (0, 0) → keep live approximations (200 chars ≈ 50 tok)
    assert _final_tokens((0, 0), 120, 200) == (120, 50)
    # no usage at all → approximations
    assert _final_tokens(None, 120, 200) == (120, 50)
    # partial: exact prompt 0 but completion known
    assert _final_tokens((0, 51), 120, 200) == (120, 51)


def test_delta_channel_classifies_every_delta_event_type():
    assert _delta_channel("response.output_text.delta") == "content"
    assert _delta_channel("response.refusal.delta") == "content"
    assert _delta_channel("response.reasoning_summary_text.delta") == "reasoning"
    assert _delta_channel("response.reasoning_text.delta") == "reasoning"
    # streaming tool-call JSON must NOT leak into the answer
    assert _delta_channel("response.function_call_arguments.delta") == "ignore"
    assert _delta_channel("") == "ignore"


def _drain(splitter: ThinkSplitter, chunks: list[str]) -> tuple[str, str]:
    content, reasoning = "", ""
    for ch in chunks:
        c, r = splitter.feed(ch)
        content += c
        reasoning += r
    c, r = splitter.flush()
    return content + c, reasoning + r


def test_think_splitter_separates_reasoning_from_answer():
    content, reasoning = _drain(ThinkSplitter(), ["<think>secret plan</think>The answer."])
    assert content == "The answer."
    assert reasoning == "secret plan"


def test_think_splitter_handles_tags_split_across_chunks():
    content, reasoning = _drain(ThinkSplitter(), ["hello <thi", "nk>why</thi", "nk> world"])
    assert content == "hello  world"
    assert reasoning == "why"


def test_think_splitter_passes_plain_text_through():
    content, reasoning = _drain(ThinkSplitter(), ["just ", "an answer"])
    assert content == "just an answer"
    assert reasoning == ""


def test_think_splitter_flushes_unclosed_think_as_reasoning():
    content, reasoning = _drain(ThinkSplitter(), ["<think>still thinking"])
    assert content == ""
    assert reasoning == "still thinking"


def test_runner_constructs():
    r = LitellmAgentRunner()
    assert r is not None


async def test_runner_fails_loud_when_ctx_has_no_agent_config():
    """#94: no silent fallback. A turn whose ctx carries no resolved
    agent_config yields a RunError naming the cause (and never reaches the
    LLM), instead of quietly running some default agent."""
    r = LitellmAgentRunner()
    ctx = AgentToolContext(investigation_id="x")  # agent_config defaults to None
    events = [ev async for ev in r.run("hi", ctx)]
    kinds = {type(e).__name__ for e in events}
    assert "RunError" in kinds and "RunDone" in kinds
    msg = " ".join(e.message for e in events if isinstance(e, RunError))
    assert "agent config" in msg.lower()


def test_should_retry_blocks_restart_after_assistant_progress():
    """Issue #26: once the user has seen partial output, restarting the turn
    (which is what the SDK does — no resume) would clobber it. So retry only
    when nothing assistant-visible has streamed yet."""
    # Fresh attempt, no progress, retries available → retry.
    assert _should_retry(progress_made=False, attempt=1, max_retries=2) is True
    # Progress made → never retry, regardless of attempt count.
    assert _should_retry(progress_made=True, attempt=1, max_retries=2) is False
    assert _should_retry(progress_made=True, attempt=0, max_retries=5) is False
    # No progress but out of retries.
    assert _should_retry(progress_made=False, attempt=3, max_retries=2) is False
    # Edge: max_retries=0 means no retry ever.
    assert _should_retry(progress_made=False, attempt=1, max_retries=0) is False


class _ScriptedOnce(LitellmAgentRunner):
    """Test double: replaces `_run_once` with a per-attempt event script so
    we can drive the run() outer loop deterministically without an LLM."""

    def __init__(self, per_attempt):
        super().__init__(max_retries=2)
        self._per_attempt = list(per_attempt)
        self._calls = 0

    async def _run_once(self, prompt, ctx, feedback):  # type: ignore[override]
        events = self._per_attempt[self._calls]
        self._calls += 1
        for ev in events:
            if isinstance(ev, BaseException):
                raise ev
            yield ev


async def test_run_does_not_restart_after_assistant_delta_streamed():
    """#26 repro: if `_run_once` emitted a MessageDelta then raised, the user
    has already seen that content. `run()` must NOT call `_run_once` again
    (which would re-prompt the LLM from scratch and clobber the partial
    answer). Instead, surface the error and end."""
    runner = _ScriptedOnce(
        [
            [MessageDelta(text="Looking at the logs… "), RuntimeError("model timed out")],
            # If `run()` restarts despite progress, this second script would run.
            [MessageDelta(text="should-not-see"), RunDone()],
        ]
    )
    events = [
        ev
        async for ev in runner.run(
            "anything", AgentToolContext(investigation_id="x", agent_config=AgentConfig(name="t"))
        )
    ]
    # Exactly one attempt: delta then RunError then RunDone (no retry).
    assert runner._calls == 1
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["MessageDelta", "RunError", "RunDone"]
    err = next(e for e in events if isinstance(e, RunError))
    assert "model timed out" in err.message
    # The retry-budget phrasing is reserved for "no progress + give up" —
    # we made one attempt that produced output, not N attempts that gave up.
    assert "giving up" not in err.message


async def test_run_still_retries_when_failure_is_early():
    """Counterpart: an early failure (before any content) keeps the existing
    retry-with-hint behaviour — that's the small-model JSON-parse fix path."""
    runner = _ScriptedOnce(
        [
            [ValueError("Extra data: tool call malformed")],  # diagnose → ToolCallParseError
            [MessageDelta(text="OK, one tool at a time."), RunDone()],
        ]
    )
    events = [
        ev
        async for ev in runner.run(
            "anything", AgentToolContext(investigation_id="x", agent_config=AgentConfig(name="t"))
        )
    ]
    assert runner._calls == 2  # actually retried
    kinds = [type(e).__name__ for e in events]
    # First attempt: the classify_retry_event yields ToolCallParseError; then
    # second attempt streams delta + RunDone.
    assert "MessageDelta" in kinds
    assert "RunDone" in kinds
    assert "RunError" not in kinds


async def test_run_retries_when_only_reasoning_streamed_before_failure():
    """Production transcript: model emits ~500 tokens of <think>…</think>
    reasoning, THEN a malformed concatenated tool_call. args_recovery
    raises 'Extra data on `plot`', the exception reaches run(), and
    _should_retry sees progress_made=True (because the runner's
    progress check treated reasoning-channel MessageDelta as visible
    progress). Result: no retry, user sees the raw error and 'then it
    just stopped'.

    Reasoning is collapsed in the FE and doesn't constitute the
    'partial output a restart would clobber' that progress_made is
    supposed to gate. Only visible deltas (reasoning=False) and
    ToolEnd should set progress_made → block retry."""
    runner = _ScriptedOnce(
        [
            [
                MessageDelta(text="Let me think about which tool …", reasoning=True),
                ValueError("Extra data on `plot`: streaming aggregator merged …"),
            ],
            # If retry works the second attempt runs cleanly.
            [MessageDelta(text="OK, one tool at a time."), RunDone()],
        ]
    )
    events = [
        ev
        async for ev in runner.run(
            "anything", AgentToolContext(investigation_id="x", agent_config=AgentConfig(name="t"))
        )
    ]
    assert runner._calls == 2  # actually retried — reasoning didn't gate progress
    kinds = [type(e).__name__ for e in events]
    # First attempt's reasoning delta is preserved; then the retry event;
    # second attempt's visible delta + RunDone. No RunError.
    assert "MessageDelta" in kinds
    assert "RunDone" in kinds
    assert "RunError" not in kinds


def test_runner_constructs_with_custom_config():
    cfg = AgentConfig(name="custom", model="ollama/llama3:8b", system_prompt="Be terse.")
    r = LitellmAgentRunner(cfg)
    assert r is not None


# ---- _map_event unit tests with fabricated stream events ----


@dataclass
class _RawToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class _RawToolOutput:
    call_id: str


@dataclass
class _Item:
    raw_item: Any
    output: Any = None


@dataclass
class _StreamEvent:
    type: str
    name: str = ""
    item: Any = None


def test_map_event_drops_unknown_types():
    assert _map_event(_StreamEvent(type="raw_response_event")) is None


def test_map_event_maps_tool_called():
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="tool_called",
        item=_Item(
            raw_item=_RawToolCall(call_id="c1", name="exec", arguments='{"cmd":["echo","hi"]}')
        ),
    )
    out = _map_event(ev)
    assert isinstance(out, ToolStart)
    assert out.call_id == "c1"
    assert out.name == "exec"
    assert out.args == {"cmd": ["echo", "hi"]}


def test_map_event_tool_called_with_invalid_json_does_not_fabricate_raw_sentinel():
    """When the streaming aggregator merged tool_calls (or the model
    just emitted garbage), the `arguments` string can't be json.loads'd.
    The previous behaviour wrapped it as `{"_raw": <string>}` so the
    FE could display the bytes — but that sentinel was indistinguishable
    from a legitimate tool that happens to have a `_raw` field, and it
    leaked into history projection (`_projected_tool_arguments`) and
    back to the model, which then mimicked the wrong shape on its
    NEXT call (the May-31 `read_file(_raw="…")` regression).

    Stop fabricating the sentinel: empty dict means 'we couldn't
    parse what the model sent.' args_recovery already raises at
    invoke-time so the run retries; this just stops the sentinel
    from polluting persisted state."""
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="tool_called",
        item=_Item(raw_item=_RawToolCall(call_id="c2", name="exec", arguments="not json")),
    )
    out = _map_event(ev)
    assert isinstance(out, ToolStart)
    assert out.args == {}
    assert "_raw" not in out.args


def test_map_event_tool_called_with_empty_args():
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="tool_called",
        item=_Item(raw_item=_RawToolCall(call_id="c3", name="ls", arguments="")),
    )
    out = _map_event(ev)
    assert isinstance(out, ToolStart)
    assert out.args == {}


def test_map_event_maps_tool_output():
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="tool_output",
        item=_Item(raw_item=_RawToolOutput(call_id="c1"), output="exit_code=0\nstdout: hi"),
    )
    out = _map_event(ev)
    assert isinstance(out, ToolEnd)
    assert out.call_id == "c1"
    assert "hi" in out.output


def test_map_event_tool_output_with_dict_raw_item_keeps_call_id():
    """LiteLLM's tool-output raw_item is a FunctionCallOutput dict — the
    call_id must still be extracted (else the FE tool stays 'running')."""
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="tool_output",
        item=_Item(
            raw_item={"call_id": "c9", "output": "ok", "type": "function_call_output"},
            output="exit_code=0\nstdout: done",
        ),
    )
    out = _map_event(ev)
    assert isinstance(out, ToolEnd)
    assert out.call_id == "c9"  # was "" before the dict fix → never matched ToolStart
    assert "done" in out.output


def test_map_event_tool_called_with_dict_raw_item():
    """A dict-shaped function-call raw_item (call_id/name/arguments) maps too."""
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="tool_called",
        item=_Item(
            # some providers hand arguments as an already-parsed dict
            raw_item={"call_id": "c10", "name": "ls", "arguments": {"prefix": "/"}},
        ),
    )
    out = _map_event(ev)
    assert isinstance(out, ToolStart)
    assert out.call_id == "c10"
    assert out.name == "ls"
    assert out.args == {"prefix": "/"}


def test_map_event_drops_other_run_item_names():
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="handoff_requested",
        item=_Item(raw_item=None),
    )
    assert _map_event(ev) is None


def test_approx_tokens_from_chars():
    assert _approx_tokens(0) == 0
    assert _approx_tokens(4) == 1
    assert _approx_tokens(10) == 2  # round(2.5)


def test_exact_usage_reads_provider_usage():
    class _Usage:
        input_tokens = 120
        output_tokens = 45

    class _CtxWrapper:
        usage = _Usage()

    class _Streamed:
        context_wrapper = _CtxWrapper()

    assert _exact_usage(_Streamed()) == (120, 45)


def test_exact_usage_returns_none_when_absent():
    assert _exact_usage(object()) is None


def test_map_event_drops_message_output_created():
    # The full-message event is dropped — the reply streams as incremental
    # raw token deltas (handled in _run_once) to avoid emitting it twice.
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="message_output_created",
        item=_Item(raw_item=None),
    )
    assert _map_event(ev) is None


def test_agent_for_with_system_prompt_set():
    """The base system_prompt is preserved as the prefix; B.10 then appends
    the auto-rendered tool inventory after it. The bare-prompt match
    was stale (pre-B.10); now we assert the prompt STARTS with the
    configured base + the inventory header follows."""
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", system_prompt="You are helpful.")
    agent = _agent_for(cfg)
    assert isinstance(agent.instructions, str)
    assert agent.instructions.startswith("You are helpful.")
    assert "## Tools available" in agent.instructions


def test_agent_for_does_not_force_parallel_tool_calls():
    """#69: forcing `parallel_tool_calls=False` was the only wire-level
    difference from the Replay path — which calls the model with the same
    tools and NO such flag, and reliably gets a structured tool_call. For
    some providers litellm even rejects the flag (ollama_chat →
    UnsupportedParamsError). Leave it unset so the live turn's request
    matches Replay; `args_recovery` still guards the concatenated-args case."""
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(AgentConfig(name="ws"))
    assert agent.model_settings.parallel_tool_calls is None


def test_agent_for_wraps_model_in_repairing_model_by_default():
    """#76 self-repair is ON by default: _agent_for wraps the LitellmModel in a
    RepairingModel so malformed tool-call JSON is fixed at the output boundary.
    Commenting out the single wrap line in _agent_for disables it (falls back to
    the bare LitellmModel) — no config/env knob."""
    from agents.extensions.models.litellm_model import LitellmModel

    from workspace_app.agent.repairing_model import RepairingModel
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(AgentConfig(name="ws"))
    assert isinstance(agent.model, RepairingModel)
    # transparent passthrough: the real model id is still reachable for the trace
    assert isinstance(agent.model._inner, LitellmModel)
    assert agent.model.model == AgentConfig(name="ws").model


def test_agent_for_reasoning_effort_still_set_without_forcing_parallel_tool_calls():
    """The reasoning-effort selector must keep threading effort into
    ModelSettings, but it must NOT re-introduce the parallel_tool_calls
    flag (#69)."""
    from workspace_app.api.litellm_runner import _agent_for

    agent = _agent_for(AgentConfig(name="ws"), reasoning_effort="medium")
    assert agent.model_settings.parallel_tool_calls is None
    assert agent.model_settings.reasoning is not None
    assert agent.model_settings.reasoning.effort == "medium"


def test_emit_llm_trace_logs_request_and_flags_text_tool_call(caplog):
    """#69 observability: the per-turn trace line carries the redacted
    endpoint + tool knobs we sent, and labels a no-tool turn whose text
    looks like a call as 'text-looks-like-tool-call' — the exact symptom."""
    import logging

    from workspace_app.agent import AgentToolContext
    from workspace_app.api.litellm_runner import _agent_for, _emit_llm_trace

    cfg = AgentConfig(name="kb", allowed_tools=["kb_search"], llm_base_url="http://proxy:4000/v1")
    agent = _agent_for(cfg)
    ctx = AgentToolContext(agent_config=cfg)
    with caplog.at_level(logging.INFO, logger="workspace_app.api.litellm_runner"):
        _emit_llm_trace(
            agent,
            ctx,
            runner_base_url=None,
            tool_calls=0,
            content_text='I will kb_search({"query": "voids"})',
        )
    line = next(r.message for r in caplog.records if "LLM turn:" in r.message)
    assert "endpoint=proxy:4000" in line
    assert "tools=[kb_search]" in line
    assert "parallel_tool_calls=unset" in line  # post-#69: no longer forced false
    assert "outcome=text-looks-like-tool-call" in line


def test_emit_llm_trace_swallows_extraction_errors(caplog):
    """A trace must never break a turn: if reading the agent's settings
    explodes, it degrades to a debug breadcrumb instead of raising."""
    import logging

    from workspace_app.agent import AgentToolContext
    from workspace_app.api.litellm_runner import _emit_llm_trace

    class _Boom:
        tools: list = []
        model = None

        @property
        def model_settings(self):
            raise RuntimeError("boom")

    with caplog.at_level(logging.DEBUG, logger="workspace_app.api.litellm_runner"):
        _emit_llm_trace(
            _Boom(), AgentToolContext(), runner_base_url=None, tool_calls=0, content_text=""
        )
    assert any("trace" in r.message.lower() for r in caplog.records)


def test_agent_for_without_system_prompt():
    """No base + default workspace tool set still yields a non-None
    prompt — the tool inventory section is always there when tools are
    registered (B.10). The pre-B.10 expectation of `is None` was stale."""
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws")
    agent = _agent_for(cfg)
    assert isinstance(agent.instructions, str)
    assert agent.instructions.startswith("## Tools available")
    # The 9 default workspace tools all appear under the inventory.
    for name in ("exec", "read_file", "write_file", "ask_knowledge_base"):
        assert f"### `{name}`" in agent.instructions


def test_agent_for_with_explicit_empty_allowed_tools_registers_zero_tools():
    """Tri-state contract for `allowed_tools` (Q4-followup of the
    config grill): ``None`` → defaults, ``[]`` → none, ``[...]`` →
    exact. The old runner aliased ``[]`` to ``None`` (a deploy
    putting kb_chat behind a generic preset got every workspace tool
    instead of the loud "you forgot kb_search" failure). This test
    pins the explicit-empty case end-to-end through `_agent_for`."""
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", allowed_tools=[])
    agent = _agent_for(cfg)
    assert agent.tools == []


def test_agent_for_with_explicit_list_registers_only_those_tools():
    """The exact-list path: ``allowed_tools=["read_file"]`` → only
    read_file is exposed."""
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", allowed_tools=["read_file"])
    agent = _agent_for(cfg)
    names = [t.name for t in agent.tools]
    assert names == ["read_file"]


def test_agent_for_with_none_allowed_tools_registers_defaults():
    """The ``None`` (= "haven't specified") case — preserved behaviour
    so existing test fixtures + bundled RCA presets keep working."""
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", allowed_tools=None)
    agent = _agent_for(cfg)
    names = {t.name for t in agent.tools}
    # The workspace defaults — same set as the original test above.
    assert {"exec", "read_file", "write_file", "ask_knowledge_base"} <= names


def test_agent_for_appends_extra_instructions_to_system_prompt():
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", system_prompt="Be terse.")
    agent = _agent_for(cfg, extra_instructions="Retry hint: emit one tool at a time.")
    assert isinstance(agent.instructions, str)
    assert "Be terse." in agent.instructions
    assert "Retry hint" in agent.instructions


def test_agent_for_extra_instructions_with_no_base_prompt():
    """Extra instructions (a retry feedback hint) become the prefix when
    no base prompt is set; the auto tool inventory follows."""
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws")
    agent = _agent_for(cfg, extra_instructions="Hint only.")
    assert isinstance(agent.instructions, str)
    assert agent.instructions.startswith("Hint only.")
    assert "## Tools available" in agent.instructions


def test_agent_for_appends_tool_inventory_section_to_instructions():
    """B.10 regression: every tool registered on the agent appears in
    the auto-rendered `## Tools available` section the LLM sees in its
    system prompt — name, description, JSON args schema. Small models
    misread function tools as shell binaries without this; large
    models can rely on the tool_choice payload alone, but we still
    want it for parity + debugging."""
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", system_prompt="anchor")
    agent = _agent_for(cfg)
    instructions = agent.instructions
    assert isinstance(instructions, str)
    # the section header includes the "DO NOT exec a tool name" warning
    # that's the whole point of B.10.
    assert "Each entry below is a **function tool**" in instructions
    assert "Do NOT" in instructions
    # every registered tool's name + JSON schema block is there
    for t in agent.tools:
        assert f"### `{t.name}`" in instructions, f"tool {t.name} missing from prompt"


# ---- diagnose_error ----


def test_diagnose_concatenated_tool_calls_returns_one_tool_per_turn_hint():
    """#76: our own ConcatenatedToolCallsError routes to the 'one tool call
    per turn' hint via isinstance, independent of message wording."""
    from workspace_app.agent.args_recovery import ConcatenatedToolCallsError

    hint = diagnose_error(ConcatenatedToolCallsError("Tool `plot` received more than one…"))
    assert "one tool call" in hint.lower()


def test_diagnose_malformed_args_tells_model_to_resend_valid_json():
    """#76: a single malformed-JSON tool call must NOT be mis-coached as
    'you combined multiple tool calls' — it gets an accurate 'your args
    weren't valid JSON, re-send one JSON object' hint."""
    from workspace_app.agent.args_recovery import MalformedToolArgsError

    hint = diagnose_error(MalformedToolArgsError("The arguments for tool `read_file`…"))
    low = hint.lower()
    assert "valid json" in low
    assert "multiple tool calls" not in low


def test_diagnose_non_object_args_tells_model_to_send_one_object():
    """#76: valid JSON but wrong shape (list/scalar) → 'send a single JSON
    object' hint, distinct from both the malformed and the concat hints."""
    from workspace_app.agent.args_recovery import NonObjectToolArgsError

    hint = diagnose_error(NonObjectToolArgsError("…must be a single JSON object, not a list."))
    low = hint.lower()
    assert "json object" in low
    assert "multiple tool calls" not in low


def test_diagnose_upstream_extra_data_string_still_routes_to_concat_hint():
    """Backstop: a genuine upstream LiteLLM/Ollama 'Extra data: line…' error
    (a plain string, not our exception type) still means concatenation."""
    hint = diagnose_error(RuntimeError("Extra data: line 1 column 54 (char 53)"))
    assert "one tool call" in hint.lower()


def test_diagnose_upstream_json_tool_string_routes_to_malformed_hint():
    """#76 flip: a generic upstream 'parse tool args as json' failure (no
    'extra data') is a malformed-single-object case, so it now routes to the
    'valid JSON' hint — NOT the old 'one tool call' hint."""
    hint = diagnose_error(RuntimeError("failed to parse tool args as json"))
    assert "valid json" in hint.lower()


def test_diagnose_timeout_returns_smaller_step_hint():
    hint = diagnose_error(RuntimeError("request timed out"))
    assert "smaller step" in hint.lower()


def test_diagnose_unknown_falls_back_to_generic_hint():
    hint = diagnose_error(RuntimeError("some weird upstream 500"))
    assert "try again" in hint.lower()


# ---- classify_retry_event ----


@pytest.mark.parametrize(
    "exc_type",
    ["MalformedToolArgsError", "NonObjectToolArgsError", "ConcatenatedToolCallsError"],
)
def test_classify_retry_event_maps_all_tool_arg_errors_to_parse_error(exc_type):
    """#76: every flavour of bad tool args is a model-format glitch the FE
    should render as a parse-error banner — not a generic RunError."""
    import workspace_app.agent.args_recovery as ar
    from workspace_app.api.events import ToolCallParseError

    exc = getattr(ar, exc_type)("bad")
    ev = classify_retry_event(exc, "some hint")
    assert isinstance(ev, ToolCallParseError)
    assert ev.hint == "some hint"


def test_classify_retry_event_carries_raw_offending_args_for_transparency():
    """#76: the user has a right to see WHAT the model got wrong. The parse-error
    event must carry the model's actual bad args string in `raw` (and the
    affected tool's call_id) so the FE can show it, not just a generic hint."""
    from workspace_app.agent.args_recovery import MalformedToolArgsError
    from workspace_app.api.events import ToolCallParseError

    exc = MalformedToolArgsError(
        "clean message", tool_name="read_file", raw_args='{"path": ./hello.md"}'
    )
    ev = classify_retry_event(exc, "re-send valid JSON")
    assert isinstance(ev, ToolCallParseError)
    assert ev.raw == '{"path": ./hello.md"}'  # the model's actual mistake, surfaced
    assert ev.hint == "re-send valid JSON"


def test_classify_retry_event_non_tool_error_is_generic_run_error():
    from workspace_app.api.events import RunError

    ev = classify_retry_event(RuntimeError("upstream 500"), "h")
    assert isinstance(ev, RunError)


# ---- retry loop ----


class _RecordingRunner(LitellmAgentRunner):
    """Test double: lets us script the exceptions/events for _run_once."""

    def __init__(self, scripts, **kw):
        super().__init__(**kw)
        self._scripts = list(scripts)
        self.feedbacks: list[str | None] = []

    async def _run_once(self, prompt, ctx, feedback):
        self.feedbacks.append(feedback)
        script = self._scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        for ev in script:
            yield ev


def _ctx() -> AgentToolContext:

    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox
    from workspace_app.sync import SandboxSync

    spec = make_spec(default_user="u")
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    return AgentToolContext(
        investigation_id="ws-x",
        agent_config=AgentConfig(name="t"),
        sandbox=sandbox,
        filestore=filestore,
        sync=SandboxSync(filestore=filestore, sandbox=sandbox),
    )


async def test_runner_succeeds_first_try_emits_done():
    runner = _RecordingRunner(scripts=[[MessageDelta(text="hi")]])
    events = [ev async for ev in runner.run("p", _ctx())]
    assert [type(e).__name__ for e in events] == ["MessageDelta", "RunDone"]
    assert runner.feedbacks == [None]


async def test_runner_retries_after_extra_data_error_and_feeds_back_hint():
    """Extra-data classifies as ToolCallParseError (not generic RunError)
    so the FE can render it distinctly."""
    from workspace_app.api.events import ToolCallParseError

    err = RuntimeError("Extra data: line 1 column 54 (char 53)")
    runner = _RecordingRunner(scripts=[err, [MessageDelta(text="ok")]])
    events = [ev async for ev in runner.run("p", _ctx())]
    types = [type(e).__name__ for e in events]
    assert types == ["ToolCallParseError", "MessageDelta", "RunDone"]
    tcpe = next(e for e in events if isinstance(e, ToolCallParseError))
    assert "one tool call" in tcpe.hint.lower()
    # Second attempt should have received the hint as feedback.
    assert runner.feedbacks[0] is None
    assert "one tool call" in (runner.feedbacks[1] or "").lower()


async def test_runner_gives_up_after_max_retries():
    """N retries of Extra-data → N ToolCallParseError + 1 final RunError
    (giving up) + RunDone."""
    from workspace_app.api.events import ToolCallParseError

    err = RuntimeError("Extra data: blah")
    runner = _RecordingRunner(scripts=[err, err, err], max_retries=2)
    events = [ev async for ev in runner.run("p", _ctx())]
    tcpes = [e for e in events if isinstance(e, ToolCallParseError)]
    err_events = [e for e in events if isinstance(e, RunError)]
    assert len(tcpes) == 2  # two retries
    assert len(err_events) == 1  # final give-up
    assert "giving up" in err_events[-1].message
    done = [e for e in events if isinstance(e, RunDone)]
    assert len(done) == 1


async def test_runner_emits_max_turns_exceeded_terminal():
    """When the underlying agents-SDK raises MaxTurnsExceeded, it's a
    hard ceiling — no retry, terminal event. The SDK exception only carries
    a free-text message, so the runner reports its OWN configured turn
    budget (never a useless 0)."""
    from agents import MaxTurnsExceeded as _AgentsMTE

    from workspace_app.api.events import MaxTurnsExceeded as MTEEvent

    exc = _AgentsMTE("Max turns (7) exceeded")  # no turns_run attribute
    runner = _RecordingRunner(scripts=[exc], max_turns=7)
    events = [ev async for ev in runner.run("p", _ctx())]
    types = [type(e).__name__ for e in events]
    assert types == ["MaxTurnsExceeded", "RunDone"]
    mte = next(e for e in events if isinstance(e, MTEEvent))
    assert mte.turns == 7


async def test_runner_classifies_non_parse_retry_as_run_error():
    """Generic errors still get the RunError catch-all on retry."""
    err = RuntimeError("upstream 500: something else")
    runner = _RecordingRunner(scripts=[err, [MessageDelta(text="ok")]])
    events = [ev async for ev in runner.run("p", _ctx())]
    types = [type(e).__name__ for e in events]
    assert types == ["RunError", "MessageDelta", "RunDone"]
    err_ev = next(e for e in events if isinstance(e, RunError))
    assert "retry:" in err_ev.message


# ---- live test against Ollama (skipped when unavailable) ----


def _ollama_default_model_available() -> bool:
    from workspace_app.resources import AgentConfig

    default = AgentConfig(name="probe").model
    # Strip the "ollama_chat/" or "ollama/" prefix LiteLLM uses.
    model_tag = default.split("/", 1)[1] if "/" in default else default
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        if resp.status_code != 200:
            return False
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return any(model_tag in m for m in models)
    except (httpx.HTTPError, OSError):
        return False


@pytest.mark.skipif(
    not _ollama_default_model_available(),
    reason="Ollama or the default AgentConfig.model not available",
)
async def test_live_run_against_ollama_emits_at_least_one_event():

    from workspace_app.agent import AgentToolContext
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox
    from workspace_app.sync import SandboxSync

    spec = make_spec(default_user="u")
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    ctx = AgentToolContext(
        investigation_id="ws-live",
        agent_config=AgentConfig(name="workspace-agent"),
        sandbox=sandbox,
        filestore=filestore,
        sync=SandboxSync(filestore=filestore, sandbox=sandbox),
    )
    runner = LitellmAgentRunner()
    events = []
    async for ev in runner.run("Say hello in one short sentence.", ctx):
        events.append(ev)
        if len(events) >= 10:
            break  # bound test runtime
    # At minimum, the RunDone sentinel must come through.
    assert events, "expected at least one event"
    types = {type(e).__name__ for e in events}
    assert "RunDone" in types or "MessageDelta" in types


def test_trace_workflow_name_distinguishes_run_flavours():
    """The SDK trace label tells wiki maintenance / reader / merge apart from
    KB chat and the RCA workspace turn (issue #11 telemetry, wiki visibility)."""
    from workspace_app.api.litellm_runner import _trace_workflow_name
    from workspace_app.resources import AgentConfig

    def ctx(**kw) -> AgentToolContext:
        return AgentToolContext(**kw)

    # Wiki configs have fixed names → matched precisely.
    assert _trace_workflow_name(ctx(agent_config=AgentConfig(name="Wiki Maintainer"))) == (
        "Wiki maintainer"
    )
    assert _trace_workflow_name(ctx(agent_config=AgentConfig(name="Wiki Reader"))) == "Wiki reader"
    assert _trace_workflow_name(ctx(agent_config=AgentConfig(name="Wiki Merge"))) == "Wiki merge"
    # Context flags are belt-and-suspenders for the maintainer / reader.
    assert _trace_workflow_name(ctx(wiki_new_source="x")) == "Wiki maintainer"
    assert _trace_workflow_name(ctx(wiki_cite_sources=True)) == "Wiki reader"
    # A retriever-bearing context is a KB-style lookup; the rest is the RCA turn.
    assert _trace_workflow_name(ctx(retriever=object())) == "KB chat"  # type: ignore[arg-type]
    assert _trace_workflow_name(ctx(investigation_id="inv-1")) == "RCA turn"
