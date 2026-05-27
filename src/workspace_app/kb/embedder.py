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
    """Turns text into vectors for KB retrieval. Asymmetric: documents and
    queries may carry different instruction prefixes (bge / e5 / qwen3-embedding
    style). Implement the three members to swap the embedding model; inject via
    `create_app(kb_embedder=...)`.
    """

    @property
    def dim(self) -> int:
        """The vector width. MUST equal `EMBED_DIM` (the `DocChunk` Vector
        column size) — query and document vectors are stored/compared at this
        width."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of documents (applying the document-side prefix);
        returns one `dim`-length vector per input, in order."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query (applying the query-side prefix); returns
        one `dim`-length vector."""
        ...


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


class LitellmEmbedder(_PrefixedEmbedder):
    """Production embedder via LiteLLM (local Ollama or hosted). `dim` must match
    the model's output AND the DocChunk Vector dim (KB_EMBED_DIM) — query and
    doc vectors are stored/compared at that width. Asymmetric prefixes (e.g.
    qwen3-embedding's instruction format) are configured by the caller."""

    def __init__(
        self,
        model: str,
        *,
        dim: int,
        query_prefix: str = "",
        doc_prefix: str = "",
        timeout: float = 60.0,
        num_retries: int = 2,
        batch_size: int = 64,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(query_prefix=query_prefix, doc_prefix=doc_prefix)
        self._model = model
        self._dim = dim
        self._timeout = timeout
        self._num_retries = num_retries
        self._batch_size = batch_size
        # Embedder endpoint (separate from the chat LLM). None → Ollama/env.
        self._base_url = base_url
        self._api_key = api_key

    @property
    def dim(self) -> int:
        return self._dim

    def _embed(self, texts: list[str]) -> list[list[float]]:
        # A big document yields many chunks — send them in bounded batches so one
        # request can't be huge (and slow to the point of timing out). Order is
        # preserved across batches.
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            out.extend(self._embed_batch(texts[i : i + self._batch_size]))
        return out

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover — live model
        import litellm

        resp = litellm.embedding(
            model=self._model,
            input=texts,
            timeout=self._timeout,
            num_retries=self._num_retries,
            api_base=self._base_url,
            api_key=self._api_key,
        )
        return [item["embedding"] for item in resp.data]
