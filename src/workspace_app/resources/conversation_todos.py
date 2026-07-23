"""Per-conversation agent todo list (#613).

The agent's ``update_todos`` tool whole-overwrites this row (Claude-style
TodoWrite semantics); the FE renders it as the pinned checklist panel next to
the chat. ``resource_id == conversation_id``, so every pod reads/writes the one
shared row by a point key (no scan) — the same pattern as
``api.sandbox_activity._SandboxActivity``.

Registered post-``spec.apply`` via ``register_conversation_todos`` (NOT in
``_register_all``), so specstar does not mount bare auto-CRUD routes for it:
FE reads/writes go through dedicated, permission-gated endpoints instead
(#613 P2) — a new open route family was #607's defect class.
"""

from __future__ import annotations

import contextlib

from msgspec import Struct, field
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

TODO_STATUSES = ("pending", "in_progress", "completed")


class TodoItem(Struct):
    text: str
    status: str
    """One of `pending` / `in_progress` / `completed` (TODO_STATUSES)."""


class ConversationTodos(Struct):
    conversation_id: str
    items: list[TodoItem] = field(default_factory=list)


def register_conversation_todos(spec: SpecStar) -> None:
    """Idempotently register the todos model. Safe to call on every pod."""
    with contextlib.suppress(ValueError):
        spec.add_model(ConversationTodos)


def upsert_todos(
    spec: SpecStar, conversation_id: str, items: list[TodoItem], *, user: str = ""
) -> None:
    """Whole-overwrite the conversation's todo list (create the row on first
    write, restore it if the conversation was soft-deleted and came back).
    Draft-status writes keep the frequent overwrites out of revision history,
    mirroring the activity heartbeat."""
    rm = spec.get_resource_manager(ConversationTodos)
    rec = ConversationTodos(conversation_id=conversation_id, items=list(items))
    attribution = rm.using(user=user) if user else contextlib.nullcontext()
    with attribution:
        try:
            rm.modify(conversation_id, rec, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            pass
        except ResourceIsDeletedError:
            rm.restore(conversation_id)
            rm.modify(conversation_id, rec, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(rec, resource_id=conversation_id, status=RevisionStatus.draft)


def clear_todos(spec: SpecStar, conversation_id: str) -> None:
    """Drop the todos row (the chat was deleted). Idempotent."""
    rm = spec.get_resource_manager(ConversationTodos)
    with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
        rm.delete(conversation_id)


def read_todos(spec: SpecStar, conversation_id: str) -> ConversationTodos | None:
    """The conversation's current todo list, or None when none was ever written."""
    rm = spec.get_resource_manager(ConversationTodos)
    try:
        res = rm.get(conversation_id)
    except (ResourceIDNotFoundError, ResourceIsDeletedError):
        return None
    data = res.data
    assert isinstance(data, ConversationTodos)  # narrow Struct|Unset for ty
    return data
