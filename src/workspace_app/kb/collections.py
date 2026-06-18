"""Resolve KB collections + read the Topic Hub ``collections.json`` set (§5).

A Topic Hub's collection set is a **workspace file** (``collections.json`` — a list
of ``{"id", "name"}``), not a resource field. ``collection_ids_from_json`` turns the
parsed file into the ids used as the chat retrieval scope / a workflow's allowed set;
``resolve_collection`` backs the agent tool that maps a user-given id-or-name to the
canonical ``{id, name}`` the agent then writes into the file (resolve only, no write).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from specstar import QB

from ..resources.kb import Collection

if TYPE_CHECKING:
    from specstar import SpecStar


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
