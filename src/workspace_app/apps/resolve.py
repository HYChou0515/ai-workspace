"""Per-turn agent-config resolution (#89).

A per-App ``WorkItem`` (``RcaInvestigation`` …) resolves its turn's
``AgentConfig`` via the 3-layer ``AppCatalog``. ``find_work_item`` is the shared
"id → which App owns it + the item" seam, also used by the mention paths.
"""

from __future__ import annotations

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..resources import AgentConfig
from .base import WorkItemBase
from .catalog import AppCatalog
from .registry import registered_apps


def find_work_item(spec: SpecStar, item_id: str) -> tuple[str, WorkItemBase] | None:
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

    A SOFT-DELETED row is a miss too, and saying so here is what stops it being
    everyone else's problem. specstar raises `ResourceIsDeletedError` rather
    than returning nothing, and the global handler maps that to 410 — so an
    unguarded lookup anywhere turned one person's deleted item into a 410 for
    whoever happened to be reading. `/me/resources` scans every sandbox on the
    replica, so that was every tenant on the pod, from one delete, with no
    reaping or window expiry needed.

    Guarding it at each call site is what produced that: three lookups run
    behind one request and only the one being looked at was covered. The
    contract already says "``None`` for an id that is not there, callers handle
    that" — a deleted id is not there, and callers that must still act on it
    (the quota ledger keeps charging for a deleted item's live sandbox, and its
    row must stay closable) read the ledger, which is keyed on the id and does
    not need the row."""
    by_resource_name = {
        spec.get_resource_manager(model).resource_name: (slug, model)
        for slug, model in registered_apps().items()
    }
    named = by_resource_name.get(item_id.split(":", 1)[0])
    if named is not None:
        slug, model = named
        try:
            item = spec.get_resource_manager(model).get(item_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(item, WorkItemBase)
        return slug, item
    for slug, model in registered_apps().items():
        try:
            item = spec.get_resource_manager(model).get(item_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            continue
        assert isinstance(item, WorkItemBase)
        return slug, item
    return None


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
