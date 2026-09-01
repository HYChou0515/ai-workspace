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
    a guess would spend a turn AND lose detail for a limit nobody measured.

    The thread has to be long enough for the guard to be the reason. A one
    message thread returns an empty span whatever the budget says, because
    `split_for_compaction` always keeps one — so a spec written on one asserts a
    fact a different rule already guarantees, and passes with this guard deleted.
    (Caught by review: the double agreed with the code for the wrong reason.)"""
    msgs = [_Msg("user", f"很長的訊息{i}" * 50) for i in range(5)]
    _at, span = plan_for_budget(msgs, used=999_999, budget=None, estimate=estimate_messages)
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


def test_the_tail_is_sized_in_the_unit_it_is_spent_in():
    """Found by pressing the button on a running app, not by these tests.

    `used` is the PROVIDER's count of the whole request, so it carries a fixed
    overhead — system prompt, tool schemas, skills index — of several thousand
    tokens that the message estimator cannot see. Sizing the kept tail from that
    figure and then filling it with estimator-measured messages spends one unit
    against another: six short messages "fit" a tail budget meant to hold a
    fraction of the window, the span comes back empty, and compaction silently
    does nothing at all.

    The overhead is recoverable: it is exactly what `used` has that the messages
    themselves do not."""
    msgs = [_Msg("user", f"訊息{i}") for i in range(8)]
    own = estimate_messages(msgs)
    # A real request: the messages are small, the fixed overhead is not.
    at, span = plan_for_budget(
        msgs, used=own + 6_700, budget=8_800, estimate=estimate_messages, force=True
    )
    assert span, "a forced compaction must produce a span even on a small thread"
    assert at == len(span)
    assert len(span) < len(msgs), "and must leave a tail behind"


def test_the_overhead_is_subtracted_on_the_path_that_actually_uses_it():
    """The `- overhead` term only decides anything when the messages' own size
    exceeds what is left of the budget after the fixed overhead — which is
    algebraically the same condition as the automatic trigger. So it governs
    EVERY automatic compaction, and a spec that only exercises `force=True` on a
    small thread leaves it inert: `min(room, own)` picks `own` either way, and
    the subtraction can be deleted with the suite green. (Caught by review.)

    Here the thread is big: `own` is larger than `budget - overhead`, so the
    subtraction is the term that sizes the tail. Without it the tail is sized
    against the whole budget and swallows messages that do not fit the window."""
    msgs = [_Msg("user", f"訊息{i}" * 200) for i in range(10)]
    own = estimate_messages(msgs)
    overhead = 6_000
    budget = 8_000
    assert own > budget - overhead, "the fixture must exercise the subtraction"

    _at, span = plan_for_budget(
        msgs, used=own + overhead, budget=budget, estimate=estimate_messages
    )
    kept = msgs[len(span) :]
    assert estimate_messages(kept) <= budget - overhead, (
        "the tail must fit the room the overhead leaves, not the whole budget"
    )
    assert span, "an over-budget thread must still produce a span"


def test_folding_alone_gets_its_free_pass_before_any_model_is_called():
    """The agreed order is 折疊 → 壓縮 → 丟棄, and the reason is cost: folding a
    bulky tool output is free, compaction costs a round trip and permanently
    replaces a span with a précis.

    A thread pushed over the line by one `exec` dump must therefore NOT be
    compacted — folding that dump brings it back under on its own. Shipping the
    check the other way round meant every such thread paid for a summary it did
    not need. (Caught by review, twice, independently.)"""
    dump = _Msg("tool", "巨" * 20_000, tool_name="exec")
    msgs = [_Msg("user", "問題"), dump, _Msg("user", "再問"), _Msg("user", "最新")]
    own = estimate_messages(msgs)
    overhead = 1_000
    # Over budget by a margin far smaller than the dump itself.
    budget = own + overhead - 5_000

    _at, span = plan_for_budget(
        msgs, used=own + overhead, budget=budget, estimate=estimate_messages
    )
    assert span == [], "folding the dump alone brings this under budget"


def test_the_summariser_is_handed_the_folded_span_not_the_raw_one():
    """When compaction IS needed, the span it summarises is the folded one.

    Nothing else bounds that request: the span grows the further over budget the
    thread is, and it is sent as ONE prompt carrying the conversation's whole
    system prompt and tool schemas. If it overflows, the runner's overflow branch
    can only shrink `ctx.history` — which the compactor deliberately emptied — so
    it returns nothing, `summarise` yields "", and the thread silently falls back
    to the amputation this feature exists to remove. The failure mode is
    inverted: the fuller the thread, the less likely compaction works."""
    dump = _Msg("tool", "巨" * 20_000, tool_name="exec")
    msgs = [dump, *[_Msg("user", f"訊息{i}" * 100) for i in range(6)]]
    own = estimate_messages(msgs)

    _at, span = plan_for_budget(msgs, used=own + 1_000, budget=2_000, estimate=estimate_messages)
    assert span, "this one really is too big to fold away"
    bodies = "".join(getattr(m, "content", "") for m in span)
    assert "巨" * 100 not in bodies, "the dump must reach the summariser folded"
    assert "摺疊" in bodies, "and folded means the marker, not silence"


@pytest.mark.asyncio
async def test_the_summariser_is_offered_no_tools():
    """It has one job and no way to do anything else: the context it runs in has
    no sandbox, no filestore, no retriever. Leaving the conversation's tool set
    on it means a model can open with a tool call, produce no text at all, and
    `summarise` returns "" — which the caller reads as "no summary" and falls
    back to the old amputation. A silent no-op, from a tool that could never
    have worked. (Caught by review.)"""
    from workspace_app.resources.agent_config import AgentConfig

    runner = _Runner()
    base = AgentToolContext(
        investigation_id="i1",
        agent_config=AgentConfig(name="c", system_prompt="p", allowed_tools=["exec", "read_file"]),
    )
    await AgentCompactor(runner).summarise([_Msg("user", "很久以前")], ctx=base)

    assert runner.ctx is not None
    assert runner.ctx.agent_config is not None
    assert runner.ctx.agent_config.allowed_tools == [], "an empty list is an explicit 'no tools'"
    assert base.agent_config is not None
    assert base.agent_config.allowed_tools == ["exec", "read_file"], (
        "the caller's own config is untouched"
    )
