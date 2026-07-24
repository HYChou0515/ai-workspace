"""#624: what to give up when the context does not fit.

One algorithm, behind an interface. The interface is not a menu — a second
implementation (summarising a span rather than giving it up) is foreseeable, and
that is what it is for. Shipping several selectable policies would mean shipping
several that never run anywhere, and therefore several that rot.

What the algorithm must do is give things up in increasing order of what they
cost, so the cheap sacrifice is always exhausted before an expensive one is
considered.
"""

from __future__ import annotations

import pytest

from workspace_app.context_budget import estimate_messages
from workspace_app.context_reduce import IContextReducer, ReductionResult
from workspace_app.context_reducers import default_reducer

TASK = "分析這批晶圓資料,做 SPC,寫成報告"


class _Msg:
    def __init__(self, role: str, content: str, tool_name: str | None = None) -> None:
        self.role = role
        self.content = content
        self.tool_name = tool_name
        self.tool_args = None

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"{self.role}:{self.content[:12]}"


def _thread() -> list[_Msg]:
    """A realistic work session: the task, then turns whose tool output dwarfs
    everything the humans actually said."""
    msgs = [_Msg("user", TASK)]
    for i in range(6):
        msgs += [
            _Msg("assistant", f"我先讀取第 {i} 批"),
            _Msg("tool", "x" * 8_000, tool_name="exec"),
            _Msg("assistant", f"第 {i} 批有 27 筆異常"),
            _Msg("user", f"那第 {i} 批的爐溫呢?"),
        ]
    return msgs


def _tokens(msgs) -> int:
    return estimate_messages(msgs)


def _humans(msgs) -> int:
    return len([m for m in msgs if getattr(m, "role", "") in ("user", "assistant")])


# ── the contract ────────────────────────────────────────────────────


def test_there_is_one_algorithm_behind_the_interface():
    """One implementation, not a menu. The interface earns its place from the
    second implementation that is coming, not from choices nobody makes."""
    assert isinstance(default_reducer(), IContextReducer)


def test_a_thread_that_fits_is_returned_untouched():
    """No budget pressure ⇒ nothing is given up, and no notice for a non-event."""
    msgs = _thread()
    result = default_reducer().reduce(msgs, budget=10**9, estimate=_tokens)

    assert result.messages == msgs
    assert result.changed is False


def test_a_reduction_explains_itself():
    """The user-facing notice renders this. "N messages dropped" would be a lie
    for the stage that drops nothing and folds instead."""
    result = default_reducer().reduce(_thread(), budget=6_000, estimate=_tokens)

    assert isinstance(result, ReductionResult)
    assert result.changed and result.summary


# ── the three stages, in order of what they cost ────────────────────


def test_stage_one_folds_tool_output_and_keeps_every_message():
    """The cheapest sacrifice, and by far the most effective: this six-turn
    session measures 12,153 tokens and folds to 279 with every message still
    present. That is why it runs first — it is usually enough by itself, so the
    expensive stages rarely run at all."""
    msgs = _thread()
    assert _tokens(msgs) > 12_000  # the session really is dominated by dumps

    result = default_reducer().reduce(msgs, budget=6_000, estimate=_tokens)

    assert len(result.messages) == len(msgs), "folding must not drop anything"
    assert _humans(result.messages) == _humans(msgs), "no conversation is given up"
    assert result.messages[0].content == TASK
    assert _tokens(result.messages) <= 6_000


def test_stage_two_gives_up_conversation_but_never_the_task():
    """Reached only when folding was not enough. The opening request is what
    every later turn refers back to; losing it is how a session becomes "the AI
    forgot what we were doing"."""
    msgs = _thread()
    result = default_reducer().reduce(msgs, budget=100, estimate=_tokens)

    assert result.messages[0].content == TASK
    assert len(result.messages) < len(msgs)
    assert _tokens(result.messages) <= 100


def test_stage_three_keeps_only_the_current_turn():
    """Last resort: not even the request plus this turn will fit. A turn without
    its own context produces an incoherent reply, so that is what survives."""
    msgs = _thread()
    result = default_reducer().reduce(msgs, budget=10, estimate=_tokens)

    assert result.messages == [msgs[-1]]


# ── invariants no stage may break ───────────────────────────────────


@pytest.mark.parametrize("budget", [10, 100, 1_500, 6_000])
def test_the_newest_message_always_survives(budget):
    msgs = _thread()
    result = default_reducer().reduce(msgs, budget=budget, estimate=_tokens)

    assert result.messages[-1] is msgs[-1]


@pytest.mark.parametrize("budget", [100, 1_500, 6_000])
def test_the_budget_is_respected(budget):
    result = default_reducer().reduce(_thread(), budget=budget, estimate=_tokens)

    assert _tokens(result.messages) <= budget


def test_it_works_on_sdk_history_items_too():
    """History reaches the retry path as plain dicts, not `Message` objects. An
    algorithm that understood only one shape would not fail — it would silently
    do nothing, and the caller would fall back to a blunter rule. That is
    precisely how a reduction once reported success while dropping the task."""
    history = [
        {"role": "user", "content": TASK},
        *[{"role": "tool", "content": "x" * 6_000} for _ in range(4)],
        {"role": "user", "content": "現在呢?"},
    ]

    result = default_reducer().reduce(history, budget=200, estimate=_tokens)

    assert result.changed
    assert _tokens(result.messages) <= 200
    assert any(TASK in m["content"] for m in result.messages)


def test_the_leading_messages_do_not_churn_as_the_thread_grows():
    """P2 originally asked for trimming in BLOCKS, so a provider's prefix cache
    could survive several turns — a window that slides by one message per turn
    invalidates it every turn.

    Folding delivers that, and more directly: it is a per-message, context-free
    transform, so a bulky tool output folds to the same text no matter what else
    is in the thread. The leading items stay byte-identical as the conversation
    grows; only the tail extends. A sliding window cannot do this by
    construction.
    """
    msgs = _thread()
    grown = [*msgs, _Msg("assistant", "再跑一批"), _Msg("tool", "y" * 8_000, tool_name="exec")]

    before = default_reducer().reduce(msgs, budget=6_000, estimate=_tokens)
    after = default_reducer().reduce(grown, budget=6_000, estimate=_tokens)

    assert [m.content for m in after.messages[: len(msgs)]] == [m.content for m in before.messages]
