"""#739 P3: turning a span of conversation into a précis.

Compaction is not deletion. The originals stay in the store; this module only
produces the text that stands in for them when the thread is replayed to the
model.
"""

from __future__ import annotations

import pytest

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.compaction import (
    AgentCompactor,
    compaction_prompt,
    split_for_compaction,
)
from workspace_app.api.events import MessageDelta, RunDone


class _Msg:
    """Duck-typed stand-in for `resources.Message`."""

    def __init__(self, role: str, content: str, tool_name: str | None = None) -> None:
        self.role = role
        self.content = content
        self.tool_name = tool_name


class _Runner:
    """Captures what the sub-agent was actually asked, and answers with a fixed
    summary the way a real one would — as streamed deltas."""

    def __init__(self, text: str = "先前:使用者要修 X,試過 Y,失敗在 Z。") -> None:
        self.text = text
        self.prompt: str | None = None
        self.ctx: AgentToolContext | None = None

    async def run(self, prompt: str, ctx: AgentToolContext):
        self.prompt = prompt
        self.ctx = ctx
        yield MessageDelta(text=self.text)
        yield RunDone()


@pytest.mark.asyncio
async def test_the_summary_is_written_in_a_throwaway_context():
    """The span being compacted is the noisiest input in the system — it is
    compacted precisely because it no longer fits. Feeding it back into the
    caller's own context to summarise it would blow the window open in order to
    reclaim it, so the sub-agent starts EMPTY and only the précis comes back
    (the `ask_knowledge_base` shape, #270)."""
    runner = _Runner()
    base = AgentToolContext(investigation_id="i1", history=[{"role": "user", "content": "舊的"}])

    got = await AgentCompactor(runner).summarise(
        [_Msg("user", "很久以前的問題"), _Msg("assistant", "很久以前的回答")],
        ctx=base,
    )

    assert got == "先前:使用者要修 X,試過 Y,失敗在 Z。"
    assert runner.ctx is not None
    assert runner.ctx.history == [], "the sub-agent must not inherit the caller's thread"
    assert "很久以前的問題" in (runner.prompt or ""), "the span to summarise IS the payload"
    assert base.history, "the caller's own context is left untouched"


def test_the_prompt_names_what_must_survive_verbatim():
    """A summary that paraphrases a path, an id or a command is worse than no
    summary: the next turn acts on it. And the user's original request is the
    thing every later message refers back to — the layered reducer's habit of
    sacrificing it first is exactly what compaction exists to stop."""
    got = compaction_prompt([_Msg("user", "幫我修 build")])
    for demand in ("逐字", "最初", "未完成"):
        assert demand in got, f"the summariser is never told to keep: {demand}"


def test_the_newest_turns_are_never_compacted():
    """Compaction must leave the recent exchange alone. The user's next message
    almost always refers to the last few turns, and a précis of "what we just
    said" is both the least useful summary and the most damaging to lose detail
    from. It also bounds the input: the span handed to the summariser is
    everything EXCEPT that tail, so it can never be the whole window."""
    msgs = [_Msg("user", f"訊息{i}") for i in range(10)]
    old, keep = split_for_compaction(msgs, keep_recent=3)
    assert [m.content for m in keep] == ["訊息7", "訊息8", "訊息9"]
    assert [m.content for m in old] == [f"訊息{i}" for i in range(7)]


def test_a_thread_with_nothing_but_recent_turns_is_left_alone():
    """Nothing to gain and a turn's latency to lose. An empty span is the signal
    the caller checks BEFORE spending an LLM call on it."""
    msgs = [_Msg("user", "只有一句")]
    old, keep = split_for_compaction(msgs, keep_recent=3)
    assert old == []
    assert len(keep) == 1


def test_compaction_never_reaches_back_past_an_earlier_summary():
    """A second compaction summarises what happened SINCE the first one. Reading
    back past it would re-summarise a summary — each pass copying a copy, and
    the original request degrading a little every time."""
    msgs = [
        _Msg("user", "很久以前"),
        _Msg("summary", "第一次的摘要"),
        _Msg("user", "之後1"),
        _Msg("user", "之後2"),
    ]
    old, keep = split_for_compaction(msgs, keep_recent=1)
    assert [m.content for m in old] == ["第一次的摘要", "之後1"]
    assert [m.content for m in keep] == ["之後2"]
