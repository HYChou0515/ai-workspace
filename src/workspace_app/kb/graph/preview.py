"""#697 — see what the graph WOULD be, and change nothing seeing it.

The extraction criterion is domain knowledge, so it has to be written by whoever
owns the corpus, and writing it means iterating: state a criterion, run it, read
what came out, adjust. That loop was impossible while running the graph had side
effects — every experiment rewrote the corpus it was judging, and it could only
be run where the data was.

This is the loop. It reads a collection, runs the computation PRODUCTION runs
(:func:`build_graph`), and writes the result as JSON in a local directory. Two
runs either side of a criterion change are directly comparable, and the diff
shows what the change did.

Nothing here writes to the store, and not as a promise: the module imports no
persistence and calls nothing that could. Reading is the only thing it knows how
to do — which is also what makes it safe to point at an environment that has
data in it.

**One difference, stated rather than hidden.** The identities here are built
from THIS collection's evidence alone, while production builds them over the
whole corpus — identity is shared across collections, which is the point of the
layer. So the extraction half (what the model named, and what kind of thing it
said each was) is exactly what production would produce; the vocabulary half is
what this collection alone supports, and two identities that would meet
another corpus's evidence will not have met it here. The tool exists to tune the
EXTRACTION criterion, which is per-collection, so that is the right scope — but
it is a difference, and a difference nobody was told about is a lie.

The summary is the part that gets read most. Whether a criterion is working is a
question about SHAPE — how many distinct names one document yields, what sorts of
thing the model decided it was seeing — and nobody answers that by scrolling
through thousands of rows.
"""

from __future__ import annotations

import json
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import msgspec
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ...perm import Actor
from ...resources.kb import Collection, DocChunk, SourceDoc
from ..collections import readable_collection_ids
from ..doc_permission import denied_doc_ids, doc_mirror_fields
from ..llm import ILlm
from .build import DocSource, Graph, build_graph
from .paging import walk_rows


def read_collection_sources(
    spec: SpecStar, collection_id: str, *, as_user: str | None = None
) -> tuple[str, list[DocSource]]:
    """The collection's criterion and one :class:`DocSource` per document.

    ``as_user`` reads AS that person, so the preview shows the corpus THEY can
    see. Setting the spec's default user is not the same thing and does not do
    it: that decides who a write is attributed to, not what a read may return —
    so a tool offering the option without the check would be handing out an
    assurance it was not giving. ``None`` reads with whatever the spec was built
    with, which is the operator's own view.

    The question asked is ``read_content``, at both levels, because what comes
    out of here is the passages themselves. The access scopes on ``Collection``
    and ``SourceDoc`` are read_meta scopes — "may you know this exists" — and
    "discoverable but not readable" is a state the product models on purpose and
    offers in the share dialog, so scoping on them alone would hand a corpus's
    verbatim text to someone allowed only to know it is there.

    The permission mirror is read here, exactly as the extractor reads it, so a
    previewed row is the row that would have been stored — including whether
    anyone could have seen it. A document that has since vanished, or that this
    reader may not open, is skipped rather than previewed against a mirror nobody
    could compute.
    """
    denied: frozenset[str] = frozenset()
    if as_user is not None:
        # READ_CONTENT, not read_meta. The scopes below are the collection's and
        # the deck's, and both are read_meta scopes — "may you know this exists".
        # What this tool hands over is the passages themselves, and
        # "discoverable but not readable" is a state the product models on
        # purpose and offers in the share dialog. Production's rule for exactly
        # this data says it outright (`graph_evidence_access_scope`): with only
        # read_meta, a claim would hand over content the reader is not allowed
        # to read.
        if collection_id not in readable_collection_ids(spec, [collection_id], as_user):
            return "", []
        # …and the same question per deck, because #308 lets one tighten inside
        # a collection anyone may read. Usually empty.
        denied = denied_doc_ids(spec, Actor.human(as_user), [collection_id], "read_content")

    with ExitStack() as scope:
        if as_user is not None:
            # The read_meta scopes still apply on top: they are what hides a
            # collection or a deck the person may not even know about, and the
            # content gate above does not answer that question. `DocChunk` is
            # deliberately absent — it carries no access scope at all, so
            # entering one would be a no-op dressed as a safeguard; chunks are
            # gated by the two checks above instead.
            for model in (Collection, SourceDoc):
                scope.enter_context(
                    spec.get_resource_manager(model).using(as_user, apply_access_scope=True)  # ty: ignore[unknown-argument]
                )
        return _read_sources(spec, collection_id, denied=denied)


