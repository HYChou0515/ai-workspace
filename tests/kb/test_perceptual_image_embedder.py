"""PerceptualImageEmbedder (#519) — a dependency-free placeholder that actually
clusters visually-similar images, so query-by-image demonstrably works before
the real model lands.

The existing ``HashImageEmbedder`` byte-hashes, so only *byte-identical* images
match — a re-saved or cropped copy is an orthogonal random vector, and
query-by-image looks dead. This one downscales to a small grayscale grid, so two
images that *look* alike land close. Quality is crude (no semantics); the point
is the socket demonstrably lights up and swaps for the real model unchanged.
"""

from __future__ import annotations

import io

import pytest

from workspace_app.kb.image_embedder import PerceptualImageEmbedder

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _gradient(flip: bool = False) -> bytes:
    img = Image.new("L", (64, 64))
    px = img.load()
    for y in range(64):
        for x in range(64):
            px[x, y] = (63 - x if flip else x) * 4
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _cos(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def test_reports_dim_and_stable_identity():
    emb = PerceptualImageEmbedder(dim=256)
    assert emb.dim == 256
    assert isinstance(emb.identity, str) and emb.identity
    # identity changes with the width (the #390 cache keys on it).
    assert PerceptualImageEmbedder(dim=64).identity != emb.identity


def test_is_deterministic():
    emb = PerceptualImageEmbedder(dim=256)
    png = _png((120, 30, 30))
    assert emb.embed_documents([png])[0] == emb.embed_query_image(png)


def test_similar_images_are_closer_than_dissimilar_ones():
    """The whole reason this exists instead of the byte-hash stub."""
    emb = PerceptualImageEmbedder(dim=256)
    red = emb.embed_query_image(_png((200, 40, 40)))
    red_ish = emb.embed_query_image(_png((190, 55, 50)))  # nearly the same
    blue = emb.embed_query_image(_png((40, 40, 200)))  # very different

    assert _cos(red, red_ish) > _cos(red, blue)


def test_a_re_encoded_copy_still_matches():
    """Byte-hash fails this: re-saving changes the bytes. Perceptual doesn't —
    the pixels are what it looks at."""
    emb = PerceptualImageEmbedder(dim=256)
    original = _png((80, 160, 90))
    # Round-trip through PIL (re-encode) — different bytes, same picture.
    reencoded_img = Image.open(io.BytesIO(original)).convert("RGB")
    buf = io.BytesIO()
    reencoded_img.save(buf, format="PNG", optimize=True)
    reencoded = buf.getvalue()
    assert reencoded != original  # genuinely different bytes

    assert _cos(emb.embed_query_image(original), emb.embed_query_image(reencoded)) > 0.99


def test_orientation_matters():
    """A mirror image is a different picture and should not be identical —
    distinguishes real perceptual content from a size-only average."""
    emb = PerceptualImageEmbedder(dim=256)
    left = emb.embed_query_image(_gradient(flip=False))
    right = emb.embed_query_image(_gradient(flip=True))
    assert left != right


def test_is_image_only():
    """Image-only (no CLIP text→image), so a text query mounts no image arm and
    text retrieval stays byte-for-byte unchanged (#513)."""
    emb = PerceptualImageEmbedder(dim=256)
    assert emb.embed_query_text("a red square") is None


def test_undecodable_bytes_do_not_crash():
    """Ingest must not blow up on a corrupt or non-image byte blob — it falls
    back to a deterministic vector so the pipeline keeps moving."""
    emb = PerceptualImageEmbedder(dim=256)
    v = emb.embed_documents([b"not an image at all"])[0]
    assert len(v) == 256
