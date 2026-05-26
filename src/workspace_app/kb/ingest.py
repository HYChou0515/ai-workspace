"""Ingestion — turn an upload into a SourceDoc + its embedded DocChunks.

Pipeline: bytes → (sniff content-type) → SourceDoc (natural-key resource id, the
original bytes kept as a Binary blob) → canonical text → Chunker → Embedder →
DocChunks. Archives (zip/tar) are unpacked and each md/txt member ingested.
"""

from __future__ import annotations

import io
import logging
import tarfile
import zipfile

import magic
import msgspec
import xxhash
from specstar import QB, SpecStar
from specstar.types import Binary, ResourceIDNotFoundError

from ..resources.kb import DocChunk, SourceDoc
from .chunker import Chunker
from .doc_id import encode_doc_id
from .embedder import Embedder

logger = logging.getLogger(__name__)

# md sniffs as text/plain on libmagic; both accepted.
_TEXT_MIMES = {"text/plain", "text/markdown"}
_ARCHIVE_MIMES = {"application/zip", "application/x-tar", "application/gzip"}


def normalize_text(raw: str) -> str:
    """Canonical text: strip a leading BOM and normalize line endings, so chunk
    offsets are stable and rendering is consistent."""
    return raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


class Ingestor:
    def __init__(self, spec: SpecStar, *, chunker: Chunker, embedder: Embedder) -> None:
        self._spec = spec
        self._chunker = chunker
        self._embedder = embedder

    def ingest(self, *, collection_id: str, user: str, filename: str, data: bytes) -> list[str]:
        """Store + index synchronously; returns the SourceDoc ids touched.

        The synchronous path (tests, scripts). The API stores first (fast) and
        indexes in the background — see `store` / `index`."""
        touched = self.store(collection_id=collection_id, user=user, filename=filename, data=data)
        for doc_id in touched:
            self.index(doc_id)
        return touched

    def store(self, *, collection_id: str, user: str, filename: str, data: bytes) -> list[str]:
        """Fast path: persist the SourceDoc(s) as ``status="indexing"`` and
        return the ids that need indexing (new or changed). No chunking/embedding
        — that's the slow `index` step.

        A zip/tar(.gz) is unpacked and each md/txt member stored at its
        archive-relative path; a lone file is stored if it's md/txt. Other types
        are skipped. Content type is sniffed via magic, not the extension."""
        mime = magic.from_buffer(data, mime=True)
        members = self._extract(mime, data) if mime in _ARCHIVE_MIMES else [(filename, data)]
        touched: list[str] = []
        for path, member in members:
            if magic.from_buffer(member, mime=True) not in _TEXT_MIMES:
                continue
            doc_id = self._store_file(collection_id, user, path, member)
            if doc_id is not None:
                touched.append(doc_id)
        return touched

    def index(self, doc_id: str) -> None:
        """Slow path: (re)build a stored doc's chunks — chunk + embed — then
        flip its status to ``ready`` (``error`` if embedding fails). Safe to run
        off the request thread."""
        drm = self._spec.get_resource_manager(SourceDoc)
        doc = drm.get(doc_id).data
        assert isinstance(doc, SourceDoc)
        raw = drm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        try:
            self._delete_chunks(doc_id)
            self._index(doc.collection_id, doc_id, raw)
            status = "ready"
        except Exception:  # noqa: BLE001 — surface failure as doc status, don't crash the worker
            status = "error"
            # Don't lose the cause: the status flip alone is opaque (a missing
            # embedding model, a dim mismatch, …). Log the traceback so it's
            # visible in the server logs instead of a silent "error" badge.
            logger.exception("indexing failed for %s", doc_id)
        drm.update(doc_id, msgspec.structs.replace(doc, status=status))

    @staticmethod
    def _extract(mime: str, data: bytes) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        if mime == "application/zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        out.append((info.filename, z.read(info)))
        else:  # tar or tar.gz — let tarfile auto-detect compression
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
                for m in t.getmembers():
                    if m.isfile():
                        f = t.extractfile(m)
                        assert f is not None  # isfile() ⇒ extractable
                        out.append((m.name, f.read()))
        return out

    def _store_file(self, collection_id: str, user: str, path: str, data: bytes) -> str | None:
        # specstar resource ids can't contain '/', so the natural key is
        # percent-encoded into a slash-free, reversible id.
        doc_id = encode_doc_id(collection_id, user, path)
        drm = self._spec.get_resource_manager(SourceDoc)
        try:
            existing = drm.get(doc_id).data
        except ResourceIDNotFoundError:
            existing = None
        # Identical bytes already at this id → no-op (don't churn a revision).
        if existing is not None and existing.content.file_id == xxhash.xxh3_128_hexdigest(data):
            return None
        doc = SourceDoc(
            collection_id=collection_id, path=path, content=Binary(data=data), status="indexing"
        )
        if existing is None:
            drm.create(doc, resource_id=doc_id)
        else:
            # Changed content → new revision in place; index() rebuilds the chunks.
            drm.update(doc_id, doc)
        return doc_id

    def _delete_chunks(self, doc_id: str) -> None:
        # Hard-delete: chunks are derived & current-only; a soft delete would
        # leave them in queries / vector search.
        chrm = self._spec.get_resource_manager(DocChunk)
        for r in chrm.list_resources((QB["source_doc_id"] == doc_id).build()):
            chrm.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]

    def _index(self, collection_id: str, doc_id: str, data: bytes) -> None:
        text = normalize_text(data.decode("utf-8", errors="replace"))
        chunks = self._chunker.chunk(text)
        vectors = self._embedder.embed_documents([c.text for c in chunks])
        chrm = self._spec.get_resource_manager(DocChunk)
        for c, vec in zip(chunks, vectors, strict=True):
            chrm.create(
                DocChunk(
                    collection_id=collection_id,
                    source_doc_id=doc_id,
                    seq=c.seq,
                    start=c.start,
                    end=c.end,
                    text=c.text,
                    embedding=vec,
                )
            )
