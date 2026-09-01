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
    compaction_plan,
    compaction_prompt,
    plan_for_budget,
    split_for_compaction,
)
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.context_budget import estimate_messages


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
    keep_tokens = estimate_messages([_Msg("user", "訊息7")]) * 3
    old, keep = split_for_compaction(msgs, keep_tokens=keep_tokens, estimate=estimate_messages)
    assert [m.content for m in keep] == ["訊息7", "訊息8", "訊息9"]
    assert [m.content for m in old] == [f"訊息{i}" for i in range(7)]


def test_a_thread_with_nothing_but_recent_turns_is_left_alone():
    """Nothing to gain and a turn's latency to lose. An empty span is the signal
    the caller checks BEFORE spending an LLM call on it."""
    msgs = [_Msg("user", "只有一句")]
    old, keep = split_for_compaction(msgs, keep_tokens=10_000, estimate=estimate_messages)
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
    keep_tokens = estimate_messages([_Msg("user", "之後2")])
    old, keep = split_for_compaction(msgs, keep_tokens=keep_tokens, estimate=estimate_messages)
    assert [m.content for m in old] == ["第一次的摘要", "之後1"]
    assert [m.content for m in keep] == ["之後2"]


def test_one_huge_message_does_not_get_to_keep_the_whole_window():
    """The tail is bounded by TOKENS, not by a count. Three messages sounds
    modest until three `exec` dumps arrive: a count-based tail would "keep the
    last 3" and hand back a tail that alone overflows the window, so compaction
    would run, cost a turn, and change nothing.

    At least one message always survives — replacing the whole thread with a
    summary and nothing else is not a conversation."""
    msgs = [_Msg("user", "小"), _Msg("tool", "巨" * 5_000), _Msg("user", "小")]
    old, keep = split_for_compaction(msgs, keep_tokens=50, estimate=estimate_messages)
    assert [m.content for m in keep] == ["小"], "the dump does not fit the tail"
    assert len(old) == 2


def test_the_plan_says_where_the_summary_goes():
    """The summary is INSERTED before the kept tail, not appended — appending
    would put it after the newest messages, where `history_items` would replay
    it as the latest thing said and drop the very turns it was meant to precede.

    The index is in the ORIGINAL list's coordinates, because that is the list
    the caller mutates."""
    msgs = [_Msg("user", f"訊息{i}") for i in range(5)]
    at, span = compaction_plan(
        msgs,
        keep_tokens=estimate_messages([_Msg("user", "訊息4")]),
        estimate=estimate_messages,
    )
    assert at == 4
    assert [m.content for m in span] == ["訊息0", "訊息1", "訊息2", "訊息3"]


def test_nothing_to_compact_is_reported_as_an_empty_span():
    """The caller checks this BEFORE spending an LLM call — an empty span is the
    whole signal. `insert_at` is deliberately not asserted here: with nothing to
    replace there is nothing to insert, so its value is a don't-care and pinning
    one would be a test of the implementation rather than of the behaviour."""
    _at, span = compaction_plan(
        [_Msg("user", "只有一句")], keep_tokens=10_000, estimate=estimate_messages
    )
    assert span == []


def test_an_unknown_ceiling_never_compacts():
    """`history_budget` returns None for "no ceiling known", and #624's rule is
    that we then send everything and learn the real limit from the response. A
    thread that is never trimmed must never be compacted either — compacting on
    a guess would spend a turn AND lose detail for a limit nobody measured."""
    _at, span = plan_for_budget(
        [_Msg("user", "很長" * 1000)],
        used=999_999,
        budget=None,
        estimate=estimate_messages,
    )
    assert span == []


def test_a_thread_that_still_fits_is_left_alone():
    """The trigger is the moment the reducer would otherwise start throwing
    things away — not a separate threshold anyone has to tune."""
    _at, span = plan_for_budget(
        [_Msg("user", f"訊息{i}") for i in range(20)],
        used=100,
        budget=1_000,
        estimate=estimate_messages,
    )
    assert span == []


