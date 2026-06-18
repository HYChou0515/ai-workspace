"""P3.0 §2.9 D1: per-collection embedder + dual-vector DocChunks.

A code Collection (``embedder_id=1``) routes its chunks through the
**code** embedder and writes vectors into ``DocChunk.embedding_alt``
(leaving ``embedding`` empty). A regular doc Collection
(``embedder_id=0``, the default) keeps the existing behaviour: vectors
on ``embedding``, ``embedding_alt`` left None/empty.

This lets the Retriever fan out across both vector fields in parallel
and merge via RRF — covered in a separate cycle.
"""

from __future__ import annotations

from specstar import QB, SpecStar

from workspace_app.factories import Settings, get_code_embedder
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources.kb import (
    CODE_EMBED_DIM,
    EMBED_DIM,
    Collection,
    DocChunk,
)


def _chunks_of(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    return [r.data for r in rm.list_resources((QB["source_doc_id"] == doc_id).build())]  # ty: ignore[invalid-return-type]


def test_code_collection_embeddings_land_on_embedding_alt(spec: SpecStar):
    """Collection.embedder_id=1 → code embedder is used + vectors land on
    embedding_alt (default `embedding` field stays empty)."""
    doc_embedder = HashEmbedder(dim=EMBED_DIM)
    code_embedder = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    pipeline = build_doc_pipeline(embedder=doc_embedder)
    ingestor = Ingestor(
        spec,
        pipeline=pipeline,
        embedder=doc_embedder,
        code_embedder=code_embedder,
    )

    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="my-code", embedder_id=1))
        .resource_id
    )
    py = (
        "def greet(name: str) -> str:\n"
        "    return f'hello, {name}!'\n"
        "\n\n"
        "def farewell(name: str) -> str:\n"
        "    return f'bye, {name}!'\n"
    ) * 4
    ingestor.ingest(collection_id=cid, user="alice", filename="g.py", data=py.encode())

    chunks = _chunks_of(spec, encode_doc_id(cid, "g.py"))
    assert chunks
    for c in chunks:
        assert c.embedding is None, "code collection must NOT populate the default field"
        assert c.embedding_alt is not None and len(c.embedding_alt) == CODE_EMBED_DIM


def test_code_embedder_factory_returns_none_when_unconfigured():
    """The default Settings doesn't wire a code embedder (the FE flag is
    opt-in per Collection); factory returns None and the Ingestor + Retriever
    paths fall back to single-vector behaviour."""
    assert get_code_embedder(Settings()) is None


def test_code_embedder_factory_returns_embedder_when_configured():
    """kb.code_embedder.model set → factory constructs a LitellmEmbedder
    at the CODE_EMBED_DIM width."""
    from dataclasses import replace

    base = Settings()
    settings = replace(
        base,
        kb=replace(
            base.kb,
            code_embedder=replace(base.kb.code_embedder, model="ollama/nomic-embed-code"),
        ),
    )
    e = get_code_embedder(settings)
    assert e is not None and e.dim == CODE_EMBED_DIM


def test_doc_collection_default_routing_unchanged(spec: SpecStar):
    """Collection.embedder_id=0 (the default) keeps the legacy behaviour:
    chunks have a populated `embedding` and `embedding_alt` left empty/None."""
    doc_embedder = HashEmbedder(dim=EMBED_DIM)
    code_embedder = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    pipeline = build_doc_pipeline(embedder=doc_embedder)
    ingestor = Ingestor(
        spec,
        pipeline=pipeline,
        embedder=doc_embedder,
        code_embedder=code_embedder,
    )

    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="docs"))  # embedder_id defaults to 0
        .resource_id
    )
    ingestor.ingest(
        collection_id=cid,
        user="alice",
        filename="x.md",
        data=b"# H\n\nSome body content explaining the system.",
    )
    chunks = _chunks_of(spec, encode_doc_id(cid, "x.md"))
    assert chunks
    for c in chunks:
        assert len(c.embedding) == EMBED_DIM  # ty: ignore[invalid-argument-type]
        # Default collection → no alt vector written.
        assert c.embedding_alt in (None, [])
