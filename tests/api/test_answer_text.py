"""Review round 1 (docs/plan-friendly-errors.md D3): a model call re-sent after
it broke mid-answer has its partial text WITHDRAWN — and that has to hold for
every caller that assembles an answer from a run's events, not only the chat
transcript. Five of them joined `MessageDelta` text themselves and returned the
broken half glued to the whole answer to the model that asked."""

from __future__ import annotations

from typing import Any

import httpx
import litellm
import pytest

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import litellm_runner as lr
from workspace_app.api.events import AnswerText, MessageDelta, OutputRetracted
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.resources import AgentConfig

from ._fake_llm import Fail, Say, ScriptedLlm


def _unavailable() -> Exception:
    resp = httpx.Response(503, request=httpx.Request("POST", "http://llm.test/v1"))
    return litellm.ServiceUnavailableError("down", "openai", "m", response=resp)


def _broke_then_whole() -> ScriptedLlm:
    return ScriptedLlm([Fail(_unavailable(), deltas=["half an "]), Say(["the whole answer"])])


def test_the_answer_withdraws_what_a_re_sent_call_showed() -> None:
    answer = AnswerText()
    for ev in (
        MessageDelta(text="Before. "),
        MessageDelta(text="thinking", reasoning=True),
        MessageDelta(text="half an "),
        OutputRetracted(text_chars=8, reasoning_chars=0),
        MessageDelta(text="the whole answer"),
    ):
        answer.add(ev)
    assert answer.text == "Before. the whole answer"


def test_with_reasoning_kept_both_channels_are_withdrawn() -> None:
    """The compaction summary keeps reasoning text too; a re-sent call's output
    on BOTH channels is the tail of what it gathered."""
    answer = AnswerText(include_reasoning=True)
    for ev in (
        MessageDelta(text="kept "),
        MessageDelta(text="half", reasoning=True),
        MessageDelta(text=" an"),
        OutputRetracted(text_chars=3, reasoning_chars=4),
        MessageDelta(text="whole"),
    ):
        answer.add(ev)
    assert answer.text == "kept whole"


async def _no_sleep(_s: float) -> None:
    return None


@pytest.mark.asyncio
async def test_a_delegated_agents_report_is_the_answer_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_agent` and the skill-hub review read their report through
    `drive_subagent` — through the real runner, the broken half must not ride
    along."""
    from workspace_app.api.subagent_run import drive_subagent
    from workspace_app.apps.subagents import SubagentDef

    monkeypatch.setattr(lr, "LitellmModel", _broke_then_whole())
    parent = AgentToolContext(investigation_id="i", agent_config=AgentConfig(name="p", model="m"))
    defn = SubagentDef(name="x", description="d", tools=[], body="do it")

    answer, failure = await drive_subagent(LitellmAgentRunner(sleep=_no_sleep), parent, defn, "go")

    assert (answer, failure) == ("the whole answer", None)


@pytest.mark.asyncio
async def test_a_kb_answer_is_the_answer_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ask_knowledge_base` returns `answer_question`'s text to the model."""
    from workspace_app.api.kb_chat_routes import answer_question

    class _NoRetriever:
        def search(self, *a: Any, **k: Any) -> list[Any]:
            return []

    monkeypatch.setattr(lr, "LitellmModel", _broke_then_whole())

    answer = await answer_question(
        LitellmAgentRunner(sleep=_no_sleep),
        _NoRetriever(),  # ty: ignore[invalid-argument-type]
        ["c"],
        "q",
        agent_config=AgentConfig(name="kb", model="m"),
    )

    assert answer == "the whole answer"


def test_no_caller_assembles_an_answer_by_hand() -> None:
    """The five callers above each joined `MessageDelta` text themselves, and
    each one had to be found. A sixth would be written the same way, so the
    rule is checked over the source: outside the modules that MAKE or REDUCE
    the stream (and so already honour `OutputRetracted`), nothing inspects a
    `MessageDelta` — an answer is read through `AnswerText`."""
    import ast
    from pathlib import Path

    src = Path(__file__).parents[2] / "src" / "workspace_app"
    allowed = {
        "api/events.py",  # AnswerText itself
        "api/turns.py",  # the transcript reducer
        "api/repetition_guard.py",
        "api/agent_progress.py",  # progress lines, never the answer
        "api/litellm_runner.py",  # makes the stream
    }
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        if rel in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
                and len(node.args) == 2
                and "MessageDelta" in ast.unparse(node.args[1])
            ):
                found.append(f"{rel}:{node.lineno}")
    assert found == []
