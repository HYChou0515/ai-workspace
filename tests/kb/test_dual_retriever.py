"""P3.0 §2.9 D1 + parallel: dense fan-out across both vector fields.

A search over collections mixing default + code collections must hit
BOTH ``embedding`` (text model) AND ``embedding_alt`` (code model) in
parallel and RRF the two ranked lists together with BM25. Code chunks
have no ``embedding`` (only ``embedding_alt``), so the legacy single-
field dense pass would silently miss every code result — this test
locks the cross-field behaviour in.
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import CODE_EMBED_DIM, EMBED_DIM, Collection


def _ingest_code(spec: SpecStar, name: str) -> tuple[str, Ingestor]:
    """Build a code collection with a couple of `.py` files and return
    (collection_id, ingestor)."""
    doc_embedder = HashEmbedder(dim=EMBED_DIM)
    code_embedder = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    pipeline = build_doc_pipeline(embedder=doc_embedder)
    ing = Ingestor(spec, pipeline=pipeline, embedder=doc_embedder, code_embedder=code_embedder)
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, embedder_id=1))
        .resource_id
    )
    ing.ingest(
        collection_id=cid,
        user="alice",
        filename="auth.py",
        data=(
            b"def authenticate_user(username, password):\n"
            b"    return username == password\n"
            b"\n\n"
            b"def reset_password(user_id):\n"
            b"    return True\n"
        )
        * 4,
    )
    return cid, ing


def test_overlay_in_a_code_collection_ranks_on_embedding_alt(spec: SpecStar):
    """#328 overlay over a code collection: the in-memory dense order reads the
    code vector field (``embedding_alt``) for the virtual chunk and skips the
    empty text field — mirroring the stored-vector fan-out, so a dry-run preview
    works for code chunks too."""
    from workspace_app.kb.doc_id import encode_doc_id
    from workspace_app.kb.retriever import Overlay
    from workspace_app.resources.kb import DocChunk

    cid, _ = _ingest_code(spec, "code-overlay")
    code_embedder = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    doc_id = encode_doc_id(cid, "auth.py")

    vtext = "def widget_factory(): return Widget()"
    virtual = DocChunk(
        collection_id=cid,
        source_doc_id=doc_id,
        seq=0,
        start=0,
        end=len(vtext),
        text=vtext,
        embedding_alt=code_embedder.embed_documents([vtext])[0],
    )
    r = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM), code_embedder=code_embedder)
    hits = r.search(
        vtext,
        [cid],
        overlay=Overlay(virtual_chunks=[virtual], shadow_doc_id=doc_id, virtual_text=vtext),
    )
    assert any("widget" in h.text for h in hits)


def test_retriever_returns_code_passages_for_code_only_collection(spec: SpecStar):
    """A query against a code-only collection still returns passages —
    proving the dense pass uses `embedding_alt` (not `embedding`) when
    every chunk's text vector is None."""
    cid, _ = _ingest_code(spec, "code-only")
    code_embedder = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    retriever = Retriever(spec, embedder=code_embedder, code_embedder=code_embedder)
    hits = retriever.search("authenticate user", collection_ids=[cid])
    assert hits, "code-only collection must return passages via embedding_alt fan-out"
    for h in hits:
        assert h.collection_id == cid
        assert "def authenticate_user" in h.text or "def reset_password" in h.text


def test_hyde_pass_also_fans_out_to_alt_field(spec: SpecStar):
    """HyDE (LLM-generated pseudo-document) gets re-embedded with the code
    embedder too — covering the alt-field branch inside the HyDE block."""
    from collections.abc import Iterator

    from workspace_app.kb.llm import ILlm

    class _FakeLlm(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield ("// some plausible code", False)

    from workspace_app.kb.retriever import Enhancements

    cid, _ = _ingest_code(spec, "code-only-hyde")
    code_embedder = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    text_embedder = HashEmbedder(dim=EMBED_DIM)
    retriever = Retriever(spec, embedder=text_embedder, code_embedder=code_embedder, llm=_FakeLlm())
    # Bundled defaults ship `hyde=0`; raise it explicitly so the alt-field
    # HyDE branch this test covers actually fires.
    hits = retriever.search(
        "authenticate user", collection_ids=[cid], enhancements=Enhancements(hyde=1)
    )
    assert hits


def test_dense_pass_fans_out_to_both_vector_fields(spec: SpecStar):
    """The dense pass must hit both `embedding` and `embedding_alt`.

    Implementation guarantee: when a `code_embedder` is wired, the
    retriever issues a dense rank per query for each vector field — so
    the spy records 2 calls (one per field), not 1. This is the lock-in
    for the §2.9 D1+parallel fan-out; without it, code chunks would only
    surface via BM25 (no semantic recall)."""
    cid, _ = _ingest_code(spec, "code-only-2")
    code_embedder = HashEmbedder(dim=CODE_EMBED_DIM, doc_prefix="code: ")
    text_embedder = HashEmbedder(dim=EMBED_DIM)
    retriever = Retriever(spec, embedder=text_embedder, code_embedder=code_embedder)

    calls: list[tuple[str, int]] = []
    real = retriever._dense_order

    def spy(
        collection_ids: list[str], vec: list[float], *, field: str = "embedding", location=None
    ) -> list[str]:
        calls.append((field, len(vec)))
        return real(collection_ids, vec, field=field, location=location)

    retriever._dense_order = spy  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    retriever.search("authenticate user", collection_ids=[cid])
    # Exactly one query → one dense pass per field = 2 calls. Each call's
    # vector width matches the targeted field.
    by_field = {f: w for f, w in calls}
    assert by_field.get("embedding") == EMBED_DIM, calls
    assert by_field.get("embedding_alt") == CODE_EMBED_DIM, calls
