"""Embedder Protocol + implementations — turn text into vectors. Pluggable and
asymmetric: queries and documents can carry different instruction prefixes
(needed by models like bge / e5 / qwen3-embedding). We embed ourselves (not
specstar's auto-encoder) so prefixes are under our control; the raw vectors are
stored in the DocChunk Vector field.

`HashEmbedder` is a deterministic, dependency-free embedder (offline / tests),
the precedent being MockSandbox. `LitellmEmbedder` is the production path
(local Ollama or hosted, via LiteLLM).
"""

from __future__ import annotations

import hashlib
import struct
from typing import Protocol


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class _PrefixedEmbedder:
    """Applies the asymmetric query/document instruction prefixes, then delegates
    raw embedding to `_embed`. Subclasses implement `_embed` + `dim`."""

    def __init__(self, *, query_prefix: str = "", doc_prefix: str = "") -> None:
        self._query_prefix = query_prefix
        self._doc_prefix = doc_prefix

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self._doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._query_prefix + text])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError  # pragma: no cover — overridden


class HashEmbedder(_PrefixedEmbedder):
    """Deterministic, dependency-free embedding for offline use and tests:
    same text → same vector, different text → different vector."""

    def __init__(self, dim: int = 64, **kw: str) -> None:
        super().__init__(**kw)
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        # Expand sha256(text) to `dim` floats in [-1, 1) by re-hashing with a
        # counter until enough bytes are produced.
        out: list[float] = []
        counter = 0
        while len(out) < self._dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            for i in range(0, len(digest), 4):
                if len(out) >= self._dim:
                    break
                (n,) = struct.unpack("<I", digest[i : i + 4])
                out.append(n / 2**31 - 1.0)
            counter += 1
        return out
