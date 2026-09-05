"""The outbound half of a notification.

`notify()` writes a row and the bell shows it. That is the whole of delivery
today, which is fine until somebody asks to be told when they are not looking at
the page — the case the whole scheduling design exists for ("寄信給我").

The platform ships no implementation. Which relay, which from-address, which
compliance rules are the deploy's, exactly as with `IRequestEnv` and
`IEnvProvider`, so this is a seam and nothing more.

Two properties matter more than the plumbing:

* **The row is written first and always.** It is the record of truth; the
  channel is a copy going somewhere else.
* **A failing channel cannot fail anything else.** A two-hour mail outage must
  not turn into "every scheduled job in the company stopped itself", which is
  what happens if delivery failure counts as the job's failure.
"""

from __future__ import annotations

import asyncio

import pytest
from specstar import SpecStar

from workspace_app.api.notification_delivery import (
    INotificationChannel,
    OutboundNotification,
    deliver_pending,
)
from workspace_app.api.notifications import notify
from workspace_app.resources import Notification, make_spec


@pytest.fixture
def spec() -> SpecStar:
    return make_spec(default_user="alice")


class _Channel(INotificationChannel):
    """A deploy's relay, as a double. Records what it was handed."""

    def __init__(self, fail: bool = False):
        self.sent: list[OutboundNotification] = []
        self.fail = fail

    async def deliver(self, note: OutboundNotification) -> None:
        self.sent.append(note)
        if self.fail:
            raise RuntimeError("the relay refused")


def _one(spec: SpecStar, recipient: str = "alice") -> str:
    # Explicit parameters, not a `**dict[str, object]`: a shared kwargs dict
    # silently costs the type check on every value it carries.
    return notify(
        spec,
        recipient=recipient,
        kind="status",
        title="Your report is ready",
        body="12 lots",
        link="/a/rca/items/i1",
    )


def _row(spec: SpecStar, nid: str) -> Notification:
    data = spec.get_resource_manager(Notification).get(nid).data
    assert isinstance(data, Notification)
    return data


def test_the_row_is_written_whether_or_not_a_channel_exists(spec: SpecStar):
    """The in-app row is the record of truth. A deploy that named no channel
    behaves exactly as it does today — this seam adds a copy, it does not move
    where the truth lives."""
    nid = _one(spec)

    assert _row(spec, nid).title == "Your report is ready"
    assert _row(spec, nid).delivered_at == 0


def test_a_pending_row_is_handed_to_the_channel_and_marked(spec: SpecStar):
    nid = _one(spec)
    channel = _Channel()

    asyncio.run(deliver_pending(spec, channel))

    assert [n.title for n in channel.sent] == ["Your report is ready"]
    assert channel.sent[0].recipient == "alice"
    assert _row(spec, nid).delivered_at > 0


def test_a_delivered_row_is_never_handed_over_twice(spec: SpecStar):
    """The sweep runs on a timer and on every pod. Without the mark, every sweep
    re-sends everything ever written."""
    _one(spec)
    channel = _Channel()

    asyncio.run(deliver_pending(spec, channel))
    asyncio.run(deliver_pending(spec, channel))

    assert len(channel.sent) == 1


def test_a_failing_channel_leaves_the_row_undelivered_and_raises_nothing(spec: SpecStar):
    """A mail outage must not propagate. The row stays pending, so the next
    sweep tries again once the relay is back — no work is lost and nothing else
    is failed on its behalf."""
    nid = _one(spec)
    channel = _Channel(fail=True)

    asyncio.run(deliver_pending(spec, channel))  # must not raise

    assert _row(spec, nid).delivered_at == 0
    assert _row(spec, nid).delivery_attempts == 1


def test_a_row_that_cannot_be_delivered_is_given_up_on(spec: SpecStar):
    """Not every failure is transient — a malformed address never succeeds. A
    row retried forever is a sweep that grows without bound and a log nobody
    reads; giving up leaves the in-app notification, which was always the copy
    that mattered."""
    _one(spec)
    channel = _Channel(fail=True)

    for _ in range(6):
        asyncio.run(deliver_pending(spec, channel))

    assert len(channel.sent) == 3


def test_one_bad_row_does_not_stop_the_others(spec: SpecStar):
    """Per-item resilience, the same rule the mirror and reaper sweeps follow:
    one recipient's broken address must not hold up everyone else's mail."""

    class _Picky(INotificationChannel):
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def deliver(self, note: OutboundNotification) -> None:
            if note.recipient == "bob":
                raise RuntimeError("no address for bob")
            self.sent.append(note.recipient)

    _one(spec, "alice")
    _one(spec, "bob")
    _one(spec, "carol")
    channel = _Picky()

    asyncio.run(deliver_pending(spec, channel))

    assert sorted(channel.sent) == ["alice", "carol"]


def test_nothing_is_swept_when_the_deploy_named_no_channel(spec: SpecStar):
    """`None` is the default and must cost nothing — not a query, not a mark.
    A deploy that never opted in should not even be able to tell this exists."""
    nid = _one(spec)

    asyncio.run(deliver_pending(spec, None))

    assert _row(spec, nid).delivered_at == 0
    assert _row(spec, nid).delivery_attempts == 0
