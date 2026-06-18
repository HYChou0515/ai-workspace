"""DecideThenActModel — structured decide-then-act at the SDK Model.get_response seam.

The live LLM exercise is a separate canned check; here litellm is mocked so we verify
the synthesis logic: a decision that picks a tool yields a ModelResponse carrying that
tool's call (args from the structured args step), and ``final`` yields a plain message.
"""

import json
from types import SimpleNamespace

import litellm
import pytest
from agents import function_tool

from workspace_app.agent.decide_then_act import DecideThenActModel
from workspace_app.api.litellm_runner import _decide_then_act_enabled


@function_tool
def write_file(path: str, content: str) -> str:
    """Create a file."""
    return "ok"


def _resp(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _model() -> DecideThenActModel:
    inner = SimpleNamespace(model="m")  # a fake Model — only `.model` is read via passthrough
    return DecideThenActModel(
        inner,  # ty: ignore[invalid-argument-type]
        model="ollama_chat/qwen3:14b",
        base_url=None,
        api_key=None,
    )


async def _get_response(model: DecideThenActModel, tools):
    return await model.get_response(
        "sys", "do the task", None, tools, None, [], None,
        previous_response_id=None, conversation_id=None, prompt=None,
    )


async def test_decision_picks_tool_then_structured_args_become_a_tool_call(monkeypatch):
    calls: list[dict] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        # 1st call = decision; 2nd = args. Distinguish by the schema's shape.
        schema = kwargs["response_format"]["json_schema"]["schema"]
        if "action" in schema["properties"]:
            return _resp(json.dumps({"action": "write_file"}))
        return _resp(json.dumps({"path": "memory/x.md", "content": "the note"}))

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    out = await _get_response(_model(), [write_file])

    # both sub-calls were structured (response_format) + non-streaming
    assert len(calls) == 2
    assert all(c["response_format"]["type"] == "json_schema" for c in calls)
    assert all(c["stream"] is False for c in calls)
    # the synthesized ModelResponse carries a write_file tool call with our args
    fc = next(i for i in out.output if getattr(i, "type", "") == "function_call")
    assert fc.name == "write_file"
    assert json.loads(fc.arguments) == {"path": "memory/x.md", "content": "the note"}


async def test_decision_final_yields_a_plain_message_no_tool_call(monkeypatch):
    async def fake_acompletion(**kwargs):
        if kwargs.get("response_format"):
            return _resp(json.dumps({"action": "final"}))
        return _resp("All done.")  # the free final answer

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    out = await _get_response(_model(), [write_file])

    assert not any(getattr(i, "type", "") == "function_call" for i in out.output)
    assert any(getattr(i, "type", "") == "message" for i in out.output)


async def test_no_tools_just_answers_unconstrained(monkeypatch):
    seen: list[dict] = []

    async def fake_acompletion(**kwargs):
        seen.append(kwargs)
        return _resp("hello")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    out = await _get_response(_model(), [])
    assert len(seen) == 1 and "response_format" not in seen[0]  # free completion, no schema
    assert any(getattr(i, "type", "") == "message" for i in out.output)


@pytest.mark.parametrize(
    ("val", "on"),
    [("1", True), ("true", True), ("ON", True), ("", False), ("0", False), ("no", False)],
)
def test_toggle_parses(monkeypatch, val, on):
    monkeypatch.setenv("WORKSPACE_AGENT_DECIDE_THEN_ACT", val)
    assert _decide_then_act_enabled() is on
