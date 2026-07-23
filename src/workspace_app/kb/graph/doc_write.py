"""One pass over a document, both graph layers written from it (#630 P4).

The primary layer (what the document mentions, the equivalences it declares,
what it says connects things) and its attribute statements used to be two
prompts over the same chunk. That doubled the model time on the single most
expensive thing the graph does, and it split the signal: the pass naming the
things and the pass stating facts about them never saw each other's answers, so
a subject could be named in one and a stranger to the other.

So the extraction happens HERE, once per chunk, and the two writers consume its
result. Each still owns its own wipe-then-rewrite, so re-running after a prompt
change replaces both layers rather than accumulating.
"""

from __future__ import annotations

from collections.abc import Iterable

from specstar import SpecStar

from ..llm import ILlm
from .entity_extract import Extraction, extract_entities
from .mention_write import write_doc_mentions
from .write import write_doc_claims


def write_doc_graph(
    spec: SpecStar,
    llm: ILlm,
    *,
    collection_id: str,
    source_doc_id: str,
    chunks: Iterable[tuple[str, str]],
) -> tuple[int, int]:
    """Extract each chunk once and persist both layers.

    ``chunks`` is ``(chunk_id, text)`` pairs. Returns
    ``(things_written, statements_written)``.
    """
    extracted: list[tuple[str, Extraction]] = [
        (chunk_id, extract_entities(llm, text)) for chunk_id, text in chunks
    ]
    things = write_doc_mentions(
        spec, extracted, collection_id=collection_id, source_doc_id=source_doc_id
    )
    statements = write_doc_claims(
        spec, extracted, collection_id=collection_id, source_doc_id=source_doc_id
    )
    return things, statements
