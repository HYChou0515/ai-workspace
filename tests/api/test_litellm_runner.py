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
from workspace_app.api.litellm_runner import LitellmAgentRunner, _map_event, diagnose_error
from workspace_app.resources import AgentConfig


def test_runner_constructs_with_default_config():
    r = LitellmAgentRunner()
    assert r is not None


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


def test_map_event_tool_called_with_invalid_json_keeps_raw():
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="tool_called",
        item=_Item(raw_item=_RawToolCall(call_id="c2", name="exec", arguments="not json")),
    )
    out = _map_event(ev)
    assert isinstance(out, ToolStart)
    assert out.args == {"_raw": "not json"}


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


def test_map_event_drops_other_run_item_names():
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="handoff_requested",
        item=_Item(raw_item=None),
    )
    assert _map_event(ev) is None


@dataclass
class _RawMessage:
    content: list


def test_map_event_maps_message_output_created():
    ev = _StreamEvent(
        type="run_item_stream_event",
        name="message_output_created",
        item=_Item(raw_item=_RawMessage(content=[])),
    )
    out = _map_event(ev)
    assert isinstance(out, MessageDelta)
    # Empty content list → empty text, but the branch is exercised.
    assert out.text == ""


def test_agent_for_with_system_prompt_set():
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", system_prompt="You are helpful.")
    agent = _agent_for(cfg)
    assert agent.instructions == "You are helpful."


def test_agent_for_without_system_prompt():
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws")
    agent = _agent_for(cfg)
    assert agent.instructions is None


def test_agent_for_appends_extra_instructions_to_system_prompt():
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws", system_prompt="Be terse.")
    agent = _agent_for(cfg, extra_instructions="Retry hint: emit one tool at a time.")
    assert isinstance(agent.instructions, str)
    assert "Be terse." in agent.instructions
    assert "Retry hint" in agent.instructions


def test_agent_for_extra_instructions_with_no_base_prompt():
    from workspace_app.api.litellm_runner import _agent_for

    cfg = AgentConfig(name="ws")
    agent = _agent_for(cfg, extra_instructions="Hint only.")
    assert agent.instructions == "Hint only."


# ---- diagnose_error ----


def test_diagnose_extra_data_returns_one_tool_per_turn_hint():
    hint = diagnose_error(RuntimeError("Extra data: line 1 column 54 (char 53)"))
    assert "one tool call" in hint.lower()


def test_diagnose_json_tool_error_returns_same_hint():
    hint = diagnose_error(RuntimeError("failed to parse tool args as json"))
    assert "one tool call" in hint.lower()


def test_diagnose_timeout_returns_smaller_step_hint():
    hint = diagnose_error(RuntimeError("request timed out"))
    assert "smaller step" in hint.lower()


def test_diagnose_unknown_falls_back_to_generic_hint():
    hint = diagnose_error(RuntimeError("some weird upstream 500"))
    assert "try again" in hint.lower()


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
    from specstar import SpecStar

    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox
    from workspace_app.sync import SandboxSync

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    return AgentToolContext(
        workspace_id="ws-x",
        sandbox=sandbox,
        filestore=filestore,
        sync=SandboxSync(filestore=filestore, sandbox=sandbox),
    )


from datetime import UTC, datetime  # noqa: E402 — used by _ctx above


async def test_runner_succeeds_first_try_emits_done():
    runner = _RecordingRunner(scripts=[[MessageDelta(text="hi")]])
    events = [ev async for ev in runner.run("p", _ctx())]
    assert [type(e).__name__ for e in events] == ["MessageDelta", "RunDone"]
    assert runner.feedbacks == [None]


async def test_runner_retries_after_extra_data_error_and_feeds_back_hint():
    err = RuntimeError("Extra data: line 1 column 54 (char 53)")
    runner = _RecordingRunner(scripts=[err, [MessageDelta(text="ok")]])
    events = [ev async for ev in runner.run("p", _ctx())]
    types = [type(e).__name__ for e in events]
    assert types == ["RunError", "MessageDelta", "RunDone"]
    err_ev = next(e for e in events if isinstance(e, RunError))
    assert "one tool call" in err_ev.message.lower()
    # Second attempt should have received the hint as feedback.
    assert runner.feedbacks[0] is None
    assert "one tool call" in (runner.feedbacks[1] or "").lower()


async def test_runner_gives_up_after_max_retries():
    err = RuntimeError("Extra data: blah")
    runner = _RecordingRunner(scripts=[err, err, err], max_retries=2)
    events = [ev async for ev in runner.run("p", _ctx())]
    # 2 retry errors + 1 give-up error + 1 done
    err_events = [e for e in events if isinstance(e, RunError)]
    assert len(err_events) == 3
    assert "giving up" in err_events[-1].message
    done = [e for e in events if isinstance(e, RunDone)]
    assert len(done) == 1


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
    from datetime import UTC, datetime

    from specstar import SpecStar

    from workspace_app.agent import AgentToolContext
    from workspace_app.filestore.specstar_impl import SpecstarFileStore
    from workspace_app.sandbox.mock import MockSandbox
    from workspace_app.sync import SandboxSync

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    sandbox = MockSandbox()
    filestore = SpecstarFileStore(spec)
    ctx = AgentToolContext(
        workspace_id="ws-live",
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
