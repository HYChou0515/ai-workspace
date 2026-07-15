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
