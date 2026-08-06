"""One document, from chunks to stored rows (#630 P4, re-cut by #697).

The primary layer (what the document mentions, the equivalences it declares,
what it says connects things) and its attribute statements come out of ONE model
call per chunk. They were two prompts over the same chunk, which doubled the
model time on the most expensive thing the graph does and split the signal: the
pass naming the things and the pass stating facts about them never saw each
other's answers, so a subject could be named in one and a stranger to the other.

Read the permission the rows must carry, COMPUTE the graph, and only then clear
what the document wrote before and record the new answer. The computation lives
in :mod:`build` and holds no store handle, so this module is the only part that
can write — and the read-only inspection tool runs the same computation without
it.

The order of those last two is not cosmetic. Extraction is minutes of model time
per document inside a job with a 30-minute ceiling it is known to hit (#670), so
"the run did not finish" is an ordinary outcome rather than a hypothetical.
Clearing first turns every one of those into a deletion: the rows go, nothing
arrives to replace them, and the vocabulary pass then recomputes over the hole
and drops the identities as well. Clearing last means the previous answer stands
until there is a new one to put in its place — which is worth more than no
answer, and is what the wipe-then-rewrite contract was always trying to say.

Still wipe-then-rewrite per document, so re-running after a prompt change
replaces what the document produced rather than accumulating beside it.
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
    try:
        mirror = doc_mirror_fields(spec, source_doc_id)
    except ResourceIDNotFoundError:
        # Nothing is coming to replace them and nothing may: a deck with no
        # permission to inherit can only produce rows born invisible. So this is
        # the one path where clearing is the whole job.
        wipe_doc_graph(spec, source_doc_id)
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
    if not graph.readable:
        # The model never answered for this document — a refusal, a truncated
        # generation, commentary instead of JSON. `extract_entities` never
        # raises, so this arrives as an empty result that is indistinguishable
        # from "the passages mention nothing", and clearing on it would delete
        # the document's evidence and record silence in its place.
        #
        # Compute-then-clear only protects the case where the call RAISED. This
        # is the same rule for the case it does not, and it is the reachable one.
        _LOGGER.warning(
            "graph: doc %s produced no readable reply from the model; "
            "keeping what it had rather than replacing it with nothing",
            source_doc_id,
        )
        return 0, 0
    wipe_doc_graph(spec, source_doc_id)
    persist_doc_graph(spec, graph)
    return len(graph.mentions), len(graph.claims)
