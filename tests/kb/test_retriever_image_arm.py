"""#513 P2 — the additive text→image retrieval arm.

When an ImageEmbedder is wired AND it supports text queries (CLIP-style shared
space), the dense fan-out gains a third arm over ``embedding_img`` — mirroring
the code-embedder → ``embedding_alt`` fan-out. Doubly gated: no image embedder,
or an image-only model, mounts NO arm, so the text path is byte-for-byte
unchanged (the additive guarantee).
"""

from __future__ import annotations

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.image_embedder import HashImageEmbedder
from workspace_app.kb.retriever import Retriever
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, IMG_EMBED_DIM, Collection, DocChunk


def _coll(spec) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="d")).resource_id


def _chunk(spec, cid, text, *, embedding=None, embedding_img=None) -> None:
    spec.get_resource_manager(DocChunk).create(
        DocChunk(
            collection_id=cid,
            seq=0,
            start=0,
            end=len(text),
            text=text,
            embedding=embedding,
            embedding_img=embedding_img,
        )
    )


def _spy_fields(r: Retriever, query: str, cids: list[str]) -> dict[str, int]:
    """Run a search recording (field → vector width) for each dense pass."""
    calls: list[tuple[str, int]] = []
    real = r._dense_order

    def spy(collection_ids, vec, *, field="embedding", **kw):
        # **kw so the spy records the fan-out without pinning the scope kwargs — a new
        # retrieval filter is a change to the query, not to what this test asserts.
        calls.append((field, len(vec)))
        return real(collection_ids, vec, field=field, **kw)

    r._dense_order = spy  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    r.search(query, collection_ids=cids)
    return {f: w for f, w in calls}


def test_image_only_embedder_mounts_no_text_to_image_arm():
    # An image-only model (embed_query_text → None) adds no arm: the dense passes
    # are exactly the text path's, so behaviour is unchanged.
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    text_emb = HashEmbedder(dim=EMBED_DIM)
    _chunk(spec, cid, "authenticate the user", embedding=text_emb.embed_documents(["x"])[0])
    r = Retriever(
        spec,
        embedder=text_emb,
        image_embedder=HashImageEmbedder(dim=IMG_EMBED_DIM, supports_text=False),
    )
    assert "embedding_img" not in _spy_fields(r, "authenticate", [cid])


def test_text_capable_image_embedder_mounts_the_arm():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    text_emb = HashEmbedder(dim=EMBED_DIM)
    _chunk(spec, cid, "authenticate the user", embedding=text_emb.embed_documents(["x"])[0])
    r = Retriever(
        spec,
        embedder=text_emb,
        image_embedder=HashImageEmbedder(dim=IMG_EMBED_DIM, supports_text=True),
    )
    fields = _spy_fields(r, "authenticate", [cid])
    assert fields.get("embedding_img") == IMG_EMBED_DIM  # the image arm ran, right width
    assert fields.get("embedding") == EMBED_DIM  # and the text arm still runs alongside


def test_image_only_embedder_leaves_results_byte_for_byte_unchanged():
    # The additive guarantee, end-to-end over a real ingested corpus: wiring an
    # image-only embedder must not change what a text search returns.
    from workspace_app.kb.ingest import Ingestor
    from workspace_app.kb.li_pipeline import build_doc_pipeline

    spec = make_spec(default_user="u")
    text_emb = HashEmbedder(dim=EMBED_DIM)
    cid = _coll(spec)
    Ingestor(spec, pipeline=build_doc_pipeline(embedder=text_emb), embedder=text_emb).ingest(
        collection_id=cid,
        user="alice",
        filename="notes.txt",
        data=b"authenticate the user with a password\nreset the password on request\n" * 4,
    )
    q = "how does authentication work"
    base = [h.text for h in Retriever(spec, embedder=text_emb).search(q, collection_ids=[cid])]
    with_img_only = [
        h.text
        for h in Retriever(
            spec,
            embedder=text_emb,
            image_embedder=HashImageEmbedder(dim=IMG_EMBED_DIM, supports_text=False),
        ).search(q, collection_ids=[cid])
    ]
    assert base and with_img_only == base  # identical results — nothing added
