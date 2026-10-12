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


def _item_runs(spec: SpecStar, investigation_id: str) -> list[tuple[str, WorkflowRun]]:
    """The item's workflow runs — one bounded per-item query (#132)."""
    out: list[tuple[str, WorkflowRun]] = []
    for r in spec.get_resource_manager(WorkflowRun).list_resources(
        (QB["item_id"] == investigation_id).build()
    ):
        # The query is on the WorkflowRun manager, so every row IS a WorkflowRun;
        # the isinstance is a typing narrowing guard, never False at runtime.
        if isinstance(r.data, WorkflowRun):  # pragma: no branch
            out.append((r.info.resource_id, r.data))  # ty: ignore[unresolved-attribute]
    return out


def item_run_status(
    spec: SpecStar, investigation_id: str, runs: list[tuple[str, WorkflowRun]] | None = None
) -> dict[str, str]:
    """``run_id → status`` for the item's workflow runs, so the chat list shows a
    chat's run badge without polling each run (#132)."""
    rows = _item_runs(spec, investigation_id) if runs is None else runs
    return {rid: str(run.status) for rid, run in rows}


def is_chat(conv: Conversation) -> bool:
    """Whether the chat list shows this conversation (plan-workflow-out-of-chat
    D3 / D4). One with no run is a chat. One with a run is a chat only if a person
    spoke in it — a chat #343 took over holds their own words, and under #343 a
    person could chat on after a run ended. A run's own conversation (opened by
    its launch, or a schedule's) holds none. A person's message is a `user` one
    no driver sent (a goal round, a workflow step's prompt) and no run marked."""
    if conv.run_id is None:
        return True
    return any(m.role == "user" and m.driven_by is None and m.run_id is None for m in conv.messages)


def is_chat_resource(r: Any) -> bool:
    """`is_chat` of a fetched conversation resource, as the chat list holds it."""
    conv = r.data
    assert isinstance(conv, Conversation)
    return is_chat(conv)
