"""#613/#615: what the turn-end goal checker actually gets to see."""

from __future__ import annotations

from workspace_app.api.goal_checker import transcript_tail
from workspace_app.resources.conversation import Message


def test_the_drivers_own_rounds_are_left_out_of_the_verdict():
    # An auto-continue round is the driver re-asking, never evidence that the
    # goal was reached. Dropping it beats asking a small model to ignore lines
    # it can plainly see.
    tail = transcript_tail(
        [
            Message(role="user", content="write the report"),
            Message(role="assistant", content="starting"),
            Message(role="user", content="[goal] keep going", driven_by="goal"),
            Message(role="assistant", content="still working"),
        ]
    )
    assert "keep going" not in tail
    assert "write the report" in tail
    assert "still working" in tail


def test_only_the_conversation_roles_reach_the_checker():
    tail = transcript_tail(
        [
            Message(role="goal", content="目標已達成"),
            Message(role="error", content="boom"),
            Message(role="assistant", content="the report is written"),
        ]
    )
    assert tail == "assistant: the report is written"
