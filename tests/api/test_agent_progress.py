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
