"""INotificationChannel — the seam a deploy plugs "and also send it somewhere" into.

`notify()` writes a row and the bell shows it. That is the whole of delivery
today, which is enough right up until somebody asks to be told when they are NOT
looking at the page — which is the entire point of letting a page schedule work.

The platform ships no implementation and never will. Which relay, which
from-address, which retention and compliance rules are the deploy's, exactly as
with ``IRequestEnv`` and ``IEnvProvider``. A deploy that names none behaves
precisely as it does today.

Two properties are load-bearing, and both are structural rather than a rule
somebody has to remember:

**The row is written first, and always.** It is the record of truth; the channel
carries a copy elsewhere. So `notify()` is untouched — it neither awaits a relay
nor learns that one exists. That also keeps network I/O off the caller's path,
which matters here more than it looks: `notify()` has both sync and async
callers, and this codebase has already had one incident from blocking I/O on the
event loop.

**A failing channel cannot fail anything else.** Delivery happens on a sweep,
completely decoupled from whatever produced the notification. A two-hour mail
outage leaves rows pending and they go out afterwards — it does not become "every
scheduled job in the company stopped itself", which is what happens when a
delivery failure counts as the job's failure.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import msgspec
from specstar import QB, SpecStar

from ..resources import Notification

logger = logging.getLogger(__name__)

#: How many times one row is offered to the channel before it is abandoned.
#:
#: Not every failure is transient — a malformed address never succeeds — and a
#: row retried forever is a sweep that grows without bound and a log nobody
#: reads. Giving up costs the OUTBOUND copy only: the in-app notification is
#: already there, and it was always the one that could not be lost.
MAX_ATTEMPTS = 3

#: How many pending rows one sweep will carry. A backlog drains over several
#: sweeps rather than making one of them unbounded.
BATCH = 200


@dataclass(frozen=True)
class OutboundNotification:
    """What a channel is handed. Deliberately not the stored row: an
    implementation has no business seeing `read`, `dedup_key` or the resource id,
    and freezing those into the seam would make every future field a decision
    about somebody else's mail server."""

    recipient: str
    """The platform user id. Turning that into an address is the deploy's job —
    only it knows the directory."""
    kind: str
    title: str
    body: str
    link: str
    """Where clicking goes, relative to the app. A channel that sends mail will
    want to make it absolute; only the deploy knows its own base URL."""


class INotificationChannel(abc.ABC):
    """Send one notification somewhere other than the bell.

    Resolved at startup from the ``server.notification_channel`` dotted path, so
    an implementation must be constructible with no arguments.
    """

    @abc.abstractmethod
    async def deliver(self, note: OutboundNotification) -> None:
        """Deliver it, or raise.

        Raising means "not delivered" and nothing more — the row stays pending
        and a later sweep offers it again, up to :data:`MAX_ATTEMPTS`. Nothing
        else is failed on your behalf.

        ``async`` because this is network I/O and it must never sit on the event
        loop. Bound your own latency: a sweep hands rows over one at a time, so
        a channel that hangs holds up everyone else's mail behind it.

        Deliver-at-least-once is the contract the platform can offer, not
        exactly-once: a pod dying between your successful send and the mark will
        offer the same row again. If duplicates matter to your medium, key on
        ``(recipient, title, link)`` or ask for a stable id to be added here.
        """
        ...


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


async def deliver_pending(spec: SpecStar, channel: INotificationChannel | None) -> int:
    """Hand every undelivered notification to the channel. Returns how many went.

    ``channel is None`` is the default for every deploy that has not opted in,
    and it costs nothing — not a query, not a write. A deploy without a channel
    cannot tell this function exists.
    """
    if channel is None:
        return 0

    rm = spec.get_resource_manager(Notification)
    # ONE indexed predicate. A row that is given up on moves to "failed" and so
    # leaves this set — without that the query grows forever and every sweep
    # re-reads rows nothing will ever do anything with.
    # `returns=["data", "info"]` because the id lives on `info`, and the sweep
    # needs both: the fields to send and the id to mark.
    # Bounded in the QUERY. Slicing a materialised list bounds the delivery and
    # not the read: every pending row would still be transferred and decoded,
    # every sweep, on every pod — and the case that makes the backlog large is
    # the one this module exists for, a multi-hour outage. So the expensive half
    # would scale with exactly the incident it is meant to survive.
    #
    # Offloaded because specstar is blocking I/O and this runs on the API's loop
    # (`project_api_sync_specstar_blocks_loop` / PR#657 is the same shape).
    pending = await asyncio.to_thread(
        lambda: list(
            rm.list_resources((QB["outbound"] == "").limit(BATCH).build(), returns=["data", "info"])
        )
    )

    sent = 0
    for res in pending:
        row = res.data
        if not isinstance(row, Notification):  # pragma: no cover — defensive
            continue
        nid: str = res.info.resource_id  # ty: ignore[unresolved-attribute]
        try:
            await channel.deliver(
                OutboundNotification(
                    recipient=row.recipient,
                    kind=row.kind,
                    title=row.title,
                    body=row.body,
                    link=row.link,
                )
            )
        except Exception:
            # Per-row resilience, the same rule the mirror and reaper sweeps
            # follow: one recipient's broken address must not hold up everyone
            # else's mail.
            logger.exception("notification %s could not be delivered", nid)
            tried = row.delivery_attempts + 1
            # Offloaded like the read. One per row, up to BATCH per sweep, every
            # 30s on every pod — moving only the query left the expensive half of
            # this loop exactly where it was.
            await asyncio.to_thread(
                rm.update,
                nid,
                msgspec.structs.replace(
                    row,
                    delivery_attempts=tried,
                    # Out of the pending set once it is hopeless. Not every
                    # failure is transient — a malformed address never succeeds
                    # — and the in-app row, the one that could not be lost, is
                    # already there.
                    outbound="failed" if tried >= MAX_ATTEMPTS else row.outbound,
                ),
            )
            continue
        await asyncio.to_thread(
            rm.update,
            nid,
            msgspec.structs.replace(
                row,
                outbound="sent",
                delivered_at=_now_ms(),
                delivery_attempts=row.delivery_attempts + 1,
            ),
        )
        sent += 1
    return sent
