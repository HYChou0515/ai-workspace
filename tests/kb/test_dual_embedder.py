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


def test_image_embedder_factory_returns_none_by_default():
    """#513 P2: the image model is an external-team deliverable, so the factory
    returns None and retrieval stays text-only until it's wired in P4."""
    from workspace_app.factories import get_image_embedder

    assert get_image_embedder(Settings()) is None


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


# ── #389: per-file embedder routing (a collection can mix prose + code) ──


def _dual_ingestor(spec: SpecStar, *, with_code_embedder: bool) -> Ingestor:
    doc_embedder = HashEmbedder(dim=EMBED_DIM)
    code_embedder = (
        HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ") if with_code_embedder else None
    )
    return Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=doc_embedder),
        embedder=doc_embedder,
        code_embedder=code_embedder,
    )


_PY_SRC = (
    "def greet(name: str) -> str:\n    return f'hi {name}'\n\n\n"
    "def bye(name: str) -> str:\n    return f'bye {name}'\n"
) * 4


def test_code_file_routes_to_code_embedder_without_embedder_id(spec: SpecStar):
    """#389: a code file uses the code embedder → `embedding_alt`, EVEN in a
    default (embedder_id=0) collection — routing is per-file, not per-collection,
    so a mixed collection can hold prose and code together."""
    ingestor = _dual_ingestor(spec, with_code_embedder=True)
    cid = spec.get_resource_manager(Collection).create(Collection(name="mixed")).resource_id
    ingestor.ingest(collection_id=cid, user="a", filename="app.py", data=_PY_SRC.encode())
    chunks = _chunks_of(spec, encode_doc_id(cid, "app.py"))
    assert chunks
    for c in chunks:
        assert c.embedding is None, "code file → code embedder, not the default field"
        assert c.embedding_alt is not None and len(c.embedding_alt) == CODE_EMBED_DIM


def test_prose_and_code_split_across_vector_fields_in_one_collection(spec: SpecStar):
    """The mixed-collection payoff: prose lands on `embedding` (text model) and
    code on `embedding_alt` (code model) within the SAME default collection."""
    ingestor = _dual_ingestor(spec, with_code_embedder=True)
    cid = spec.get_resource_manager(Collection).create(Collection(name="mixed")).resource_id
    ingestor.ingest(
        collection_id=cid, user="a", filename="README.md", data=b"# Guide\n\nHow it works."
    )
    ingestor.ingest(collection_id=cid, user="a", filename="app.py", data=_PY_SRC.encode())

    prose = _chunks_of(spec, encode_doc_id(cid, "README.md"))
    code = _chunks_of(spec, encode_doc_id(cid, "app.py"))
    assert prose and code
    for c in prose:  # prose → text embedder on the default field
        assert len(c.embedding) == EMBED_DIM  # ty: ignore[invalid-argument-type]
        assert not c.embedding_alt
    for c in code:  # code → code embedder on the alt field
        assert c.embedding is None
        assert len(c.embedding_alt) == CODE_EMBED_DIM  # ty: ignore[invalid-argument-type]


def test_code_file_degrades_to_text_embedder_when_no_code_embedder(spec: SpecStar):
    """#389 decision: with NO code embedder configured, a code file must not
    crash — it degrades to the text embedder (its path>symbol breadcrumb is the
    semantic anchor), landing on the default `embedding` field."""
    ingestor = _dual_ingestor(spec, with_code_embedder=False)
    cid = spec.get_resource_manager(Collection).create(Collection(name="plain")).resource_id
    ingestor.ingest(collection_id=cid, user="a", filename="app.py", data=_PY_SRC.encode())
    chunks = _chunks_of(spec, encode_doc_id(cid, "app.py"))
    assert chunks
    for c in chunks:
        assert len(c.embedding) == EMBED_DIM  # ty: ignore[invalid-argument-type]
        assert c.embedding_alt in (None, [])


def test_embedder_id_override_forces_alt_for_prose_too(spec: SpecStar):
    """The collection-level `embedder_id` override is preserved: it forces the
    code embedder for EVERY doc, prose included (back-compat)."""
    ingestor = _dual_ingestor(spec, with_code_embedder=True)
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="all-code", embedder_id=1))
        .resource_id
    )
    ingestor.ingest(
        collection_id=cid, user="a", filename="notes.md", data=b"# H\n\nplain prose only."
    )
    chunks = _chunks_of(spec, encode_doc_id(cid, "notes.md"))
    assert chunks
    for c in chunks:
        assert c.embedding is None
        assert c.embedding_alt is not None and len(c.embedding_alt) == CODE_EMBED_DIM
