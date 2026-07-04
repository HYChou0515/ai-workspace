"""P5 (#435) DSL surface for send_notification: recipient/topic carried in ``args`` (both
required) + a ``name`` site; runs through the interpreter over the wired capability."""

from __future__ import annotations

import json

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.dsl import build_run, parse_def, validate_def
from workspace_app.workflow.handle import WorkflowHandle


def _errs(steps: list[dict]) -> list[str]:  # type: ignore[type-arg]
    d = parse_def(json.dumps({"id": "wf", "phases": [{"id": "p"}], "steps": steps}))
    return validate_def(d)


def test_send_notification_needs_recipient_topic_in_args_and_a_name() -> None:
    errs = _errs(
        [{"type": "capability", "call": "send_notification", "phase": "p", "args": {"title": "hi"}}]
    )
    assert any("needs 'recipient' in 'args'" in e for e in errs)
    assert any("needs 'topic' in 'args'" in e for e in errs)
    assert any("needs a 'name'" in e for e in errs)


def test_send_notification_valid_def_is_empty() -> None:
    assert (
        _errs(
            [
                {
                    "type": "capability",
                    "call": "send_notification",
                    "phase": "p",
                    "name": "notify",
                    "args": {"recipient": "bob", "topic": "done", "title": "Done"},
                }
            ]
        )
        == []
    )


async def test_send_notification_runs_through_the_interpreter() -> None:
    calls: list[str] = []

    async def notify(recipient: str, title: str, body: str, dedup_key: str) -> str:
        calls.append(dedup_key)
        return "n1"

    async def already(_dedup_key: str) -> bool:
        return False

    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", notify=notify)
    wf._notification_sent = already  # type: ignore[attr-defined]
    d = parse_def(
        json.dumps(
            {
                "id": "wf",
                "phases": [{"id": "p"}],
                "steps": [
                    {
                        "type": "capability",
                        "call": "send_notification",
                        "phase": "p",
                        "name": "notify",
                        "args": {"recipient": "{inputs.who}", "topic": "done", "title": "Done"},
                    }
                ],
            }
        )
    )
    assert await build_run(d)(wf, {"who": "bob"}) == {"status": "done"}
    assert calls == ["bob:done"]  # recipient interpolated, fingerprint composed
