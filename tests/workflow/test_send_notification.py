"""P5 (#435) — send_notification (M1 send-once): decide queries the send ledger by the
fingerprint {recipient}:{topic} (the Notification store IS the ledger), so a replay or a
revise that changes only the title never re-notifies about the same topic. Handle-level
mechanism with fake hooks; the driver wiring over the real store is covered separately.
"""

from __future__ import annotations

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.handle import WorkflowHandle


def _wf(sent: set[str] | None) -> tuple[WorkflowHandle, list[str]]:
    calls: list[str] = []

    async def notify(recipient: str, title: str, body: str, dedup_key: str) -> str:
        calls.append(dedup_key)
        if sent is not None:
            sent.add(dedup_key)
        return f"n{len(calls)}"

    async def already(dedup_key: str) -> bool:
        return sent is not None and dedup_key in sent

    wf = WorkflowHandle(
        store=MemoryFileStore(),
        workspace_id="ws",
        workflow_id="pm",
        notify=notify,
        notification_sent=already if sent is not None else None,
    )
    return wf, calls


async def test_sends_once_then_a_revise_of_the_same_topic_skips() -> None:
    ledger: set[str] = set()
    wf, calls = _wf(ledger)

    r1 = await wf.send_notification(
        "bob", "issue-5-overdue", name="notify", title="Issue #5 overdue"
    )
    assert r1 == {"sent": True, "action": "send", "notification_id": "n1"}

    # a revise: same recipient+topic, only the title changed → fingerprint dedup → skip
    r2 = await wf.send_notification(
        "bob", "issue-5-overdue", name="notify", title="Issue #5 STILL overdue"
    )
    assert r2 == {"sent": False, "action": "skip", "notification_id": ""}
    assert calls == ["bob:issue-5-overdue"]  # notify fired exactly once


async def test_different_topics_send_separately() -> None:
    ledger: set[str] = set()
    wf, calls = _wf(ledger)
    await wf.send_notification("bob", "topic-a", name="notify")
    await wf.send_notification("bob", "topic-b", name="notify")
    assert calls == ["bob:topic-a", "bob:topic-b"]  # distinct fingerprints, both sent


async def test_no_ledger_hook_always_sends() -> None:
    wf, calls = _wf(None)  # no dedup query wired
    await wf.send_notification("bob", "t", name="n1", title="a")
    assert calls == ["bob:t"]  # sent (dedup inert without the ledger query)


async def test_unwired_notify_raises() -> None:
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws")
    with pytest.raises(RuntimeError):
        await wf.send_notification("bob", "t", name="n")
