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
    is_deleted: bool = False
    """Whether the row is soft-deleted. Carried rather than filtered out because
    the two answers a caller can want are different: a gate operating ON this
    item wants 410 Gone (it existed, it is finished), while a page that lists or
    bills across MANY items must never let one deleted row take the page down.
    Both read the same facts and decide for themselves."""


def load_access_facts(
    spec: SpecStar, item_id: str, *, include_deleted: bool = False
) -> ItemAccessFacts | None:
    """The item's identity + permission inputs, or ``None`` for an unknown id.

    ``include_deleted`` relaxes what can be FOUND, never who may see it — the
    reader's own `read_meta` is checked on the facts either way. Three kinds of
    caller take it: the two that must still NAME a deleted item whose sandbox is
    running (the resources page, and the `holding` list in a 507 refusal body),
    and the gates, which resolve it in order to answer 410 rather than 404. The
    returned facts say which case this is via ``is_deleted``."""
    found = find_work_item(spec, item_id, include_deleted=include_deleted)
    if found is None:
        return None
    slug, item = found
    meta = spec.get_resource_manager(app_model(slug)).get_meta(
        item_id, include_deleted=include_deleted
    )
    return ItemAccessFacts(
        slug=slug, item=item, created_by=meta.created_by, is_deleted=meta.is_deleted
    )


def refuse_if_gone(facts: ItemAccessFacts | None, item_id: str) -> None:
    """410 Gone for a soft-deleted item, once the caller has passed ``check_access``.

    ONE function because there are THREE gates in front of hand-written item
    routes — `require_item_access` here, and `require_item` / `require_access` on
    the locator — and this branch installed in the two I happened to be reading
    is how the last six review rounds went. Call it right after the access check
    in each.

    AFTER the access check, never before: 410 says "this item existed", and only
    somebody who could already have seen it may learn that.

    404 and 410 are told apart deliberately. An outside system lists items and
    then acts on them (#700), so "that one is finished, open a new one" and "no
    such item, the platform may be broken" are different instructions."""
    if facts is not None and facts.is_deleted:
        raise HTTPException(status_code=410, detail=f"item {item_id!r} is gone")


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
    facts = load_access_facts(spec, item_id, include_deleted=True)
    groups = groups_provider(user) if groups_provider is not None else groups_of(spec, user)
    check_access(facts, slug, item_id, verb, user=user, groups=groups, superusers=superusers)
    refuse_if_gone(facts, item_id)
    assert facts is not None  # check_access raises on None
    return facts.item, facts.created_by
