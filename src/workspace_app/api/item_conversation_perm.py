"""#306 PR3 — populate a Conversation's denormalized item-permission mirror.

A Conversation carries a copy of its owning item's read-visibility fields
(``item_visibility`` / ``item_read_chat`` / ``item_created_by``) so the
``conversation`` access_scope can gate reading the thread on the item's
``read_chat`` at the storage layer, without a cross-resource join — the #303
SourceDoc pattern, applied to the chat thread (which the item's own scope never
covers). This module is the single reader that turns an item into those mirror
kwargs, used at chat-create and in the fan-out that re-pushes them when the item's
permission / members change.
"""

from __future__ import annotations

from typing import Any

import msgspec
from specstar import SpecStar

from ..apps.registry import app_model
from ..apps.resolve import find_work_item
from ..resources.conversation import Conversation
from .chats import list_item_conversations


def item_conversation_mirror(spec: SpecStar, item_id: str) -> dict[str, Any]:
    """The ``item_*`` Conversation mirror kwargs for a chat under ``item_id``, read
    from the item's LIVE permission + owner. An item with no ``Permission`` ≡ public.
    A vanished item (created-then-deleted race) mirrors as public so an orphan thread
    never becomes unreadable. Always sets all three EXPLICITLY so a re-stamp after an
    item is loosened back to public resets a previously-restricted chat."""
    found = find_work_item(spec, item_id)
    if found is None:  # pragma: no cover — chats are created under a live item
        return {"item_visibility": "public", "item_read_chat": [], "item_created_by": ""}
    slug, item = found
    created_by = spec.get_resource_manager(app_model(slug)).get_meta(item_id).created_by
    perm = item.permission
    visibility = "public" if perm is None else perm.visibility
    read_chat = [] if perm is None else list(perm.read_chat)
    return {
        "item_visibility": visibility,
        "item_read_chat": read_chat,
        "item_created_by": created_by,
    }


def push_item_mirror_to_conversations(
    spec: SpecStar,
    item_id: str,
    *,
    visibility: str,
    read_chat: list[str],
    created_by: str,
) -> int:
    """Re-push the item's read-chat mirror onto every live Conversation of the item
    (the #303 fan-out). No bulk update, so a per-chat loop — run OFF the event loop
    by the caller. Runs as ``created_by`` (the item owner). A chat already carrying
    the target mirror is skipped so a no-op change doesn't churn revisions. Returns
    the number of conversations actually updated."""
    conv_rm = spec.get_resource_manager(Conversation)
    target = list(read_chat)
    updated = 0
    with conv_rm.using(created_by):
        for rid, conv in list_item_conversations(conv_rm, item_id):
            if (
                conv.item_visibility == visibility
                and conv.item_read_chat == target
                and conv.item_created_by == created_by
            ):
                continue
            conv_rm.update(
                rid,
                msgspec.structs.replace(
                    conv,
                    item_visibility=visibility,
                    item_read_chat=target,
                    item_created_by=created_by,
                ),
            )
            updated += 1
    return updated
