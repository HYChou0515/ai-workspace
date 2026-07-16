"""Multi-chat resolution (Phase 6, manual §3) — many `Conversation`s per item.

An item holds several chats: *free chats* (human-driven, ``run_id is None``) and
*workflow chats* (a `WorkflowRun` drives the turns, ``run_id`` set). Item-level
endpoints that carry no ``chat_id`` (``/messages``, ``/stream``, cancel, undo) operate
on an implicit **default chat** — the earliest-born free chat, created on first use.

Resolution is migration-free: a conversation written before multi-chat has no
``created_ms`` stamp, so it sorts before every stamped chat and stays the default even
once newer free chats are added. Workflow chats are never the default.
"""

from __future__ import annotations

import time

from specstar import QB

from ..resources.conversation import Conversation


def _now_ms() -> int:
    return int(time.time() * 1000)


def _item_chats_query(item_id: str):
    """Indexed lookup of an item's **live** chats — its ``item_id`` rows minus any
    soft-deleted ones (#132), so a deleted chat never resurfaces (as the default or
    in the list)."""
    return ((QB["item_id"] == item_id) & (QB.is_deleted() == False)).build()  # noqa: E712


def list_item_conversations(conv_rm, item_id: str) -> list[tuple[str, Conversation]]:
    """``(resource_id, Conversation)`` for every live chat of an item, by indexed
    ``item_id`` lookup (a bounded per-item set, not a global scan)."""
    out: list[tuple[str, Conversation]] = []
    for r in conv_rm.list_resources(_item_chats_query(item_id)):
        data = r.data
        assert isinstance(data, Conversation)
        out.append((r.info.resource_id, data))
    return out


def find_default_conversation(conv_rm, item_id: str) -> tuple[str, Conversation] | None:
    """The item's default chat (manual §3) — the earliest-born **free** chat — or
    ``None`` if the item has no free chat yet. Read-only (never creates): workflow
    chats (``run_id`` set) are skipped; unstamped legacy rows (``created_ms is None``)
    sort first, so they stay the default."""
    free = [(rid, c) for rid, c in list_item_conversations(conv_rm, item_id) if c.run_id is None]
    if not free:
        return None
    free.sort(key=lambda rc: (rc[1].created_ms if rc[1].created_ms is not None else -1, rc[0]))
    return free[0]


def resolve_default_conversation(
    conv_rm, item_id: str, *, mirror: dict | None = None
) -> tuple[str, Conversation]:
    """Like :func:`find_default_conversation` but **creates** the default free chat
    when the item has none — for the item-level endpoints' get-or-create semantics.
    ``mirror`` (#306 PR3) stamps the item read-chat mirror on the created chat so its
    access_scope gates the thread; ``None`` leaves the public defaults (a caller
    without a spec handle)."""
    found = find_default_conversation(conv_rm, item_id)
    if found is not None:
        return found
    rev = conv_rm.create(Conversation(item_id=item_id, created_ms=_now_ms(), **(mirror or {})))
    got = conv_rm.get(rev.resource_id).data
    assert isinstance(got, Conversation)
    return rev.resource_id, got
