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
            "def authenticate_user(username, password):\n"
            "    return username == password\n"
            "\n\n"
            "def reset_password(user_id):\n"
            "    return True\n"
        ).encode()
        * 4,
    )
    return cid, ing


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

    def spy(collection_ids: list[str], vec: list[float], *, field: str = "embedding") -> list[str]:
        calls.append((field, len(vec)))
        return real(collection_ids, vec, field=field)

    retriever._dense_order = spy  # type: ignore[method-assign]
    retriever.search("authenticate user", collection_ids=[cid])
    # Exactly one query → one dense pass per field = 2 calls. Each call's
    # vector width matches the targeted field.
    by_field = {f: w for f, w in calls}
    assert by_field.get("embedding") == EMBED_DIM, calls
    assert by_field.get("embedding_alt") == CODE_EMBED_DIM, calls
