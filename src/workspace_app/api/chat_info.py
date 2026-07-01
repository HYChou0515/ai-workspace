"""``_ChatInfo`` assembly (#54) for an item's multi-chat list (manual §3, #132).

Builds the list-item shape the chats routes return and the workflow run route stamps
onto a fresh workflow chat, plus the bounded ``run_id → status`` lookup that gives
each workflow chat its badge without per-run polling. Shared so the chats routes and
the workflow run route don't each restate it.
"""

from __future__ import annotations

from typing import Any

from specstar import QB, SpecStar

from ..resources import Conversation
from ..workflow.run import WorkflowRun
from .chat_naming import first_user_snippet
from .schemas import _ChatInfo
from .timeutil import dt_ms


def chat_name_hint(conv: Conversation, *, limit: int = 60) -> str:
    """First user message of a chat, whitespace-collapsed and truncated — the FE's
    fallback display name for an unnamed chat (#132). "" when no user turn yet."""
    return first_user_snippet(conv.messages, limit=limit)


def chat_info(
    chat_id: str,
    conv: Conversation,
    default_id: str | None,
    *,
    status: str | None = None,
    last_activity_ms: int | None = None,
) -> _ChatInfo:
    return _ChatInfo(
        chat_id=chat_id,
        title=conv.title,
        run_id=conv.run_id,
        created_ms=conv.created_ms,
        message_count=len(conv.messages),
        is_default=chat_id == default_id,
        name_hint=chat_name_hint(conv),
        status=status,
        last_activity_ms=last_activity_ms,
    )


def chat_info_from_resource(
    r: Any, default_id: str | None, run_status: dict[str, str]
) -> _ChatInfo:
    """Build a `_ChatInfo` from a fetched conversation resource — fills the run
    status + last-activity stamp the bare `chat_info` can't derive (#132)."""
    conv = r.data
    assert isinstance(conv, Conversation)
    return chat_info(
        r.info.resource_id,
        conv,
        default_id,
        status=run_status.get(conv.run_id) if conv.run_id else None,
        last_activity_ms=dt_ms(r.info.updated_time),
    )


def item_run_status(spec: SpecStar, investigation_id: str) -> dict[str, str]:
    """``run_id → status`` for the item's workflow runs — one bounded per-item
    query so the chat list shows each workflow chat's badge without polling each
    run (#132)."""
    out: dict[str, str] = {}
    for r in spec.get_resource_manager(WorkflowRun).list_resources(
        (QB["item_id"] == investigation_id).build()
    ):
        # The query is on the WorkflowRun manager, so every row IS a WorkflowRun;
        # the isinstance is a typing narrowing guard, never False at runtime.
        if isinstance(r.data, WorkflowRun):  # pragma: no branch
            out[r.info.resource_id] = str(r.data.status)  # ty: ignore[unresolved-attribute]
    return out
