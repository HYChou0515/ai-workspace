"""Per-turn agent-config resolution (#89).

A per-App ``WorkItem`` (``RcaInvestigation`` …) resolves its turn's
``AgentConfig`` via the 3-layer ``AppCatalog``. ``find_work_item`` is the shared
"id → which App owns it + the item" seam, also used by the mention paths.
"""

from __future__ import annotations

import logging

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..resources import AgentConfig
from .base import WorkItemBase
from .catalog import AppCatalog
from .registry import app_model, registered_apps

logger = logging.getLogger(__name__)


def find_work_item(
    spec: SpecStar, item_id: str, *, include_deleted: bool = False
) -> tuple[str, WorkItemBase] | None:
    """Locate any registered App's ``WorkItem`` by its opaque ``item_id``.

    The single seam for "id → which App owns it + the item": shared by per-turn
    agent resolution and the mention paths so neither restates the scan. Returns
    ``(slug, item)`` on the first model whose table holds the id, else ``None``
    (a legacy ``Investigation`` or unknown id — callers handle that).

    A specstar id is ``{resource_name}:{uuid}``, so it NAMES its own table: read
    the prefix and go straight there. The scan below asked every registered App
    in turn, and a miss is not free — it costs a ``get`` and a ``get_meta``, so a
    ``pm`` item paid for a ``playground`` lookup first purely because of dict
    order. This resolution runs in front of every hand-written route, several
    times per user action, and in production one round-trip measured ~219ms; the
    call count IS the latency.

    A recognised prefix that holds no such row is a genuine miss, not a reason to
    go looking elsewhere — the id could not live in another table. The scan stays
    for ids whose prefix names no registered App (legacy ``Investigation``), which
    is exactly the case it was written for.

    A SOFT-DELETED row is a miss BY DEFAULT and readable on request, and getting
    that split right took three tries.

    specstar raises `ResourceIsDeletedError` rather than returning nothing, and
    the global handler maps that to 410 — so an unguarded lookup anywhere turned
    one person's deleted item into a 410 for whoever happened to be reading.
    `/me/resources` scans every sandbox on the replica, so that was every tenant
    on the pod, from one delete, with no reaping or window expiry needed.
    Guarding each call site is what produced that: three lookups run behind one
    request and only the one being looked at was covered.

    Answering `None` everywhere instead was quieter and worse. Four consumers
    read "no owner" as NOTHING TO BILL rather than "the debtor could not be
    resolved": the admission gate returns early, the per-person disk cap returns
    early, usage is never recorded, and `registry._bump` wrote `owner=""` over a
    row that already named somebody — erasing the charge for a sandbox that is
    still running. Delete an item, poke it once, and its slot was free while it
    ran.

    Resolving it everywhere is wrong in the other direction: `require_item`
    gates every `/a/{slug}/items/…` route on this same lookup, so exec, resize
    and the rest would answer 200 for something the user deleted.

    "Still owes for its sandbox" and "still operable" are different questions,
    so the caller says which one it is asking. ``include_deleted`` is taken by
    the paths that answer the first — the debtor (`ItemLocator.owner_of`), the
    environment size (the quota facts memo), and the two places a still-running
    sandbox has to be NAMED: the resources page and the `holding` list in a 507
    refusal. The chat permission mirror takes it too, for a different reason: a
    deleted item's permission still applies, and reading it as absent stamped
    the item's threads public.

    The default stays a miss. A route gate that resolves one does so to answer
    410 rather than 404 (`refuse_if_gone`), never to let the request through."""
    by_resource_name = {
        spec.get_resource_manager(model).resource_name: (slug, model)
        for slug, model in registered_apps().items()
    }
    named = by_resource_name.get(item_id.split(":", 1)[0])
    if named is not None:
        slug, model = named
        try:
            item = (
                spec.get_resource_manager(model).get(item_id, include_deleted=include_deleted).data
            )
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(item, WorkItemBase)
        return slug, item
    for slug, model in registered_apps().items():
        try:
            item = (
                spec.get_resource_manager(model).get(item_id, include_deleted=include_deleted).data
            )
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            continue
        assert isinstance(item, WorkItemBase)
        return slug, item
    return None


def debtor_of(spec: SpecStar, slug: str, item_id: str, item: WorkItemBase) -> str:
    """Who an item's resources are charged to: its ``owner``, or the CREATOR when
    ``owner`` says nothing.

    One function because there are two debtor lookups — the quota facts memo in
    `api/app.py` and `ItemLocator.owner_of` — and a rule that lives in one of
    them is a rule the other disagrees with.

    ``owner`` is an ordinary writable field (#687), and the accepted trade-off is
    that it can be pointed at somebody else: the bill MOVES. Blanking it was
    different in kind — an empty debtor reads as "nobody owes" at four gates at
    once, so one PATCH per item switched the whole per-person quota off and left
    the sandbox on nobody's resources page, where nobody could see it to close.

    ``created_by`` is written by the store at create and reachable through no
    route, so it is a FLOOR rather than a second field to keep in sync. ``owner``
    still wins whenever it says anything; this only deletes the answer "nobody".

    "Says anything" is `.strip()`, not truthiness: the first version of this
    floor was `if item.owner:`, and a single SPACE walked straight past it and
    reproduced the whole defect. A non-empty bogus name is a different case and
    stays as it was — that is #687's documented trade-off, where the bill moves
    to a name nobody holds. What must not exist is a bill that goes nowhere.

    Takes the already-resolved ``item`` so the common path costs nothing: the
    meta read happens only when ``owner`` is empty. Best effort — a debtor we
    cannot read is not a reason to fail the write that asked."""
    if item.owner.strip():
        return item.owner
    try:
        return (
            spec.get_resource_manager(app_model(slug))
            .get_meta(item_id, include_deleted=True)
            .created_by
        )
    except Exception:  # noqa: BLE001
        logger.debug("resolve: could not read created_by for item %s", item_id, exc_info=True)
        return ""


def resolve_item_agent_config(
    spec: SpecStar,
    app_catalog: AppCatalog,
    item_id: str,
) -> AgentConfig | None:
    found = find_work_item(spec, item_id)
    if found is None:
        return None
    slug, item = found
    return app_catalog.resolve(
        app_slug=slug,
        profile=item.profile,
        attached_preset=item.attached_preset or None,
        tool_prefs=item.attached_tool_prefs or None,
        skill_prefs=item.attached_skill_prefs or None,
    )
