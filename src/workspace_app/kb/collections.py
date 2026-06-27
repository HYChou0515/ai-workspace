"""Resolve KB collections + read the Topic Hub ``collections.json`` set (§5).

A Topic Hub's collection set is a **workspace file** (``collections.json`` — a list
of ``{"id", "name"}``), not a resource field. ``collection_ids_from_json`` turns the
parsed file into the ids used as the chat retrieval scope / a workflow's allowed set;
``resolve_collection`` backs the agent tool that maps a user-given id-or-name to the
canonical ``{id, name}`` the agent then writes into the file (resolve only, no write).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from specstar import QB

from ..resources.kb import Collection

if TYPE_CHECKING:
    from specstar import SpecStar

_LOGGER = logging.getLogger(__name__)


def collection_ids_from_json(data: Any) -> list[str]:
    """Extract collection ids (in order) from the parsed ``collections.json``.

    Tolerant by design — the file is hand-editable, so a malformed entry (no/blank
    ``id``, a non-dict) is skipped rather than crashing the turn."""
    out: list[str] = []
    if not isinstance(data, list):
        return out
    for entry in data:
        if isinstance(entry, dict):
            cid = entry.get("id")
            if isinstance(cid, str) and cid:
                out.append(cid)
    return out


def collection_tiers_from_json(data: Any) -> list[list[str]]:
    """Group ``collections.json`` entries into **priority tiers**, ordered by rank.

    Each entry may carry an optional ``tier`` int (sparse by convention — 0, 10,
    20 — so operators can insert between later). Entries are grouped by tier value;
    the distinct tier values are sorted ascending and returned as a rank-indexed
    list of id-lists (rank 0 = smallest tier). Within a tier, ids keep file order.

    Backward compatible + tolerant (the file is hand-editable): a missing or
    non-int ``tier`` defaults to tier 0, malformed entries are skipped, and a
    non-list input yields ``[]``. A flat legacy file (no ``tier`` anywhere) becomes
    a single tier ``[[...]]``."""
    by_tier: dict[int, list[str]] = {}
    if not isinstance(data, list):
        return []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        if not (isinstance(cid, str) and cid):
            continue
        raw_tier = entry.get("tier")
        # bool is an int subclass; a hand-typed "10" or junk falls back to tier 0.
        tier = raw_tier if isinstance(raw_tier, int) and not isinstance(raw_tier, bool) else 0
        by_tier.setdefault(tier, []).append(cid)
    return [by_tier[t] for t in sorted(by_tier)]


def _all_collections(spec: SpecStar) -> list[tuple[str, Collection]]:
    rm = spec.get_resource_manager(Collection)
    return [
        (r.info.resource_id, r.data)  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Collection)
    ]


def resolve_collection(spec: SpecStar, ref: str) -> dict[str, Any]:
    """Resolve a user-given collection ``ref`` (an id **or** a name) to its
    canonical ``{id, name}``. **Resolve only — never writes.** Returns one of:

    - ``{"status": "ok", "id", "name"}`` — a unique match (id, or case-insensitive name);
    - ``{"status": "ambiguous", "candidates": [{id, name}, …]}`` — the name hit several;
    - ``{"status": "not_found", "available": [{id, name}, …]}`` — no match; lists all.
    """
    colls = _all_collections(spec)
    for cid, c in colls:
        if cid == ref:  # an exact resource id wins
            return {"status": "ok", "id": cid, "name": c.name}
    matches = [(cid, c) for cid, c in colls if c.name.casefold() == ref.casefold()]
    if len(matches) == 1:
        cid, c = matches[0]
        return {"status": "ok", "id": cid, "name": c.name}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "candidates": [{"id": cid, "name": c.name} for cid, c in matches],
        }
    return {"status": "not_found", "available": [{"id": cid, "name": c.name} for cid, c in colls]}


def resolve_profile_collections(
    spec: SpecStar, declared: list[tuple[str, int]]
) -> list[dict[str, Any]]:
    """Resolve a profile's declared default collections (``(name, tier)`` pairs) into
    the ``collections.json`` rows (``[{id, name, tier}]``) used to seed a new item (#280).

    Each name is resolved via :func:`resolve_collection` (exact id, or case-insensitive
    name). A name that matches no collection — or an ambiguous one — is **skipped and
    logged**, never raised: a stale profile default must not block item creation (Q9).
    The surviving rows keep their declared ``tier`` so the rank fallback works from the
    first turn."""
    rows: list[dict[str, Any]] = []
    for name, tier in declared:
        res = resolve_collection(spec, name)
        if res.get("status") == "ok":
            rows.append({"id": res["id"], "name": res["name"], "tier": tier})
        else:
            _LOGGER.warning(
                "profile default collection %r did not resolve (%s); skipping",
                name,
                res.get("status"),
            )
    return rows
