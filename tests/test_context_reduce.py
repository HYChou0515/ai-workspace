"""#624: what to give up when the context does not fit is a POLICY, not an
algorithm baked into the request path.

The first implementation hardcoded "drop the oldest messages" everywhere —
`_fit_token_budget` keeps the newest suffix, `halve_history` drops the older
half, the notice says "較早的 N 則訊息", the event fields are `kept`/`dropped`.
That is one option among several, and on the measured evidence it is close to
the worst one: it throws away the FIRST user message (usually the task itself)
before it touches a single 30 KB tool dump that cost 20x more budget.

These pin the seam, not a choice: several strategies exist, they are selected by
name, and an unknown name fails loudly instead of quietly picking one.
"""

from __future__ import annotations

import pytest

from workspace_app.context_reduce import IContextReducer, ReductionResult
from workspace_app.context_reducers import REDUCERS, get_reducer


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
    msgs = [_Msg("user", "分析這批晶圓資料,做 SPC,寫成報告")]
    for i in range(6):
        msgs += [
            _Msg("assistant", f"我先讀取第 {i} 批"),
            _Msg("tool", "x" * 8_000, tool_name="exec"),
            _Msg("assistant", f"第 {i} 批有 27 筆異常"),
            _Msg("user", f"那第 {i} 批的爐溫呢?"),
        ]
    return msgs


def _tokens(msgs) -> int:
    from workspace_app.context_budget import estimate_messages

    return estimate_messages(msgs)


# ── the seam ────────────────────────────────────────────────────────


def test_several_strategies_are_registered():
    """A single registered strategy would mean the choice is still hardcoded,
    just relocated."""
    assert len(REDUCERS) >= 2
    assert all(isinstance(get_reducer(name), IContextReducer) for name in REDUCERS)


def test_an_unknown_strategy_fails_loudly():
    """Silently falling back would recreate the defect: a policy nobody chose,
    applied without anyone knowing."""
    with pytest.raises(ValueError, match="unknown"):
        get_reducer("no-such-strategy")


def test_a_reducer_reports_what_it_did_not_just_a_count():
    """The notice has to describe what actually happened. "N messages dropped"
    is only meaningful for one of the strategies."""
    result = get_reducer("drop-oldest").reduce(_thread(), budget=2_000, estimate=_tokens)

    assert isinstance(result, ReductionResult)
    assert result.messages
    assert result.summary, "a reduction must be able to explain itself"


def test_a_thread_that_fits_is_returned_untouched():
    """No budget pressure ⇒ no policy applies, whichever one is selected."""
    msgs = _thread()
    for name in REDUCERS:
        result = get_reducer(name).reduce(msgs, budget=10**9, estimate=_tokens)
        assert result.messages == msgs, name
        assert result.changed is False, name


# ── the strategies differ in what they give up ──────────────────────


def test_drop_oldest_is_the_incumbent_behaviour():
    """Kept as a named strategy so switching the seam in changes nothing by
    itself — the behaviour is now a choice, not an assumption."""
    result = get_reducer("drop-oldest").reduce(_thread(), budget=3_000, estimate=_tokens)

    assert result.changed
    assert _tokens(result.messages) <= 3_000
    # It sacrifices the task statement first — the property that makes it a poor
    # default, pinned here so the trade-off is visible rather than implicit.
    assert result.messages[0].content != "分析這批晶圓資料,做 SPC,寫成報告"


def test_elide_tool_output_spends_the_budget_on_dialogue():
    """The measured case: one 30 KB `exec` dump costs more than twenty turns of
    conversation. Shrinking the dump keeps far more of the actual session."""
    msgs = _thread()
    dropped = get_reducer("drop-oldest").reduce(msgs, budget=3_000, estimate=_tokens)
    elided = get_reducer("elide-tool-output").reduce(msgs, budget=3_000, estimate=_tokens)

    assert _tokens(elided.messages) <= 3_000
    human = lambda r: len([m for m in r.messages if m.role in ("user", "assistant")])  # noqa: E731
    assert human(elided) > human(dropped), "eliding dumps must retain more dialogue"


def test_keep_task_never_sacrifices_the_first_user_message():
    """The task statement is what every later turn refers back to; losing it is
    how a session becomes "the AI forgot what we were doing"."""
    msgs = _thread()
    result = get_reducer("keep-task").reduce(msgs, budget=2_000, estimate=_tokens)

    assert result.messages[0].content == "分析這批晶圓資料,做 SPC,寫成報告"
    assert _tokens(result.messages) <= 2_000


@pytest.mark.parametrize("name", ["drop-oldest", "elide-tool-output", "keep-task"])
def test_every_strategy_respects_the_budget_and_keeps_the_newest(name):
    """Two invariants no policy may break: fit the budget, and never drop the
    turn's own most recent context."""
    msgs = _thread()
    result = get_reducer(name).reduce(msgs, budget=1_500, estimate=_tokens)

    assert result.messages, name
    assert result.messages[-1] is msgs[-1], name
    assert _tokens(result.messages) <= 1_500 or len(result.messages) == 1, name


# ── the default: decided on the evidence, not left open ─────────────


def test_the_default_protects_both_the_task_and_the_dialogue():
    """#624's measurements point at one answer, so the default is that answer.

    A 30 KB `exec` dump costs ~20 turns of dialogue, and the opening request is
    what every later turn refers back to. So: fold the bulky OLD dumps first
    (they are the bulk and the least worth re-reading verbatim), and if that is
    still not enough, drop from the middle rather than sacrifice the task.
    Shipping `drop-oldest` as the default would mean shipping the behaviour this
    issue exists to fix."""
    from workspace_app.context_reducers import DEFAULT_REDUCER

    msgs = _thread()
    result = get_reducer(DEFAULT_REDUCER).reduce(msgs, budget=2_500, estimate=_tokens)

    assert result.messages[0].content == "分析這批晶圓資料,做 SPC,寫成報告"
    assert _tokens(result.messages) <= 2_500
    human = len([m for m in result.messages if m.role in ("user", "assistant")])
    incumbent = get_reducer("drop-oldest").reduce(msgs, budget=2_500, estimate=_tokens)
    assert human > len([m for m in incumbent.messages if m.role in ("user", "assistant")])


def test_the_incumbent_is_still_one_config_line_away():
    """Choosing a default must not remove the choice — that was the original
    defect."""
    from workspace_app.context_reducers import REDUCERS

    assert "drop-oldest" in REDUCERS