def _read_sources(
    spec: SpecStar, collection_id: str, *, denied: frozenset[str] = frozenset()
) -> tuple[str, list[DocSource]]:
    try:
        collection = spec.get_resource_manager(Collection).get(collection_id).data
    except ResourceIDNotFoundError:
        # Unreadable is indistinguishable from absent, by design — the scope
        # hides the row rather than refusing it, and a preview must not become
        # the one channel that says a collection exists.
        return "", []
    assert isinstance(collection, Collection)

    krm = spec.get_resource_manager(DocChunk)
    chunks: dict[str, list[tuple[int, str, str]]] = {}
    for r in walk_rows(krm, QB["collection_id"] == collection_id):
        chunk = r.data
        assert isinstance(chunk, DocChunk)
        chunks.setdefault(chunk.source_doc_id, []).append(
            (chunk.seq, r.info.resource_id, chunk.text)
        )

    docs: list[DocSource] = []
    for doc_id in sorted(chunks):
        if doc_id in denied:
            continue  # a deck tightened on its own (#308), inside a readable collection
        # The document's own order. Nothing orders the chunk read, and passage
        # order decides the order every statement and connection is written in.
        chunks[doc_id].sort()
        try:
            mirror = doc_mirror_fields(spec, doc_id)
        except ResourceIDNotFoundError:
            # Chunks outlive their deck (#104 made a chunk content-addressed), and
            # a vanished deck has no permission to inherit. Narrow on purpose: a
            # bare `except` here would swallow a real fault and quietly preview a
            # subset, which is the one way this tool could lie.
            continue
        docs.append(
            DocSource(
                doc_id=doc_id,
                chunks=[(cid, text) for _, cid, text in chunks[doc_id]],
                mirror=mirror,
            )
        )
    return collection.graph_guidance, docs


def preview_collection(
    spec: SpecStar,
    llm: ILlm,
    collection_id: str,
    *,
    out_dir: Path,
    guidance: str | None = None,
    propose_with: ILlm | None = None,
    as_user: str | None = None,
) -> Graph:
    """Build ``collection_id``'s graph in memory and write it to ``out_dir``.

    ``guidance`` overrides the collection's own, which is how a criterion is
    tried BEFORE anyone commits it to the collection — the whole point of the
    loop. Omitted, the preview runs what production would run today.
    """
    from .link import read_decisions  # noqa: PLC0415 — circular at module level

    stored_guidance, docs = read_collection_sources(spec, collection_id, as_user=as_user)
    graph = build_graph(
        llm,
        docs,
        collection_id=collection_id,
        guidance=stored_guidance if guidance is None else guidance,
        propose_with=propose_with,
        # The merges people have already accepted or refused. Reading them is
        # still only reading — and without them a preview of a corpus anyone
        # has curated shows every accepted merge undone, which a reader
        # diffing two runs would read as something the criterion did.
        decisions=read_decisions(spec),
    )
    write_preview(graph, docs, out_dir=out_dir)
    return graph


def summarise(graph: Graph, docs: list[DocSource]) -> dict[str, Any]:
    """The numbers that say whether the criterion is working.

    ``mentions`` counts ROWS — one per (document, distinct name) — while
    ``distinct_names`` counts how many things the corpus turned out to be about.
    The two diverging is the signal: a corpus where every document contributes a
    fresh batch of names nobody repeats is a corpus whose extractor is naming
    values, labels and passing nouns rather than things.
    """
    documents = len(docs)
    return {
        "documents": documents,
        "chunks": sum(len(d.chunks) for d in docs),
        "mentions": len(graph.mentions),
        "distinct_names": len({m.norm_surface for m in graph.mentions}),
        "mentions_per_document": round(len(graph.mentions) / documents, 2) if documents else 0.0,
        "claims": len(graph.claims),
        "relationships": len(graph.relationships),
        "entities": len(graph.entities),
        "links": len(graph.links),
        "proposals": sum(1 for link in graph.links if link.state == "pending"),
        # Whether anyone ASKED. Zero proposals from a pass that ran says the
        # model found nothing to merge; zero from a pass that never ran says
        # nothing at all — and read as the first, it looks like evidence the
        # criterion is working.
        "proposals_asked": graph.proposed,
        # What the model thought it was looking at, commonest first. "值" / "數值"
        # / "value" near the top is the extractor treating measurements as things.
        "kinds": dict(Counter(m.kind for m in graph.mentions if m.kind).most_common()),
        # Said in the output, not only in a docstring: production groups
        # identities over the WHOLE corpus, and a reader comparing these numbers
        # against the live graph deserves to know which of the two they hold.
        "identity_scope": "this collection only (production groups across the whole corpus)",
    }


def write_preview(graph: Graph, docs: list[DocSource], *, out_dir: Path) -> None:
    """One file per layer, plus the summary.

    Every list is in a deterministic order — mentions and identities by their
    comparison key, statements and connections in document then chunk order — so
    two runs either side of a criterion change diff to the change and not to
    whatever order the store felt like returning.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _dump(out_dir / "summary.json", summarise(graph, docs))
    _dump(out_dir / "mentions.json", [msgspec.structs.asdict(m) for m in graph.mentions])
    _dump(out_dir / "claims.json", [msgspec.structs.asdict(c) for c in graph.claims])
    _dump(out_dir / "relationships.json", [msgspec.structs.asdict(r) for r in graph.relationships])
    _dump(
        out_dir / "entities.json",
        [{"id": eid, **msgspec.structs.asdict(e)} for eid, e in sorted(graph.entities.items())],
    )
    _dump(out_dir / "links.json", [msgspec.structs.asdict(link) for link in graph.links])


def _dump(path: Path, payload: Any) -> None:
    # Indented and non-ASCII-preserving: these files are read by a person, and a
    # corpus written in Chinese would otherwise arrive as escape sequences.
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
