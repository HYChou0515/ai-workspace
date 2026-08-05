"""One document, from chunks to stored rows (#630 P4, re-cut by #697).

The primary layer (what the document mentions, the equivalences it declares,
what it says connects things) and its attribute statements come out of ONE model
call per chunk. They were two prompts over the same chunk, which doubled the
model time on the most expensive thing the graph does and split the signal: the
pass naming the things and the pass stating facts about them never saw each
other's answers, so a subject could be named in one and a stranger to the other.

Three steps, and the order is the point: clear what the document wrote before,
read the permission its rows must carry, then COMPUTE the graph and record it.
The computation lives in :mod:`build` and holds no store handle, so this module
is the only part that can write — and the read-only inspection tool runs the
same computation without it.

Wipe-then-rewrite per document, so re-running after a prompt change replaces
what the document produced rather than accumulating beside it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..doc_permission import doc_mirror_fields
from ..llm import ILlm
from .build import DocSource, build_doc_graph
from .persist import persist_doc_graph, wipe_doc_graph

_LOGGER = logging.getLogger(__name__)


def write_doc_graph(
    spec: SpecStar,
    llm: ILlm,
    *,
    collection_id: str,
    source_doc_id: str,
    chunks: Iterable[tuple[str, str]],
    guidance: str = "",
) -> tuple[int, int]:
    """Extract each chunk once and persist both layers.

    ``chunks`` is ``(chunk_id, text)`` pairs. Returns
    ``(things_written, statements_written)``.

    A document that no longer exists is not an error: chunks outlive their deck
    (#104 made a chunk content-addressed), a vanished deck has no permission to
    inherit and nothing worth recording, and one dangling document must not fail
    the batch it rides in. Its rows are cleared and the model is never called —
    extracting from a deck whose permission cannot be read would spend the most
    expensive thing here on rows that could only be born invisible.
    """
    wipe_doc_graph(spec, source_doc_id)
    try:
        mirror = doc_mirror_fields(spec, source_doc_id)
    except ResourceIDNotFoundError:
        _LOGGER.warning(
            "graph: doc %s is gone; wiped its rows and skipped extraction", source_doc_id
        )
        return 0, 0
    graph = build_doc_graph(
        llm,
        DocSource(doc_id=source_doc_id, chunks=list(chunks), mirror=mirror),
        collection_id=collection_id,
        guidance=guidance,
    )
    persist_doc_graph(spec, graph)
    return len(graph.mentions), len(graph.claims)
