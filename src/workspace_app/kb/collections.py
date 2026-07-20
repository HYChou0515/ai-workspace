"""Resolve KB collections + read the Topic Hub ``collections.json`` set (§5).

A Topic Hub's collection set is a **workspace file** (``collections.json`` — a list
of ``{"id", "name"}``), not a resource field. ``collection_ids_from_json`` turns the
parsed file into the ids used as the chat retrieval scope / a workflow's allowed set;
``resolve_collection`` backs the agent tool that maps a user-given id-or-name to the
canonical ``{id, name}`` the agent then writes into the file (resolve only, no write).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from specstar import QB
from specstar.types import ResourceIDNotFoundError

from ..filestore.protocol import FileNotFound
from ..perm import Actor, DisclosurePartition, authorize, partition_by_disclosure
from ..resources.kb import Collection, WithheldSource

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..filestore.protocol import FileStore

_LOGGER = logging.getLogger(__name__)


def collection_ids_from_json(data: Any) -> list[str]:
    """Extract collection ids (in order) from the parsed ``collections.json``.

    Tolerant by design — the file is hand-editable, so a malformed entry (no/blank
    ``id``, a non-dict) is skipped rather than crashing the turn."""
    out: list[str] = []
    if not isinstance(data, list):
        return out
    for entry in data:
        # Global-collection concept: an ``exclude: true`` entry is a NEGATIVE marker
        # (remove this global from scope), not a positive include — skip it here.
        if isinstance(entry, dict) and entry.get("exclude") is not True:
            cid = entry.get("id")
            if isinstance(cid, str) and cid:
                out.append(cid)
    return out


def excluded_ids_from_json(data: Any) -> list[str]:
    """The ids of ``collections.json`` entries flagged ``exclude: true`` — global
    collections the item wants OUT of scope (grill D2 mode 3). Same tolerance as the
    include parsers (malformed entries skipped); a file with no excludes yields []."""
    out: list[str] = []
    if not isinstance(data, list):
        return out
    for entry in data:
        if isinstance(entry, dict) and entry.get("exclude") is True:
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
        if entry.get("exclude") is True:  # a negative marker, not a tier member
            continue
        cid = entry.get("id")
        if not (isinstance(cid, str) and cid):
            continue
        raw_tier = entry.get("tier")
        # bool is an int subclass; a hand-typed "10" or junk falls back to tier 0.
        tier = raw_tier if isinstance(raw_tier, int) and not isinstance(raw_tier, bool) else 0
        by_tier.setdefault(tier, []).append(cid)
    return [by_tier[t] for t in sorted(by_tier)]


def readable_collection_ids(
    spec: SpecStar,
    ids: Iterable[str],
    user: str,
    *,
    superusers: frozenset[str] = frozenset(),
) -> list[str]:
    """#305 — the subset of ``ids`` the human ``user`` may ``read_content`` (input
    order preserved). The transitive / converse-time gate: an AI consulting the KB
    on the speaker's behalf (``ask_knowledge_base``) may only search collections the
    speaker could read directly, so a private or since-tightened collection can't
    leak through the sub-agent. An unknown id is dropped; ``permission is None`` ≡
    public (back-compat). A point ``get`` per id (not a full scan) keeps the
    infer_modules hot path — one pre-resolved collection — cheap."""
    rm = spec.get_resource_manager(Collection)
    actor = Actor.human(user)
    out: list[str] = []
    for cid in ids:
        try:
            rev = rm.get(cid)
        except ResourceIDNotFoundError:
            continue
        data = rev.data
        assert isinstance(data, Collection)  # the Collection manager yields Collection
        if authorize(
            actor,
            "read_content",
            data.permission,
            created_by=rev.info.created_by,
            superusers=superusers,
        ):
            out.append(cid)
    return out


def global_collection_ids(spec: SpecStar) -> list[str]:
    """The ids of every collection flagged ``is_global`` — the AI's baseline
    retrieval scope in every conversation. An INDEXED query (``is_global`` is in
    Collection's ``indexed_fields``), not a full scan. Governance: only a superuser
    may set the flag (grill D3), so this set is curated."""
    rm = spec.get_resource_manager(Collection)
    return [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["is_global"] == True).build())  # noqa: E712
    ]


def resolve_effective_scope(
    spec: SpecStar,
    specified: Iterable[str] | None,
    *,
    excluded: Iterable[str] = (),
) -> list[str]:
    """The global-aware retrieval scope, BEFORE the per-user permission filter:
    ``(specified ∪ global) \\ excluded`` (grill D2). ``specified`` None/empty ⇒ the
    global set ALONE ("unspecified → global", the D5 hard cutover — no globals means
    an empty scope). Order-preserving (specified first in input order, then globals
    not already listed) and deduped; exclusion wins over inclusion for the same id.

    This is the ONE place the union/exclude lives — every retrieval surface
    (subagent bridge, KB chat, ask_knowledge_base tiers) calls it, then applies its
    own readable/discoverable partition ON TOP of the returned ids."""
    excl = set(excluded)
    base = list(specified) if specified else []
    seen: set[str] = set()
    out: list[str] = []
    for cid in (*base, *global_collection_ids(spec)):
        if cid and cid not in seen and cid not in excl:
            seen.add(cid)
            out.append(cid)
    return out


def partition_collection_disclosure(
    spec: SpecStar,
    ids: Iterable[str],
    user: str,
    *,
    superusers: frozenset[str] = frozenset(),
) -> DisclosurePartition:
    """The disclosure-aware sibling of ``readable_collection_ids`` (permission-
    disclosure). Splits ``ids`` into ``readable`` (read_content — searched as
    today), ``discoverable`` (read_meta but NOT read_content — surfaced by the
    disclosure probe instead of dropped), and ``hidden`` (no read_meta — a uniform
    404, never disclosed).

    ``readable`` is byte-identical to ``readable_collection_ids`` (same
    ``Actor.human(user)`` — NO groups — same point-get per id, same order), so
    swapping a caller onto this is a no-op for the searched scope; it only ADDS the
    middle tier. Groups are intentionally omitted to stay consistent with
    ``readable_collection_ids``; a future change adds them to BOTH at once. An
    unknown id is dropped; ``permission is None`` ≡ public."""
    rm = spec.get_resource_manager(Collection)
    actor = Actor.human(user)
    entries: list[tuple[str, Any, str]] = []
    for cid in ids:
        try:
            rev = rm.get(cid)
        except ResourceIDNotFoundError:
            continue
        data = rev.data
        assert isinstance(data, Collection)
        entries.append((cid, data.permission, rev.info.created_by))
    return partition_by_disclosure(actor, entries, superusers=superusers)


def resolve_withheld(spec: SpecStar, collection_ids: Iterable[str]) -> list[WithheldSource]:
    """Turn disclosed withheld collection ids into ``WithheldSource`` records
    (id + name + owner) for the assistant message the FE renders. Input order is
    preserved; a since-deleted collection is skipped. Only identity + owner are
    read — never any content — all of which a ``read_meta`` holder already sees."""
    rm = spec.get_resource_manager(Collection)
    out: list[WithheldSource] = []
    # dedupe by id (a turn's several ask_knowledge_base sub-agents each extend the
    # same accumulator, so a collection disclosed twice must chip once).
    for cid in dict.fromkeys(collection_ids):
        try:
            rev = rm.get(cid)
        except ResourceIDNotFoundError:
            continue
        data = rev.data
        assert isinstance(data, Collection)
        out.append(WithheldSource(collection_id=cid, name=data.name, owner=rev.info.created_by))
    return out


def _all_collections(spec: SpecStar) -> list[tuple[str, Collection]]:
    rm = spec.get_resource_manager(Collection)
    return [
        (r.info.resource_id, r.data)  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Collection)
    ]


# How many names a miss suggests. A miss is a typo or a half-remembered name,
# so what helps is the few names NEAR what was asked for — the rest of the
# catalog is noise the agent pays for in context on every fat-fingered lookup.
_NEAREST_MAX = 20


def _nearest(ref: str, colls: list[tuple[str, Collection]]) -> list[dict[str, str]]:
    """The collections closest to `ref`, best first, at most `_NEAREST_MAX`.

    Substring hits rank above fuzzy ones (a user who typed half a name means
    that name); ties break alphabetically so the same miss always suggests the
    same list."""
    folded = ref.casefold()

    def rank(item: tuple[str, Collection]) -> tuple[int, float, str]:
        name = item[1].name
        contains = 0 if folded in name.casefold() else 1
        ratio = SequenceMatcher(None, folded, name.casefold()).ratio()
        return (contains, -ratio, name)

    return [{"id": cid, "name": c.name} for cid, c in sorted(colls, key=rank)[:_NEAREST_MAX]]


def resolve_collection(spec: SpecStar, ref: str) -> dict[str, Any]:
    """Resolve a user-given collection ``ref`` (an id **or** a name) to its
    canonical ``{id, name}``. **Resolve only — never writes.** Returns one of:

    - ``{"status": "ok", "id", "name"}`` — a unique match (id, or case-insensitive name);
    - ``{"status": "ambiguous", "candidates": [{id, name}, …]}`` — the name hit several;
    - ``{"status": "not_found", "available": [{id, name}, …], "total": N}`` — no
      match; ``available`` is the closest names (not the whole catalog, which on
      a real deployment is hundreds of rows the agent never asked for), and
      ``total`` says how many exist so a short list can't read as "that's all".
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
    return {
        "status": "not_found",
        "available": _nearest(ref, colls),
        "total": len(colls),
    }


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


def resolve_named_collection_ids(spec: SpecStar, name: str) -> list[str] | None:
    """#66: the KB collection ids infer_modules' per-step classifier
    searches. "" ⇒ None (search ALL collections, backward-compatible). A
    configured NAME resolves to its collection's ids; a name that matches
    no collection is a loud misconfig — raise rather than silently fall
    back to taxonomy-only (a typo would otherwise disable KB lookups for
    every step). Resolved once per turn, not per step."""
    if not name:
        return None

    coll_rm = spec.get_resource_manager(Collection)
    ids = [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in coll_rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Collection) and r.data.name == name
    ]
    if not ids:
        raise ValueError(
            f"infer_modules is configured to search collection {name!r} "
            f"(agents.infer_modules[].collection) but no collection with that "
            f"name exists — create it, fix the name, or remove the setting to "
            f"search all collections."
        )
    return ids


async def read_hub_collections(filestore: FileStore, item_id: str) -> Any:
    """Parse the item's ``collections.json`` workspace file ONCE (topic-hub §5)
    → its raw JSON (a list of ``{id, name, tier?}``), or ``None`` when the file
    is absent or unparseable. The two derivations below
    (``collection_ids_from_json`` = the flat union scope for ``lookup_glossary`` /
    ``resolve_collection``; ``collection_tiers_from_json`` = #280's rank-ordered
    tiers for ``ask_knowledge_base``) each tolerate a hand-edited / malformed file."""
    try:
        raw = await filestore.read(item_id, "/collections.json")
    except FileNotFound:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
