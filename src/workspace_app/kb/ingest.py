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
from typing import Any

import magic
import msgspec
import xxhash

# LI types — only imported when a pipeline is wired (production path); kept
# behind a TYPE_CHECKING / local import would muddy mypy. The package is a
# regular dep now (see pyproject.toml) so just import.
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.schema import Document
from specstar import QB, SpecStar
from specstar.types import Binary, ResourceIDNotFoundError

from ..resources.kb import Collection, DocChunk, SourceDoc
from .chunker import Chunker
from .doc_id import encode_doc_id
from .embedder import Embedder
from .li_pipeline import reader_for

logger = logging.getLogger(__name__)

# md sniffs as text/plain on libmagic; both accepted.
_TEXT_MIMES = {"text/plain", "text/markdown"}
# Binary types we can extract text from via a LI Reader (P1+). The store
# layer accepts these only when a `pipeline` is wired — the legacy chunker
# path stays text-only.
_BINARY_MIMES_VIA_READER = {
    "application/pdf",
    "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_ARCHIVE_MIMES = {"application/zip", "application/x-tar", "application/gzip"}
# Source-code files we accept by extension when a pipeline is wired. libmagic
# usually classifies these as `text/x-script.python`, `text/x-c`, etc. — not
# in `_TEXT_MIMES`. The pipeline's DispatchSplitter routes them to LI's
# CodeSplitter (tree-sitter, function-boundary aware).
_CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}


def normalize_text(raw: str) -> str:
    """Canonical text: strip a leading BOM and normalize line endings, so chunk
    offsets are stable and rendering is consistent."""
    return raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


