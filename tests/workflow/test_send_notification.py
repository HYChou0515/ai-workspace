"""P5/P8 (#435) — send_notification (M1 send-once): decide queries the send ledger by the
fingerprint {recipient}:{topic} (the Notification store IS the ledger), so a replay or a
revise that changes only the title never re-notifies about the same topic. P8 adds an
optional per-window fingerprint {recipient}:{topic}:{window} that buckets the run's creation
instant, so a daily-triggered notify sends once per window instead of once-ever. Handle-level
mechanism with fake hooks; the driver wiring over the real store is covered separately.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


def _win_wf(store, ledger: set[str], calls: list[str], *, run_id: str, day: int) -> WorkflowHandle:
    async def notify(recipient: str, title: str, body: str, dedup_key: str) -> str:
        calls.append(dedup_key)
        ledger.add(dedup_key)
        return f"n{len(calls)}"

    async def already(dedup_key: str) -> bool:
        return dedup_key in ledger

    return WorkflowHandle(
        store=store,
        workspace_id="ws",
        workflow_id="pm",
        notify=notify,
        notification_sent=already,
        run_id=run_id,
        run_started_at=datetime(2026, 7, day, 12, tzinfo=UTC),
    )


async def test_daily_window_sends_once_per_day_and_re_sends_the_next_day() -> None:
    """P8 — a per-window fingerprint buckets the run's creation day, so a daily notify sends
    once per day: a re-trigger the SAME day dedups via the ledger (the store IS the ledger),
    and the next day's run re-sends under a fresh window key. Crucially the SHARED journal
    (workflow_id-scoped) does NOT collapse the next day into the first — the per-invocation
    identity makes decide re-consult the ledger each invocation instead of journal-skipping."""
    ledger: set[str] = set()
    calls: list[str] = []
    store = MemoryFileStore()  # shared journal across the three invocations (production-faithful)

    r1 = await _win_wf(store, ledger, calls, run_id="run-A", day=5).send_notification(
        "bob", "digest", name="daily", window="daily"
    )
    assert r1["sent"] is True

    # same day, a re-trigger (distinct run) → the ledger already has today's window → skip
    r1b = await _win_wf(store, ledger, calls, run_id="run-B", day=5).send_notification(
        "bob", "digest", name="daily", window="daily"
    )
    assert r1b["sent"] is False

    # next day → a fresh window key → re-sends (not swallowed by the shared journal)
    r2 = await _win_wf(store, ledger, calls, run_id="run-C", day=6).send_notification(
        "bob", "digest", name="daily", window="daily"
    )
    assert r2["sent"] is True
    assert calls == ["bob:digest:2026-07-05", "bob:digest:2026-07-06"]


async def test_no_window_is_once_ever_across_days() -> None:
    """P8 — without a ``window`` the fingerprint stays once-ever ({recipient}:{topic}): even
    a run on a later day dedups against the first (the P5 default is preserved)."""
    ledger: set[str] = set()
    calls: list[str] = []
    store = MemoryFileStore()

    await _win_wf(store, ledger, calls, run_id="run-A", day=5).send_notification(
        "bob", "digest", name="daily"
    )
    r2 = await _win_wf(store, ledger, calls, run_id="run-B", day=6).send_notification(
        "bob", "digest", name="daily"
    )
    assert r2["sent"] is False  # once-ever: the later day still dedups
    assert calls == ["bob:digest"]