def test_an_overflowing_thread_compacts_and_keeps_a_tail():
    """Over budget: the old span is handed to the summariser and a tail stays.
    The tail is sized from the BUDGET, so it always leaves room for the summary
    itself plus somewhere for the conversation to grow before the next
    compaction — compacting again on the very next turn would cost a round trip
    every time."""
    msgs = [_Msg("user", f"訊息{i}") for i in range(20)]
    at, span = plan_for_budget(msgs, used=5_000, budget=40, estimate=estimate_messages)
    assert span, "over budget must produce a span to summarise"
    assert at == len(span), "no earlier summary here, so the index is the span length"
    assert len(span) < len(msgs), "something must survive for the model to answer"


def test_a_turn_that_would_not_fit_compacts_before_it_runs():
    """#739 P4: the whole point, end to end. A thread past its ceiling used to
    reach the model with its oldest messages — the user's original request among
    them — silently dropped. Now a summary is written in their place, the
    originals stay in the store, and the turn runs against a thread that fits.

    The summary is INSERTED before the kept tail, not appended: appended it
    would be replayed as the newest thing said, dropping the very turns it was
    meant to precede."""
    from workspace_app.api import create_app
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, Message, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient
    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="這是摘要"), RunDone()]),
        get_user_id=lambda: "alice",
        context_limit=6_000,
    )
    rm = spec.get_resource_manager(Conversation)
    seeded = [Message(role="user", content=f"很久以前的第{i}個問題" * 40) for i in range(12)]
    conv = rm.create(Conversation(item_id=iid, created_ms=1, messages=seeded))

    TestClient(app).post(
        f"/a/rca/items/{iid}/chats/{conv.resource_id}/messages",
        json={"content": "接下來呢"},
    )

    after = rm.get(conv.resource_id).data
    assert isinstance(after, Conversation)
    roles = [m.role for m in after.messages]
    assert "summary" in roles, "an over-budget thread must be compacted"
    at = roles.index("summary")
    assert at > 0, "the span it replaces stays in the store above it"
    assert "user" in roles[at:], "something must survive after the summary"
    assert after.messages[at].content, "an empty summary is worse than no summary"


def test_the_user_can_compact_a_thread_that_still_fits():
    """#739 P5: asking is the whole trigger. The automatic path waits until the
    thread no longer fits, but a person pressing compact has a reason we do not
    have — they know the last hour of debugging is over and want the window back
    before the next question, not after it stops fitting.

    So the budget check that gates the automatic path must NOT gate this one."""
    from workspace_app.api import create_app
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, Message, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient
    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="這是摘要"), RunDone()]),
        get_user_id=lambda: "alice",
        # Roomy on purpose: nothing here is close to full.
        context_limit=200_000,
    )
    rm = spec.get_resource_manager(Conversation)
    conv = rm.create(
        Conversation(
            item_id=iid,
            created_ms=1,
            messages=[Message(role="user", content=f"第{i}個問題") for i in range(6)],
        )
    )

    r = TestClient(app).post(f"/a/rca/items/{iid}/chats/{conv.resource_id}/compact")
    assert r.status_code in (200, 202), r.text

    after = rm.get(conv.resource_id).data
    assert isinstance(after, Conversation)
    roles = [m.role for m in after.messages]
    assert "summary" in roles, "pressing compact must compact"
    at = roles.index("summary")
    assert at > 0, "the originals stay above it"
    assert roles[at + 1 :], "something must survive after the summary"


def test_compacting_a_thread_with_nothing_to_compact_is_not_an_error():
    """One message, nothing behind it. Refusing with a 4xx would make the button
    look broken; the honest answer is that it did nothing, and said so."""
    from workspace_app.api import create_app
    from workspace_app.api.runner import ScriptedAgentRunner
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import Conversation, Message, make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient
    from .conftest import register_rca_item

    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="這是摘要"), RunDone()]),
        get_user_id=lambda: "alice",
        context_limit=200_000,
    )
    rm = spec.get_resource_manager(Conversation)
    conv = rm.create(
        Conversation(
            item_id=iid,
            created_ms=1,
            messages=[Message(role="user", content="只有一句")],
        )
    )

    r = TestClient(app).post(f"/a/rca/items/{iid}/chats/{conv.resource_id}/compact")
    assert r.status_code in (200, 202), r.text
    assert r.json()["compacted"] is False

    after = rm.get(conv.resource_id).data
    assert isinstance(after, Conversation)
    assert [m.role for m in after.messages] == ["user"]
