"""Per-conversation goal — a user-set completion condition (#613 P3).

The user sets one goal per chat; after each turn a cheap LLM checks whether the
condition holds, and while it doesn't the chat auto-continues, bounded by
``goal.max_rounds`` (work-hours semantics; off-hours autonomy is #615).
``resource_id == conversation_id`` — the same point-key single row as
``ConversationTodos``, registered post-``spec.apply`` (no bare CRUD routes;
the gated ``/goal`` chat routes are the only wire surface).
"""

from __future__ import annotations

import contextlib

from msgspec import Struct
from specstar import SpecStar
from specstar.types import (
    DuplicateResourceError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

GOAL_STATES = ("active", "met", "exhausted")


class ConversationGoal(Struct):
    conversation_id: str
    condition: str
    """The user's completion condition, in their own words."""

    set_by: str
    """Who set the goal — auto-continue turns run as this user."""

    rounds_used: int = 0
    """Auto-continue turns spent on this goal (bumped BEFORE each one is
    enqueued, so a crash can't reset the count and loop forever)."""

    state: str = "active"
    """One of `active` / `met` / `exhausted` (GOAL_STATES). Terminal states
    stay readable so the panel can show the outcome; setting a new goal
    overwrites the row back to `active`."""


def register_conversation_goal(spec: SpecStar) -> None:
    """Idempotently register the goal model. Safe to call on every pod."""
    with contextlib.suppress(ValueError):
        spec.add_model(ConversationGoal)


def upsert_goal(spec: SpecStar, goal: ConversationGoal, *, user: str = "") -> None:
    """Whole-overwrite the conversation's goal row (create on first write,
    restore a soft-deleted row). Draft-status writes, like the todos row."""
    rm = spec.get_resource_manager(ConversationGoal)
    attribution = rm.using(user=user) if user else contextlib.nullcontext()
    with attribution:
        try:
            rm.modify(goal.conversation_id, goal, status=RevisionStatus.draft)
            return
        except ResourceIDNotFoundError:
            pass
        except ResourceIsDeletedError:
            rm.restore(goal.conversation_id)
            rm.modify(goal.conversation_id, goal, status=RevisionStatus.draft)
            return
        with contextlib.suppress(DuplicateResourceError):
            rm.create(goal, resource_id=goal.conversation_id, status=RevisionStatus.draft)


def read_goal(spec: SpecStar, conversation_id: str) -> ConversationGoal | None:
    """The conversation's goal row, or None when none was ever set."""
    rm = spec.get_resource_manager(ConversationGoal)
    try:
        res = rm.get(conversation_id)
    except (ResourceIDNotFoundError, ResourceIsDeletedError):
        return None
    data = res.data
    assert isinstance(data, ConversationGoal)  # narrow Struct|Unset for ty
    return data


def clear_goal(spec: SpecStar, conversation_id: str) -> None:
    """Drop the goal row (user cleared it / the chat was deleted). Idempotent."""
    rm = spec.get_resource_manager(ConversationGoal)
    with contextlib.suppress(ResourceIDNotFoundError, ResourceIsDeletedError):
        rm.delete(conversation_id)
