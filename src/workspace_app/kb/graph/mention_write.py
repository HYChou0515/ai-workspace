"""#534 B — persist one document's mentions (the primary layer).

Idempotent per document: re-extracting WIPES what this document wrote before and
writes fresh, so tuning the prompt and re-running never accumulates. Rows keep
their ids across a re-run because the id is content-addressed, which is what lets
the vocabulary layer's links survive — the invariant the two-layer split rests
on.

Occurrences are aggregated across the WHOLE document rather than per passage: a
deck mentioning the same tool on five slides is one row with a count of five and
five chunk ids. The count is how often a document bothered to say something,
which is a signal the vocabulary layer uses to decide what matters; counted per
passage it would mean nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources.graph import GraphMention, mention_id
from ..doc_permission import doc_mirror_fields
from ..llm import ILlm
from .entity_extract import extract_entities
from .normalize import norm_surface

_LOGGER = logging.getLogger(__name__)


def wipe_doc_mentions(spec: SpecStar, source_doc_id: str) -> int:
    """Drop every mention this document produced. Returns how many went.

    Hard-delete, not soft: a soft ``delete`` still lists, so a re-run would
    accumulate. Called on the re-extraction path and whenever the document itself
    is torn down — a mention is keyed on its document, so the document going means
    its mentions go.
    """
    rm = spec.get_resource_manager(GraphMention)
    stale = [
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["source_doc_id"] == source_doc_id).build())
    ]
    for rid in stale:
        rm.permanently_delete(rid)
    return len(stale)


def write_doc_mentions(
    spec: SpecStar,
    llm: ILlm,
    *,
    collection_id: str,
    source_doc_id: str,
    chunks: Iterable[tuple[str, str]],
) -> int:
    """Extract + idempotently persist one document's mentions. ``chunks`` is
    ``(chunk_id, text)`` pairs. Returns the number of distinct things written.

    A document that no longer exists is not an error: chunks outlive their deck
    (#104 made a chunk content-addressed), a vanished deck has no permission to
    inherit and nothing worth recording, and one dangling document must not fail
    the batch it rides in. The wipe above has already cleared what it left.
    """
    wipe_doc_mentions(spec, source_doc_id)
    try:
        mirror = doc_mirror_fields(spec, source_doc_id)
    except ResourceIDNotFoundError:
        _LOGGER.warning(
            "graph: doc %s is gone; wiped its mentions and skipped extraction", source_doc_id
        )
        return 0

    # Aggregate by the comparison key, but keep the FIRST surface the document
    # actually used as the display form — never a normalised string nobody wrote.
    surfaces: dict[str, str] = {}
    kinds: dict[str, str] = {}
    counts: dict[str, int] = {}
    chunk_ids: dict[str, list[str]] = {}
    for chunk_id, text in chunks:
        for mention in extract_entities(llm, text):
            key = norm_surface(mention.surface)
            surfaces.setdefault(key, mention.surface)
            # The first non-empty kind wins; a later passage that omitted it does
            # not erase what an earlier one said.
            if mention.kind and not kinds.get(key):
                kinds[key] = mention.kind
            counts[key] = counts.get(key, 0) + 1
            seen = chunk_ids.setdefault(key, [])
            if chunk_id not in seen:
                seen.append(chunk_id)

    rm = spec.get_resource_manager(GraphMention)
    for key, surface in surfaces.items():
        rm.create(
            GraphMention(
                collection_id=collection_id,
                source_doc_id=source_doc_id,
                surface=surface,
                norm_surface=key,
                kind=kinds.get(key, ""),
                norm_kind=norm_surface(kinds.get(key, "")),
                occurrences=counts[key],
                chunk_ids=chunk_ids[key],
                **mirror,
            ),
            resource_id=mention_id(source_doc_id, surface),
        )
    return len(surfaces)
