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


def list_item_conversations(conv_rm, item_id: str) -> list[tuple[str, Conversation]]:
    """``(resource_id, Conversation)`` for every chat of an item, by indexed
    ``item_id`` lookup (a bounded per-item set, not a global scan)."""
    out: list[tuple[str, Conversation]] = []
    for r in conv_rm.list_resources((QB["item_id"] == item_id).build()):
        data = r.data
        assert isinstance(data, Conversation)
        out.append((r.info.resource_id, data))
    return out


def resolve_default_conversation(conv_rm, item_id: str) -> tuple[str, Conversation]:
    """The item's default chat (manual §3): the earliest-born **free** chat, created
    if none exists. Workflow chats (``run_id`` set) are skipped; unstamped legacy rows
    (``created_ms is None``) sort first, so they stay the default."""
    free = [(rid, c) for rid, c in list_item_conversations(conv_rm, item_id) if c.run_id is None]
    if free:
        free.sort(key=lambda rc: (rc[1].created_ms if rc[1].created_ms is not None else -1, rc[0]))
        return free[0]
    rev = conv_rm.create(Conversation(item_id=item_id, created_ms=_now_ms()))
    got = conv_rm.get(rev.resource_id).data
    assert isinstance(got, Conversation)
    return rev.resource_id, got
