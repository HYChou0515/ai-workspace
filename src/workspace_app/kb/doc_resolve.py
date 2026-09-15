"""Issue #263: resolve a user-supplied filename to a SourceDoc id within a set
of collections, for the location-filtered ``kb_search``.

The agent only ever passes a human filename (``"Q3.xlsx"`` or
``"reports/Q3.xlsx"``). The opaque, ``∕``-laden id is never constructed by the
LLM — we run an INDEXED query over ``SourceDoc.path`` and read the matching
record's existing ``resource_id``. Exact path wins; otherwise a basename match
(so a user who types just the filename reaches a nested doc). Zero / many
matches are reported as recoverable statuses, not exceptions, so the tool can
turn them into a helpful message the model recovers from.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from specstar import QB, SpecStar

from ..resources.kb import SourceDoc
from .doc_id import canonical_path


@dataclass(frozen=True)
class DocResolution:
    """``status`` is ``ok`` (with ``doc_id`` + ``path``), ``not_found``, or
    ``ambiguous`` (with the matching ``candidates`` paths for the model to
    disambiguate)."""

    status: str
    doc_id: str | None = None
    path: str | None = None
    candidates: list[str] = field(default_factory=list)


def resolve_document(
    spec: SpecStar,
    collection_ids: list[str],
    name: str,
    *,
    exclude: frozenset[str] = frozenset(),
) -> DocResolution:
    """Resolve ``name`` to a single SourceDoc within ``collection_ids``.

    1. Exact path (canonicalised) — the unambiguous case.
    2. Else basename match: a coarse indexed ``path.contains(base)`` narrowed to
       an exact basename equality, so ``"Q3.xlsx"`` finds ``"reports/Q3.xlsx"``
       but not ``"Q3-old.xlsx"``.

    ``exclude`` (#308, the speaker's per-document denial) is applied BEFORE any
    decision: a denied document is neither a match nor a candidate, so a denied
    name reads exactly like a nonexistent one and an ambiguity never lists a
    path the speaker is barred from.
    """
    if not collection_ids:
        return DocResolution(status="not_found")
    rm = spec.get_resource_manager(SourceDoc)
    try:
        wanted = canonical_path(name)
    except ValueError:
        wanted = name

    exact = (QB["collection_id"].in_(collection_ids)) & (QB["path"] == wanted)
    hits = [r for r in rm.list_resources(exact.build()) if _rid(r) not in exclude]
    if len(hits) == 1:
        return _ok(hits[0])

    base = posixpath.basename(wanted)
    coarse = (QB["collection_id"].in_(collection_ids)) & QB["path"].contains(base)
    matches = [
        r
        for r in rm.list_resources(coarse.build())
        if _basename(r) == base and _rid(r) not in exclude
    ]
    if len(matches) == 1:
        return _ok(matches[0])
    if len(matches) > 1:
        return DocResolution(status="ambiguous", candidates=sorted(_path(r) for r in matches))
    return DocResolution(status="not_found")


def resolve_folder(
    spec: SpecStar, collection_ids: list[str], folder: str, *, exclude: frozenset[str] = frozenset()
) -> frozenset[str]:
    """plan-rag-context P3: the ids of every document UNDER ``folder`` (recursive)
    within ``collection_ids`` — the positive scope a folder-limited search or
    grep is restricted to (`restrict_to_doc_ids`, #518, which the context walk
    honours too). The match is on ``folder + "/"`` so ``a`` never bleeds into
    ``ab/…``; leading / trailing slashes are the same folder. Empty when nothing
    is under it (the caller says so — an empty scope must not fall back to an
    unscoped search). ``exclude`` (#308) is subtracted, so a folder whose every
    document the speaker is denied is as empty as one that does not exist.

    An INDEXED ``path.starts_with`` — the reason `source-doc` carries a
    migrations.md row: a row indexed before the `path` index has no cell to
    answer this and reads as "not in the folder"."""
    if not collection_ids:
        return frozenset()
    prefix = folder.strip("/")
    if not prefix:
        return frozenset()
    rm = spec.get_resource_manager(SourceDoc)
    cond = (QB["collection_id"].in_(collection_ids)) & QB["path"].starts_with(prefix + "/")
    return frozenset(
        rid
        for r in rm.list_resources(cond.build(), returns=["info"])
        if (rid := _rid(r)) not in exclude
    )


def _rid(rev: object) -> str:
    return rev.info.resource_id  # ty: ignore[unresolved-attribute]


def _ok(rev: object) -> DocResolution:
    return DocResolution(
        status="ok",
        doc_id=rev.info.resource_id,  # ty: ignore[unresolved-attribute]
        path=_path(rev),
    )


def _path(rev: object) -> str:
    data = rev.data  # ty: ignore[unresolved-attribute]
    assert isinstance(data, SourceDoc)
    return data.path


def _basename(rev: object) -> str:
    return posixpath.basename(_path(rev))
