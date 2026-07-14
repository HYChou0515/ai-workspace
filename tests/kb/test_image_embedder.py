"""#513 P2 — ImageEmbedder protocol + a deterministic hash stub for tests.

Mirrors the text ``Embedder`` but takes image bytes. ``embed_query_text`` is the
optional CLIP-style text→image query: an image-only model returns ``None`` and
the retriever then skips the text→image arm.
"""

from workspace_app.kb.image_embedder import HashImageEmbedder


def test_hash_image_embedder_is_deterministic_and_right_width():
    emb = HashImageEmbedder(dim=32)
    vecs = emb.embed_documents([b"img-a", b"img-b"])
    assert len(vecs) == 2
    assert all(len(v) == 32 for v in vecs)
    # same bytes → same vector; a query image embeds like the document.
    assert emb.embed_query_image(b"img-a") == vecs[0]
    assert emb.embed_query_image(b"img-b") != vecs[0]


def test_hash_image_embedder_reports_dim_and_identity():
    emb = HashImageEmbedder(dim=48)
    assert emb.dim == 48
    assert isinstance(emb.identity, str) and emb.identity


def test_hash_image_embedder_is_image_only_by_default():
    # No text→image capability → embed_query_text returns None so the retriever
    # never mounts a text arm over the image vectors.
    emb = HashImageEmbedder(dim=32)
    assert emb.embed_query_text("dark round particle") is None


def test_hash_image_embedder_can_opt_into_text_queries():
    emb = HashImageEmbedder(dim=32, supports_text=True)
    v = emb.embed_query_text("dark round particle")
    assert v is not None
    assert len(v) == 32


def test_create_app_accepts_kb_image_embedder():
    """create_app(kb_image_embedder=...) threads the image embedder into the KB
    retriever without error — the P2 socket is reachable from the composition
    root (defaults to None, so existing deploys are unaffected)."""
    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.resources import make_spec
    from workspace_app.resources.kb import EMBED_DIM
    from workspace_app.sandbox.mock import MockSandbox

    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_image_embedder=HashImageEmbedder(),
    )
    assert app is not None  # construction succeeded with the image embedder wired