class Ingestor:
    def __init__(
        self,
        spec: SpecStar,
        *,
        chunker: Chunker | None = None,
        embedder: Embedder,
        pipeline: IngestionPipeline | None = None,
        chat_pipeline: IngestionPipeline | None = None,
        code_embedder: Embedder | None = None,
    ) -> None:
        """Doc-ingest mode (P1):
        - **`pipeline`** (production): LlamaIndex `IngestionPipeline`
          encapsulates chunking + embedding for uploaded docs.
        - **`chunker`** (legacy): manual `Chunker.chunk(text)` + then
          `embedder.embed_documents(...)`. Tests + offline path use this.
          Exactly one of `chunker` / `pipeline` should be set.

        Chat-ingest mode (P2):
        - **`chat_pipeline`**: a separate `IngestionPipeline` whose
          transformations include `InsightExtractor` (LLM-driven). Required
          for `ingest_chat`; None = chat → knowledge disabled."""
        self._spec = spec
        self._chunker = chunker
        self._embedder = embedder
        self._pipeline = pipeline
        self._chat_pipeline = chat_pipeline
        # P3.0: an optional code-specialised embedder. When the Collection's
        # embedder_id != 0, chunks are routed through this embedder and the
        # vector lands on DocChunk.embedding_alt instead of .embedding.
        self._code_embedder = code_embedder

    def ingest(self, *, collection_id: str, user: str, filename: str, data: bytes) -> list[str]:
        """Store + index synchronously; returns the SourceDoc ids touched.

        The synchronous path (tests, scripts). The API stores first (fast) and
        indexes in the background — see `store` / `index`."""
        touched = self.store(collection_id=collection_id, user=user, filename=filename, data=data)
        for doc_id in touched:
            self.index(doc_id)
        return touched

    def ingest_chat(
        self,
        *,
        collection_id: str,
        user: str,
        investigation_id: str,
        investigation_title: str,
        messages: list[dict[str, Any]],
    ) -> list[str]:
        """P2: extract insights from a RCA chat and write each as a markdown
        SourceDoc in the insights collection.

        Runs the **chat** pipeline (must be wired): the conversation is
        serialised into one Document, the pipeline's InsightExtractor calls
        the LLM to produce N insight markdown bodies, those flow through
        the splitter + embedder, and the result is N SourceDocs (one per
        insight) at deterministic paths `{investigation_id}/insight-{seq}.md`
        — so re-promoting overwrites in place rather than duplicating.

        Returns the SourceDoc ids written (`[]` for an inconclusive chat
        where the LLM returned no insights)."""
        assert self._chat_pipeline is not None, "ingest_chat requires a chat_pipeline"
        from .insight_extractor import conversation_to_extraction_doc

        doc = conversation_to_extraction_doc(
            investigation_id=investigation_id,
            title=investigation_title,
            messages=messages,
        )
        nodes = self._chat_pipeline.run(documents=[doc], show_progress=False)
        if not nodes:
            return []
        # Group nodes by `insight_seq` so a long insight the splitter chopped
        # into multiple nodes still becomes one SourceDoc (one insight = one
        # markdown doc; chunks within it are per-section).
        by_seq: dict[int, list[Any]] = {}
        for n in nodes:
            seq = int(n.metadata.get("insight_seq", 0))
            by_seq.setdefault(seq, []).append(n)

        written: list[str] = []
        for seq, group in sorted(by_seq.items()):
            path = f"{investigation_id}/insight-{seq}.md"
            # Markdown body for the persisted SourceDoc. Joining each node's
            # content reconstructs the insight even when the splitter chopped
            # it across multiple TextNodes.
            body = "\n\n".join(n.get_content() for n in group).encode("utf-8")
            doc_id = self._store_file(collection_id, user, path, body)
            if doc_id is None:
                # Unchanged bytes → existing chunks survive, no work to do.
                continue
            self._delete_chunks(doc_id)
            chrm = self._spec.get_resource_manager(DocChunk)
            for chunk_seq, n in enumerate(group):
                chrm.create(
                    DocChunk(
                        collection_id=collection_id,
                        source_doc_id=doc_id,
                        seq=chunk_seq,
                        start=n.start_char_idx or 0,
                        end=n.end_char_idx or len(n.get_content()),
                        text=n.get_content(),
                        embedding=n.embedding or [],
                    )
                )
            drm = self._spec.get_resource_manager(SourceDoc)
            sd = drm.get(doc_id).data
            assert isinstance(sd, SourceDoc)
            drm.update(doc_id, msgspec.structs.replace(sd, status="ready"))
            written.append(doc_id)
        return written

    def store(self, *, collection_id: str, user: str, filename: str, data: bytes) -> list[str]:
        """Fast path: persist the SourceDoc(s) as ``status="indexing"`` and
        return the ids that need indexing (new or changed). No chunking/embedding
        — that's the slow `index` step.

        A zip/tar(.gz) is unpacked and each md/txt member stored at its
        archive-relative path; a lone file is stored if it's md/txt. Other types
        are skipped. Content type is sniffed via magic, not the extension."""
        mime = magic.from_buffer(data, mime=True)
        members = self._extract(mime, data) if mime in _ARCHIVE_MIMES else [(filename, data)]
        # Pipeline mode unlocks Reader-backed binary types (PDF / HTML / DOCX);
        # the legacy chunker path stays text-only.
        accepted = _TEXT_MIMES | (_BINARY_MIMES_VIA_READER if self._pipeline else set())
        touched: list[str] = []
        for path, member in members:
            member_mime = magic.from_buffer(member, mime=True)
            is_code = self._pipeline is not None and any(
                path.lower().endswith(ext) for ext in _CODE_EXTENSIONS
            )
            if member_mime not in accepted and not is_code:
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
            self._index(doc.collection_id, doc_id, doc.path, raw)
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

    def _index(self, collection_id: str, doc_id: str, path: str, data: bytes) -> None:
        if self._pipeline is not None:
            self._index_via_pipeline(collection_id, doc_id, path, data)
            return
        text = normalize_text(data.decode("utf-8", errors="replace"))
        assert self._chunker is not None  # one of pipeline/chunker is required
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

    def _index_via_pipeline(self, collection_id: str, doc_id: str, path: str, data: bytes) -> None:
        """P1 indexing path: text mimes go straight into a `Document`; binary
        mimes are routed through `reader_for(...)` (PDFReader / etc.) to get
        their text. Either way the result is `Document`(s) carrying
        filename/mime metadata for the splitter dispatch, which the pipeline
        runs through splitter + embedder. The resulting embedded nodes are
        mapped back to DocChunk storage.

        Char offsets fall back to (0, len(node.text)) when the splitter
        doesn't record them (e.g. MarkdownNodeParser after we prepend a
        heading breadcrumb)."""
        assert self._pipeline is not None
        mime = magic.from_buffer(data, mime=True)
        is_code = any(path.lower().endswith(ext) for ext in _CODE_EXTENSIONS)
        docs: list[Document]
        if mime in _TEXT_MIMES or is_code:
            text = normalize_text(data.decode("utf-8", errors="replace"))
            docs = [Document(text=text, metadata={"filename": path, "mime": mime})]
        else:
            reader = reader_for(filename=path, mime=mime)
            if reader is None:
                logger.warning("no reader for %s (%s) — skipping", path, mime)
                return
            docs = reader(data)
            for d in docs:
                d.metadata.setdefault("filename", path)
                d.metadata.setdefault("mime", mime)
        nodes = self._pipeline.run(documents=docs, show_progress=False)
        # P3.0 vector routing: collections with embedder_id != 0 use the code
        # embedder and write into `embedding_alt`, leaving `embedding` empty
        # so the retriever's two-path fan-out can dispatch cleanly.
        use_alt = self._should_use_alt_embedder(collection_id)
        if use_alt:
            assert self._code_embedder is not None, (
                "Collection has embedder_id != 0 but no code_embedder was wired"
            )
            alt_vecs = self._code_embedder.embed_documents([n.get_content() for n in nodes])
        chrm = self._spec.get_resource_manager(DocChunk)
        for seq, n in enumerate(nodes):
            start = n.start_char_idx if n.start_char_idx is not None else 0
            end = n.end_char_idx if n.end_char_idx is not None else len(n.get_content())
            chrm.create(
                DocChunk(
                    collection_id=collection_id,
                    source_doc_id=doc_id,
                    seq=seq,
                    start=start,
                    end=end,
                    text=n.get_content(),
                    embedding=None if use_alt else (n.embedding or None),
                    embedding_alt=alt_vecs[seq] if use_alt else None,
                )
            )

    def _should_use_alt_embedder(self, collection_id: str) -> bool:
        """Return True iff the Collection's `embedder_id` selects the alt
        (code-specialised) embedder. Cached lookups are cheap — single GET
        on the manager — and this runs once per ingested doc, not per chunk."""
        coll = self._spec.get_resource_manager(Collection).get(collection_id).data
        assert isinstance(coll, Collection)
        return coll.embedder_id != 0
