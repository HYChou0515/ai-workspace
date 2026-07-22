"""`get_image_embedder(settings)` selects the image embedder by config (#519).

The socket has been None-hardcoded since #513, so query-by-image never ran.
`kb.image_embedder.kind` now picks the backend: `none` keeps the text-only path
byte-for-byte, `perceptual` wires the dependency-light placeholder so
query-by-image works out of the box, `hash` is the byte-hash test stub.
"""

from __future__ import annotations

from workspace_app.config.schema import Settings
from workspace_app.factories import get_image_embedder
from workspace_app.kb.image_embedder import HashImageEmbedder, PerceptualImageEmbedder
from workspace_app.resources.kb import IMG_EMBED_DIM


def _settings(kind: str) -> Settings:
    s = Settings()
    # frozen dataclasses — rebuild the nested kb.image_embedder with the kind.
    import dataclasses

    ie = dataclasses.replace(s.kb.image_embedder, kind=kind)
    kb = dataclasses.replace(s.kb, image_embedder=ie)
    return dataclasses.replace(s, kb=kb)


def test_none_keeps_the_text_only_path():
    assert get_image_embedder(_settings("none")) is None


def test_perceptual_is_wired_at_the_right_width():
    emb = get_image_embedder(_settings("perceptual"))
    assert isinstance(emb, PerceptualImageEmbedder)
    # MUST match the DocChunk.embedding_img column, or vectors won't store.
    assert emb.dim == IMG_EMBED_DIM


def test_hash_stub_is_available_for_tests():
    emb = get_image_embedder(_settings("hash"))
    assert isinstance(emb, HashImageEmbedder)
    assert emb.dim == IMG_EMBED_DIM


def test_default_is_none_so_existing_deploys_are_unchanged():
    # A bare Settings() must not suddenly start image-embedding — the field is
    # additive and off unless a deployment opts in.
    assert get_image_embedder(Settings()) is None
