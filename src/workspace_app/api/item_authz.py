"""Shared WorkItem authorization for the hand-written workspace routes (#306 /
plan-permissions.md Rollout PR3).

The item auto-CRUD is storage-gated by ``work_item_access_scope`` (read_meta →
404), but the workspace SUB-routes (files, chat, stream) resolve the item through
``ItemLocator.require_item`` / ``rm.get``, which bypasses that scope and — before
this — enforced nothing. ``require_item_access`` is the single gate they all funnel
through: validate slug↔item, then sequence ``read_meta`` (404, no existence leak)
and the route's verb (403), against the LIVE item permission (no denormalized
mirror — the item lookup already happens per request).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException
from specstar import SpecStar

from ..apps.base import WorkItemBase
from ..apps.registry import app_model
from ..apps.resolve import find_work_item
from ..perm import Actor, Verb, authorize
from ..resources.groups import groups_of


@dataclass(frozen=True)
class ItemAccessFacts:
    """Everything deciding access needs about an item, and nothing else.

    Split out because the two lookups behind it — the item row and its meta — are
    the ENTIRE database cost of a read request (the handlers themselves make
    none), and that cost is CPU-bound Python rather than SQL, so it cannot be
    parallelised away. Separating "what is true about this item" from "may this
    actor do this" lets a caller that only wants the decision reuse the facts,
    while a caller that needs the row keeps reading it fresh."""

    slug: str
    item: WorkItemBase
    created_by: str


def load_access_facts(spec: SpecStar, item_id: str) -> ItemAccessFacts | None:
    """The item's identity + permission inputs, or ``None`` for an unknown id."""
    found = find_work_item(spec, item_id)
    if found is None:
        return None
    slug, item = found
    created_by = spec.get_resource_manager(app_model(slug)).get_meta(item_id).created_by
    return ItemAccessFacts(slug=slug, item=item, created_by=created_by)


def check_access(
    facts: ItemAccessFacts | None,
    slug: str,
    item_id: str,
    verb: Verb,
    *,
    user: str,
    groups: frozenset[str],
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Raise unless ``user`` may ``verb`` this item. Pure — no I/O — so the facts
    can come from a cache without the DECISION being cached with them: a verb the
    caller has never asked for is still evaluated properly."""
    if facts is None or facts.slug != slug:
        raise HTTPException(status_code=404, detail=f"item {item_id!r} not found in app {slug!r}")
    actor = Actor.human(user, groups=groups)
    perm = facts.item.permission
    if not authorize(actor, "read_meta", perm, created_by=facts.created_by, superusers=superusers):
        raise HTTPException(status_code=404, detail="item not found")
    if not authorize(actor, verb, perm, created_by=facts.created_by, superusers=superusers):
        raise HTTPException(status_code=403, detail=f"not authorized to {verb}")


def require_item_access(
    spec: SpecStar,
    slug: str,
    item_id: str,
    verb: Verb,
    *,
    user: str,
    superusers: frozenset[str] = frozenset(),
    groups_provider: Callable[[str], frozenset[str]] | None = None,
) -> tuple[WorkItemBase, str]:
    """Gate a hand-written workspace route: validate that ``item_id`` belongs to
    App ``slug`` (404), then check ``read_meta`` first (404 — an actor who can't
    see the item never learns it exists) and ``verb`` itself (403). Returns the
    item + its owner (``created_by``) for the handler. ``permission is None`` ≡
    public (legacy items, no migration).

    ``groups_provider`` resolves the caller's groups so a ``group:`` grant matches;
    defaults to the live ``groups_of`` lookup, keeping this consistent with the
    storage-layer ``work_item_access_scope`` (which honours groups)."""
    facts = load_access_facts(spec, item_id)
    groups = groups_provider(user) if groups_provider is not None else groups_of(spec, user)
    check_access(facts, slug, item_id, verb, user=user, groups=groups, superusers=superusers)
    assert facts is not None  # check_access raises on None
    return facts.item, facts.created_by
