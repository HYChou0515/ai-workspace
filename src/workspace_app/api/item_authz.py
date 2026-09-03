"""Shared WorkItem authorization for the hand-written workspace routes (#306 /
plan-permissions.md Rollout PR3).

THREE gates stand in front of the item routes, and which one a route uses is
not guessable from its path. Grepped, not remembered — three separate claims in
this branch's own comments got it wrong:

===========================  ==========================================
gate                         routes
===========================  ==========================================
``require_item_access``      ``/resources`` ``/environment``
(here; via ``_authorize_item``   ``/permission`` ``/members``, the item
 in `item_routes`, ``_gate``     PATCH, the env-provider routes, and
 in `env_provider_routes`)      ``DELETE /me/resources/live/{id}``
``ItemLocator.require_access``  ``/exec`` ``/files`` ``/chats`` and the
                                streams — 59 call sites across
                                `file_routes`, `chat_routes`,
                                `workflow_routes`
``ItemLocator.require_item``    ``/tools`` ``/entities`` ``/export``
                                (`tools_routes`, `entity_routes`,
                                 `capability_routes`) — validates the
                                 slug pairing and authorizes NOBODY
===========================  ==========================================

A difference a caller needs is a PARAMETER on ``require_item_access``
(``slug=ANY_APP`` for a route with no slug in its path, ``allow_deleted`` for a
billing action) rather than a copy of its body — a copy is how a rule added here
later stops reaching every caller, which is how this file came to be missing
`refuse_if_gone` at one gate a round after writing it.

The other two gates are deliberate second compositions, for reasons that cannot
be arguments here: `require_access` wraps the same steps in a 5-second facts
MEMO (the facts are the thing being cached, and this function loads them), and
`require_item` answers from ONE read because it needs no permission facts at
all. So the RULES they share are extracted instead — ``app_matches`` below is
the slug pairing, ``refuse_if_gone`` the deleted-item refusal — and each names
every gate that must call it.

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


class AnyApp:
    """Marker for a route that addresses an item by ID ALONE, where there is no
    slug↔item pairing to validate — `/me/resources/live/{item_id}` is the case.

    A distinct TYPE rather than ``None`` because `ItemLocator.slug_of` and
    `TurnFacts.slug` are both ``str | None``: with ``None`` as the marker, any
    one of them flowing into a gate would silently switch off #95 with no type
    error and no red test. ``str | AnyApp`` makes that a `ty` failure instead.
    Skipping the check has to be something a caller SAYS, not something that
    happens to it."""

    __slots__ = ()


ANY_APP = AnyApp()


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

    AFTER the access check wherever there IS one — `require_item_access` here and
    `ItemLocator.require_access` — because 410 says "this item existed" and only
    somebody who could already have seen it may learn that.

    `ItemLocator.require_item` calls it with no access check, because it has
    none to make: it validates the slug↔item pairing and nothing else, and the
    routes behind it (tools / entity / capability / export) authorize nobody at
    all. The 410 there discloses strictly less than the 200 beside it. That is a
    pre-existing hole in those routes rather than a licence — when they are
    gated, this call moves after the gate like the other two.

    404 and 410 are told apart deliberately. An outside system lists items and
    then acts on them (#700), so "that one is finished, open a new one" and "no
    such item, the platform may be broken" are different instructions."""
    if facts is not None and facts.is_deleted:
        raise HTTPException(status_code=410, detail=f"item {item_id!r} is gone")


def app_matches(actual: str, wanted: str | AnyApp) -> bool:
    """#95: does the App this item really belongs to satisfy how the caller
    addressed it? ``ANY_APP`` for a route that carries no slug (see `AnyApp`).

    One function because the pairing is tested at TWO gates and cannot be tested
    at one: `check_access` has the item's access facts, `ItemLocator.require_item`
    deliberately has only the item (one read, no meta). With the comparison
    written out in both, a mutation of either left the other green — which is
    how the rule came to have no test at `check_access` at all while a commit
    message claimed otherwise."""
    return isinstance(wanted, AnyApp) or actual == wanted


def check_access(
    facts: ItemAccessFacts | None,
    slug: str | AnyApp,
    item_id: str,
    verb: Verb,
    *,
    user: str,
    groups: frozenset[str],
    superusers: frozenset[str] = frozenset(),
) -> None:
    """Raise unless ``user`` may ``verb`` this item. Pure — no I/O — so the facts
    can come from a cache without the DECISION being cached with them: a verb the
    caller has never asked for is still evaluated properly.

    ``slug`` is ``ANY_APP`` for a route that addresses the item by ID ALONE (see
    `AnyApp`). Passing a fabricated slug there is what made a deleted item's
    environment unclosable: the fabrication came from a lookup that reports one
    as absent, so the check refused every time."""
    if facts is None or not app_matches(facts.slug, slug):
        # One message, no branch. The ANY_APP rendering is never surfaced: its
        # only caller maps every refusal here to "unknown environment", and a
        # branch nothing can fail without is a branch this branch keeps having
        # to delete.
        raise HTTPException(status_code=404, detail=f"item {item_id!r} not found in app {slug!r}")
    actor = Actor.human(user, groups=groups)
    perm = facts.item.permission
    if not authorize(actor, "read_meta", perm, created_by=facts.created_by, superusers=superusers):
        raise HTTPException(status_code=404, detail="item not found")
    if not authorize(actor, verb, perm, created_by=facts.created_by, superusers=superusers):
        raise HTTPException(status_code=403, detail=f"not authorized to {verb}")


def require_item_access(
    spec: SpecStar,
    slug: str | AnyApp,
    item_id: str,
    verb: Verb,
    *,
    user: str,
    superusers: frozenset[str] = frozenset(),
    groups_provider: Callable[[str], frozenset[str]] | None = None,
    allow_deleted: bool = False,
) -> tuple[WorkItemBase, str]:
    """Gate a hand-written workspace route: validate that ``item_id`` belongs to
    App ``slug`` (404), then check ``read_meta`` first (404 — an actor who can't
    see the item never learns it exists) and ``verb`` itself (403). Returns the
    item + its owner (``created_by``) for the handler. ``permission is None`` ≡
    public (legacy items, no migration).

    ``groups_provider`` resolves the caller's groups so a ``group:`` grant matches;
    defaults to the live ``groups_of`` lookup, keeping this consistent with the
    storage-layer ``work_item_access_scope`` (which honours groups).

    ``slug=ANY_APP`` for a route that addresses the item by id alone (see
    `AnyApp`). ``allow_deleted`` for a BILLING action rather than an
    operation on the item: a deleted item's sandbox keeps running and keeps
    being charged for, so closing it must still work — the resources page exists
    to offer exactly that. Both are PARAMETERS rather than a second copy of this
    body somewhere else, because the copy is what stops the next rule added here
    from reaching every caller — which is precisely how `refuse_if_gone` came to
    be missing from a gate one round after it was written."""
    facts = load_access_facts(spec, item_id, include_deleted=True)
    groups = groups_provider(user) if groups_provider is not None else groups_of(spec, user)
    check_access(facts, slug, item_id, verb, user=user, groups=groups, superusers=superusers)
    if not allow_deleted:
        refuse_if_gone(facts, item_id)
    assert facts is not None  # check_access raises on None
    return facts.item, facts.created_by
