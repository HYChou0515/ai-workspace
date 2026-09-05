"""Notification — a per-user "this is for you, come look" item.

Distinct from the global activity log (which records *what happened*);
a Notification is *addressed to a recipient* and tracked read/unread. The bell
dropdown shows the recipient's recent notifications. Produced by: status
changes, chat shares, @mentions (this batch); assignment / agent-done / system
kinds are reserved for later.
"""

from __future__ import annotations

from msgspec import Struct


class Notification(Struct):  # → resource "notification"
    recipient: str  # user id this is addressed to
    kind: str  # mention | share | status | access_request | (assignment|agent_done|system reserved)
    title: str
    body: str = ""
    link: str = ""  # where clicking goes, e.g. /a/{slug}/items/{id} or /kb/chats/{id}
    actor: str | None = None  # who triggered it (user id); None when system/agent
    read: bool = False
    created_at: int | None = None  # epoch ms
    outbound: str = ""
    # The OUTBOUND copy's state, not the notification's — the row itself is
    # delivered the moment it is written, and the bell shows it either way.
    # "" = pending, "sent", "failed" (given up on). INDEXED, because the sweep
    # asks for exactly the pending ones.
    #
    # A state rather than a sentinel inside `delivered_at`: a permanently
    # undeliverable row has to LEAVE the pending set, or the sweep's query grows
    # without bound and every pass re-reads rows it has already given up on.
    #
    # ⚠️ Old rows, written before this field was indexed, match no value and are
    # therefore never swept. That is deliberate and worth keeping: wiring a
    # channel for the first time must not mail out every notification the
    # platform has ever produced.
    delivered_at: int = 0
    # Epoch ms when a channel took it, 0 otherwise. Not indexed — nothing filters
    # on it; it is there so an operator can see WHEN.
    delivery_attempts: int = 0
    # How many times a channel was offered this row. Bounded by
    # `notification_delivery.MAX_ATTEMPTS`.
    #
    # Only `api.notification_delivery.deliver_pending` writes these three. They
    # are deliberately absent from `notify()`'s signature — a producer decides
    # WHAT to say, never how far it got.
    dedup_key: str = ""  # #435 P5: send-once fingerprint ({recipient}:{topic}[:window]) so
    # a workflow's send_notification capability can query "already sent?" — the store is the
    # ledger (M1). Empty on notifications not produced by a deduped sender.
