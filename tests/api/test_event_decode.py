"""`event_from_dict` is the inverse of the `asdict` form `to_sse` serializes — the
cross-pod event bus ships events as JSON and must reconstruct the exact dataclass so
`to_sse` / the `isinstance(Presence)` seq logic keep working on the receiving pod.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from workspace_app.api.events import (
    AgentMetrics,
    FailoverSwitch,
    FileChanged,
    MessageDelta,
    RestoreProgress,
    RunDone,
    RunError,
    TodosUpdated,
    ToolEnd,
    ToolLog,
    ToolStart,
    UserMessage,
    event_from_dict,
)
from workspace_app.workflow.events import StepOutput

# A representative event per shape: bare, str, str+bool, str+dict, ints, workflow.
_SAMPLES = [
    RunDone(),
    MessageDelta(text="hello", reasoning=True),
    ToolStart(call_id="c1", name="grep", args={"q": "x"}),
    ToolEnd(call_id="c1", output="out", display="rich"),
    ToolLog(call_id="c1", text="log line"),
    RunError(message="boom"),
    AgentMetrics(phase="down", prompt_tokens=5, completion_tokens=3, elapsed_ms=120),
    FailoverSwitch(from_model="m1", reason="TimeoutError"),
    RestoreProgress(done=2, total=7),
    UserMessage(content="hi", author="alice"),
    FileChanged(path="/a.py", by="alice", kind="modified"),
    StepOutput(phase="commit", name="ingest", text="line\n", key="f.pdf"),
    # #613: the live todo-checklist update (list-of-dicts payload, JSON-native
    # so the cross-pod bus roundtrip reconstructs it exactly).
    TodosUpdated(items=[{"text": "fix the bug", "status": "in_progress"}]),
]


@pytest.mark.parametrize("event", _SAMPLES, ids=lambda e: type(e).__name__)
def test_event_survives_a_dict_round_trip(event):
    assert event_from_dict(asdict(event)) == event


def test_the_transport_seq_key_is_ignored():
    # `to_sse` injects `seq` into the payload; the decoder must drop it (not a field).
    payload = asdict(MessageDelta(text="hi"))
    payload["seq"] = 42
    assert event_from_dict(payload) == MessageDelta(text="hi")
