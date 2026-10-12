"""`progress_line` — rendering a sub-agent's event as one live line under the
parent's tool card. Shared by every delegation path (`ask_knowledge_base` and
`run_agent`), because a user watching a card should not have to learn two
vocabularies for the same thing.
"""

from __future__ import annotations

from workspace_app.api.agent_progress import progress_line
from workspace_app.api.events import MessageDelta, RunDone, ToolEnd, ToolLog, ToolStart


def test_a_search_and_an_action_read_differently_at_a_glance():
    assert (
        progress_line(ToolStart(call_id="a", name="kb_search", args={"query": "voids"}))
        == "🔎 kb_search: voids\n"
    )
    assert (
        progress_line(ToolStart(call_id="a", name="read_file", args={"path": "/app.log"}))
        == "🔧 read_file: /app.log\n"
    )
    assert progress_line(ToolStart(call_id="b", name="kb_search", args={})) == "🔎 kb_search\n"


def test_only_work_in_progress_surfaces_not_the_answer():
    """The answer is the tool's RESULT — repeating it as progress would show the
    user the same text twice."""
    assert progress_line(ToolLog(call_id="a", text="↻ rerank\n")) == "↻ rerank\n"
    assert progress_line(MessageDelta(text="weighing it", reasoning=True)) == "weighing it"
    assert progress_line(MessageDelta(text="the answer")) is None
    assert progress_line(ToolEnd(call_id="a", output="x")) is None
    assert progress_line(RunDone()) is None


def test_a_held_or_retrying_sub_agent_says_so_on_the_turn_not_as_a_stuck_card():
    """docs/plan-friendly-errors.md P4: a sub-agent waiting out a 429 used to look
    like a tool that hung. The hold reaches the PARENT turn's status line — the
    same "rate limited, N s" a held turn of its own shows — while the work lines
    still go under the card, and the run's failure goes back to the tool."""
    from workspace_app.agent.delegation import Delegation
    from workspace_app.api.agent_progress import make_relay
    from workspace_app.api.events import ModelRetrying, RateLimited, RunError

    lines: list[bytes] = []
    status: list[object] = []
    failures: list[object] = []
    relay = make_relay(Delegation(lines.append, status=status.append, on_failure=failures.append))

    failure = RunError(message="RateLimitError: slow", kind="rate_limited")
    for ev in (
        ToolStart(call_id="a", name="kb_search", args={"query": "voids"}),
        RateLimited(seconds=5),
        ModelRetrying(attempt=1, kind="timeout"),
        MessageDelta(text="the answer"),
        failure,
    ):
        relay(ev)

    assert lines == ["🔎 kb_search: voids\n".encode()]
    assert status == [RateLimited(seconds=5), ModelRetrying(attempt=1, kind="timeout")]
    assert failures == [failure]


def test_a_plain_sink_still_gets_its_lines_and_nothing_else():
    """Not every caller hands down a Delegation (an operator-side run, a test):
    a plain output sink keeps working exactly as before."""
    from workspace_app.api.agent_progress import make_relay
    from workspace_app.api.events import RateLimited, RunError

    lines: list[bytes] = []
    relay = make_relay(lines.append)
    relay(RateLimited(seconds=5))
    relay(RunError(message="x"))
    relay(ToolStart(call_id="a", name="kb_search", args={}))
    make_relay(None)(ToolStart(call_id="a", name="kb_search", args={}))

    assert lines == ["🔎 kb_search\n".encode()]
