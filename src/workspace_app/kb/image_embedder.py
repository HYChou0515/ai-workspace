"""ImageEmbedder Protocol + a hash stub (#513) — turn image bytes into vectors,
the additive third retrieval signal beside the text ``Embedder``.

The sibling of ``embedder.Embedder`` for images: same ``dim`` / ``identity``
contract, but ``embed_documents`` / ``embed_query_image`` take raw image bytes.
``embed_query_text`` is the OPTIONAL CLIP-style text→image query — an image-only
model returns ``None`` and the retriever skips the text-over-image arm; a
shared-space model returns a vector and "type a description, find images" works.

The image embedder is external (a separate team's model); we only define the
seam. ``HashImageEmbedder`` is the deterministic, dependency-free stub for tests
(the precedent being ``HashEmbedder`` / ``MockSandbox``). The production adapter
is injected via ``create_app(kb_image_embedder=...)`` when the model lands.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Protocol


class ImageEmbedder(Protocol):
    """Turns image bytes into vectors for KB retrieval. Implement the members to
    swap the image model; inject via ``create_app(kb_image_embedder=...)``.
    """

    @property
    def dim(self) -> int:
        """The vector width. MUST equal ``IMG_EMBED_DIM`` (the ``DocChunk``
        ``embedding_img`` column size) — its own space, separate from the text
        ``EMBED_DIM``."""
        ...

    @property
    def identity(self) -> str:
        """A stable string that changes iff the DOCUMENT vectors this embedder
        produces could change (the model, plus the vector width). The #390 index
        cache keys on it so swapping the image model forces a real re-embed rather
        than reusing cross-space vectors."""
        ...

    def embed_documents(self, images: list[bytes]) -> list[list[float]]:
        """Embed a batch of images; returns one ``dim``-length vector per input,
        in order."""
        ...

    def embed_query_image(self, image: bytes) -> list[float]:
        """Embed a single query image (image-to-image search)."""
        ...

    def embed_query_text(self, text: str) -> list[float] | None:
        """OPTIONAL text→image query for a shared-space (CLIP-style) model:
        returns a ``dim``-length vector, or ``None`` when the model is image-only
        (then the retriever mounts no text-over-image arm)."""
        ...


class HashImageEmbedder:
    """Deterministic, dependency-free image embedding for offline use and tests:
    same bytes → same vector, different bytes → different vector. ``supports_text``
    toggles the optional text→image capability (off = image-only, like a
    vision-only model)."""

    def __init__(self, dim: int = 64, *, supports_text: bool = False) -> None:
        self._dim = dim
        self._supports_text = supports_text

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def identity(self) -> str:
        return f"hash-img-{self._dim}"

    def embed_documents(self, images: list[bytes]) -> list[list[float]]:
        return [self._vec(b"img\x00" + img) for img in images]

    def embed_query_image(self, image: bytes) -> list[float]:
        # Symmetric: a query image embeds exactly like the same document image.
        return self._vec(b"img\x00" + image)

    def embed_query_text(self, text: str) -> list[float] | None:
        if not self._supports_text:
            return None
        return self._vec(b"txt\x00" + text.encode())

    def _vec(self, payload: bytes) -> list[float]:
        # Expand sha256(payload) to `dim` floats in [-1, 1) by re-hashing with a
        # counter until enough bytes are produced (mirrors HashEmbedder).
        out: list[float] = []
        counter = 0
        while len(out) < self._dim:
            digest = hashlib.sha256(str(counter).encode() + b":" + payload).digest()
            for i in range(0, len(digest), 4):
                if len(out) >= self._dim:
                    break
                (n,) = struct.unpack("<I", digest[i : i + 4])
                out.append(n / 2**31 - 1.0)
            counter += 1
        return out


class PerceptualImageEmbedder:
    """A dependency-light placeholder that clusters *visually similar* images —
    the honest stand-in for the real model (#519) until it lands.

    `HashImageEmbedder` byte-hashes, so only byte-identical images match and
    query-by-image looks dead for a re-saved or cropped copy. This decodes the
    pixels and builds a coarse feature from them: downscale to a small RGB
    square and read the cells out in order. Two pictures that *look* alike land
    close; a mirror or a different colour lands apart. Quality is crude — it is
    average-colour geometry, not semantics — but the socket demonstrably lights
    up, and the real model swaps in behind the same `ImageEmbedder` contract
    with no core change.

    Deterministic (same picture → same vector, regardless of encoding), and
    **image-only**: `embed_query_text` returns None, so the retriever mounts no
    text→image arm and text retrieval stays byte-for-byte unchanged (#513).
    """

    def __init__(self, dim: int = 512) -> None:
        # Downscale to the largest RGB square that fits in `dim` (side²·3 ≤ dim);
        # the cells fill the front of the vector, the tail is zero-padded. RGB
        # (not grayscale) so a red fill and a blue fill point in different
        # directions — grayscale collapses every solid colour onto the same
        # all-ones direction and cosine can't tell them apart. A square keeps
        # left/right + top/bottom structure, so a mirror differs from a single
        # average.
        self._dim = dim
        self._side = max(1, int((dim / 3) ** 0.5))

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def identity(self) -> str:
        # Changes iff the produced vectors could change — width is the only
        # knob. The #390 cache keys on it, forcing a re-embed on a width change.
        return f"perceptual-img-{self._dim}"

    def embed_documents(self, images: list[bytes]) -> list[list[float]]:
        return [self._vec(img) for img in images]

    def embed_query_image(self, image: bytes) -> list[float]:
        return self._vec(image)

    def embed_query_text(self, text: str) -> list[float] | None:
        # Image-only, like a vision-only model — see the class docstring.
        return None

    def _vec(self, image: bytes) -> list[float]:
        cells = self._rgb_cells(image)
        # Cells occupy the front, scaled to [-1, 1]; the tail is zero-padded to
        # exactly `dim`.
        out = [c / 127.5 - 1.0 for c in cells][: self._dim]
        out.extend([0.0] * (self._dim - len(out)))
        return out

    def _rgb_cells(self, image: bytes) -> list[int]:
        """`side*side*3` RGB channel values in [0, 255], row-major RGBRGB….

        Undecodable bytes (a corrupt upload, or a non-image blob) fall back to a
        deterministic hash-derived grid so ingest never crashes on bad input —
        it just won't cluster with anything real, which is correct."""
        try:
            from PIL import Image  # deferred: only imported when a real image lands
        except ImportError:  # pragma: no cover - Pillow is in the lockfile
            return self._hash_grid(image)
        import io

        try:
            img = Image.open(io.BytesIO(image)).convert("RGB").resize((self._side, self._side))
        except Exception:  # noqa: BLE001 - any decode failure → deterministic fallback
            return self._hash_grid(image)
        return list(img.tobytes())  # row-major RGBRGB…, len == side*side*3

    def _hash_grid(self, image: bytes) -> list[int]:
        n = self._side * self._side * 3
        out: list[int] = []
        counter = 0
        while len(out) < n:
            digest = hashlib.sha256(str(counter).encode() + b":" + image).digest()
            out.extend(digest)
            counter += 1
        return out[:n]
