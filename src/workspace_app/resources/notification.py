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
    kind: str  # mention | share | status | (assignment|agent_done|system reserved)
    title: str
    body: str = ""
    link: str = ""  # where clicking goes, e.g. /a/{slug}/items/{id} or /kb/chats/{id}
    actor: str | None = None  # who triggered it (user id); None when system/agent
    read: bool = False
    created_at: int | None = None  # epoch ms
