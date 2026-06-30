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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

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
from .doc_id import canonical_path, encode_doc_id
from .embedder import Embedder
from .parser_config import effective_config
from .parsers import IParser, MaterialisedParserInput, ParserRegistry
from .parsers.chat_export_parser import ChatExportParser
from .parsers.json_file import JsonParser
from .parsers.llamaindex_readers import DocxParser, HtmlParser
from .parsers.pdf import PdfParser
from .parsers.slides import PptxParser
from .parsers.tabular import CsvParser, ExcelParser
from .tokens import count_tokens
from .upload_checks import UploadCheckRegistry, bundled_upload_checks

if TYPE_CHECKING:
    from specstar.types import IResourceManager

    from .upload_checks import UploadCheckHint

logger = logging.getLogger(__name__)


def chunk_id(doc_id: str, seq: int) -> str:
    """Deterministic ``DocChunk`` id for the fan-out path (#227): keyed on
    ``(doc_id, seq)`` so a redelivered process job overwrites its slice in place
    instead of minting duplicate chunk rows. ``seq`` is globally unique per doc
    (each fan-out batch numbers from ``batch_index * stride``)."""
    return f"{doc_id}.c{seq}"


class _IndexOutput(NamedTuple):
    """What one index pass hands back to ``index()``.

    - ``text`` — the 'text converter' output: the whole-document text BEFORE the
      chunker (joined parser Documents, or the inline-decoded text). Persisted on
      ``SourceDoc.text`` so the wiki maintainer reads clean source text instead
      of decoding the raw bytes (issue #86). ``None`` when nothing was extracted
      (a binary type no parser claimed).
    - ``preview`` — a browser-displayable derivative a parser handed back.
    """

    text: str | None
    preview: Binary | None


# What ``Ingestor.convert`` produces (#324). ``kind`` tells the caller how to file the
# converted artifact so the collection stays self-consistent (extension ⟺ content):
#   - ``"markdown"`` — a parser turned a binary/structured upload into markdown text;
#     the caller stores it under a ``.md`` name (e.g. ``deck.pptx`` → ``deck.pptx.md``).
#   - ``"passthrough"`` — already plain text/code no parser claims; the caller files the
#     ORIGINAL upload unchanged (its extension already matches its content).
#   - ``"none"`` — a binary no parser could read; nothing to store (caller skips it).
ConvertKind = Literal["markdown", "passthrough", "none"]


class ConvertResult(NamedTuple):
    text: str | None
    kind: ConvertKind


# md sniffs as text/plain on libmagic; both accepted.
_TEXT_MIMES = {"text/plain", "text/markdown"}
_ARCHIVE_MIMES = {"application/zip", "application/x-tar", "application/gzip"}
# Source-code files we accept by extension when a pipeline is wired. libmagic
# usually classifies these as `text/x-script.python`, `text/x-c`, etc. — not
# in `_TEXT_MIMES`. The pipeline's DispatchSplitter routes them to LI's
# CodeSplitter (tree-sitter, function-boundary aware).
_CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}

# Issue #254: the splitter-node metadata keys that describe WHERE a chunk lives
# (vs. ``filename`` / ``mime`` / ``content_format``, which are routing hints, and
# LlamaIndex's own ``Header_N`` markdown keys, which are already folded into the
# embedded breadcrumb). Only these are persisted on ``DocChunk.provenance``.
# Adding a new parser locator means adding its key here.
_PROVENANCE_KEYS = frozenset({"page", "section", "slide", "sheet", "jsonl_line", "row"})


def _collect_provenance(metadata: dict[str, Any]) -> dict[str, Any]:
    """Pick the location-bearing keys out of a node's metadata (issue #254).
    Order-stable on ``_PROVENANCE_KEYS`` so equal provenance compares equal."""
    return {k: metadata[k] for k in _PROVENANCE_KEYS if k in metadata}


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
        parser_registry: ParserRegistry | None = None,
        upload_checks: UploadCheckRegistry | None = None,
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
        # Issue #39: pluggable parsers handle binary uploads. The
        # factory builds this from `kb.parsers: [...]` (custom) + the
        # bundled PDF/HTML/DOCX. Tests + offline callers that don't
        # supply one get a bundled-only fallback so legacy
        # `Ingestor(spec, pipeline=...)` constructions still parse
        # PDF/HTML/DOCX.
        if parser_registry is not None:
            self._parser_registry = parser_registry
        else:
            self._parser_registry = (
                ParserRegistry()
                .register(PdfParser())
                .register(HtmlParser())
                .register(DocxParser())
                # No LLM in the fallback: a .chat.json upload errors with
                # an actionable message instead of silently doing nothing.
                .register(ChatExportParser())
                .register(JsonParser())
                .register(CsvParser())
                .register(ExcelParser())
                .register(PptxParser())
            )
        # #325: pluggable synchronous upload gate. The default bundle
        # refuses encrypted/unreadable Office + PDF uploads; an operator
        # can inject a custom registry to add their own checks.
        self._upload_checks = upload_checks or bundled_upload_checks()

    def upload_check_hints(self) -> list[UploadCheckHint]:
        """The browser-runnable upload-check descriptors (#325) — served
        by ``GET /kb/upload-checks`` so the FE pre-blocks the common case
        (an encrypted Office file) before upload."""
        return self._upload_checks.hints()

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

        A zip/tar(.gz) is unpacked and each member stored at its
        archive-relative path; a lone file is stored as-is. Issue #39
        Q8a — in pipeline mode every upload is stored regardless of
        whether a parser currently handles it (an unknown type stays
        on disk so a future custom parser registered later can
        reindex it). The legacy non-pipeline path is still text-only.
        Content type is sniffed via magic, not the extension.

        **Parser claim beats archive expansion**: pptx/xlsx/docx are
        zip containers — when libmagic only sees ``application/zip``,
        blind expansion would explode an office file into its internal
        XML members. An upload ANY registered parser claims is stored
        whole; only unclaimed archives expand."""
        mime = magic.from_buffer(data, mime=True)
        # #325: gate BEFORE creating any SourceDoc or expanding an archive —
        # a refused upload (encrypted/unreadable) leaves nothing behind and
        # raises UploadRejected, which the API maps to a 422 with an
        # actionable message instead of a cryptic background-index error.
        self._upload_checks.run(filename=filename, mime=mime, data=data)
        unpack = mime in _ARCHIVE_MIMES
        if unpack and self._pipeline is not None:
            with MaterialisedParserInput(data, filename=filename) as source:
                unpack = not self._parser_registry.all_matching(
                    filename=filename, mime=mime, source=source
                )
        members = self._extract(mime, data) if unpack else [(filename, data)]
        touched: list[str] = []
        for path, member in members:
            member_mime = magic.from_buffer(member, mime=True)
            # Legacy chunker path (pipeline is None): text-only.
            # Binary uploads are skipped (no parser dispatch outside
            # the pipeline).
            if self._pipeline is None and member_mime not in _TEXT_MIMES:
                continue
            # Pipeline mode: store everything. index() decides whether
            # any parser claims it and writes chunks; an unclaimed file
            # ends up status=ready, chunks=0.
            doc_id = self._store_file(collection_id, user, path, member)
            if doc_id is not None:
                touched.append(doc_id)
        return touched

    def store_file(self, *, collection_id: str, user: str, path: str, data: bytes) -> str | None:
        """Store ONE file at ``path`` VERBATIM — no archive expansion, no parser /
        text-only filtering. The import path's per-member primitive (#101): an
        exported member (even a ``.zip`` doc) round-trips byte-for-byte, whereas
        ``store`` would re-expand an archive member. ``path`` is canonicalised
        (zip-slip-safe — escaping paths raise). Returns the doc id, or ``None``
        when the bytes already match (no-op re-upload)."""
        return self._store_file(collection_id, user, path, data)

    def convert(
        self,
        *,
        path: str,
        data: bytes,
        on_progress: Callable[[str], None] | None = None,
    ) -> ConvertResult:
        """Parse-only (#324): produce the joined text the SAME parsers would extract at
        index time, but WITHOUT chunking/embedding and WITHOUT touching any SourceDoc —
        ``convert`` has no ``collection_id``/``doc_id``, so it structurally cannot persist.

        Topic-hub's ``→collections`` workflow calls this to turn an upload into text BEFORE
        filing it into a collection, so only the converted artifact is stored (never the raw
        binary). The branching mirrors ``_index_via_pipeline`` so a file converts here exactly
        as the index step would extract its ``text`` — see ``ConvertKind`` for what each
        result tells the caller to file. ``on_progress`` forwards a long parser's (VLM) status
        to the caller (no SourceDoc to write it onto here)."""
        mime = magic.from_buffer(data, mime=True)
        is_code = any(path.lower().endswith(ext) for ext in _CODE_EXTENSIONS)
        texts: list[str] = []
        with MaterialisedParserInput(data, filename=path) as source:
            parsers = self._parser_registry.all_matching(filename=path, mime=mime, source=source)
            for parser in parsers:
                for d in parser.parse(source, filename=path, mime=mime, on_progress=on_progress):
                    texts.append(d.text)
        if parsers:
            return ConvertResult("\n\n".join(texts).strip() or None, "markdown")
        if mime in _TEXT_MIMES or is_code:
            return ConvertResult(
                normalize_text(data.decode("utf-8", errors="replace")), "passthrough"
            )
        return ConvertResult(None, "none")

    def index(
        self,
        doc_id: str,
        *,
        source_doc_rm: IResourceManager[SourceDoc] | None = None,
        reraise: bool = False,
    ) -> None:
        """Slow path: (re)build a stored doc's chunks — chunk + embed — then
        flip its status to ``ready`` (``error`` if embedding fails). Safe to run
        off the request thread.

        ``reraise`` (#249): when True, a failure is NOT mapped to ``status=error``
        here — it propagates so the caller (the IndexCoordinator job handler) can
        tell a transient blip (re-deliver the job, show "retrying") from a
        permanent error (mark ``error``). The synchronous path leaves it False —
        it has no job to requeue, so a failure is terminal and surfaced as the
        doc's ``error`` status as before.

        Issue #39: a file with no matching parser still flips to
        ``status="ready"`` with ``chunks=0`` — the upload survives on
        disk (so a future custom parser can reindex it) but isn't
        searchable. This is the same UX the pre-#39 renderable-image
        path had; the explicit MIME allowlist is gone now.

        Issue #83: ``source_doc_rm`` lets the IndexCoordinator — which runs in a
        job pod with NO request user — hand in a SourceDoc manager already scoped
        to the doc's last updater via ``rm.using(user=...)``. The final status/
        text write below then keeps ``updated_by`` as the real uploader instead
        of stamping the bare worker default. The sync/request path omits it (its
        acting user is already the right one) and we fetch our own. The final
        update is always the last SourceDoc write of the run, so binding it is
        enough — interim ``status_detail`` progress writes are overwritten."""
        drm = (
            source_doc_rm
            if source_doc_rm is not None
            else self._spec.get_resource_manager(SourceDoc)
        )
        doc = drm.get(doc_id).data
        assert isinstance(doc, SourceDoc)
        raw = drm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        try:
            self._delete_chunks(doc_id)
            text, preview = self._index(doc.collection_id, doc_id, doc.path, raw)
        except Exception as exc:  # noqa: BLE001 — surface failure as doc status, don't crash the worker
            if reraise:
                raise  # #249: let the job handler decide status + whether to requeue
            # Don't lose the cause: the status flip alone is opaque (a missing
            # embedding model, a dim mismatch, …). Log the traceback so it's
            # visible in the server logs instead of a silent "error" badge, and
            # surface a one-line summary on the doc row.
            logger.exception("indexing failed for %s", doc_id)
            drm.update(
                doc_id,
                msgspec.structs.replace(
                    doc,
                    status="error",
                    status_detail=f"{type(exc).__name__}: {exc!s}"[:240],
                    preview=None,
                    text=None,
                ),
            )
            return
        # `preview` and `text` are derived + current-only like the chunks: each
        # (re)index round's hand-back wins; None clears a stale one.
        drm.update(
            doc_id,
            msgspec.structs.replace(
                doc,
                status="ready",
                status_detail="",
                preview=preview,
                text=text,
                token_count=count_tokens(text or ""),  # #88: chunk-based token estimate
            ),
        )

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
        # The id keys on collection + path only (NOT the user), so a collection
        # is a shared space: the same path is ONE doc whoever uploads it, and a
        # second writer updates it in place (last write wins; `created_by` stays
        # the original). `user` is kept as the acting-user context, not the key.
        # Canonicalise first — every ingest entry point funnels through here, so
        # surface variants of one path (leading slash, "//", "./..") can never
        # mint two ids for the same logical doc.
        path = canonical_path(path)
        doc_id = encode_doc_id(collection_id, path)
        drm = self._spec.get_resource_manager(SourceDoc)
        try:
            existing = drm.get(doc_id).data
        except ResourceIDNotFoundError:
            existing = None
        # Identical bytes already at this id → no-op (don't churn a revision).
        if existing is not None and existing.content.file_id == xxhash.xxh3_128_hexdigest(data):
            return None
        doc = SourceDoc(
            collection_id=collection_id,
            path=path,
            content=Binary(data=data),
            status="indexing",
            # #328/#356: carry the per-doc extraction escape hatches across a
            # re-upload. They're per-doc EXTRACTION settings, not tied to a content
            # version — so a hand-tuned doc keeps its tuning when its bytes change.
            # (A full re-index never reconstructs the SourceDoc, so it was already
            # safe there; this covers the re-upload-new-bytes path.)
            parser_config_overrides=(existing.parser_config_overrides if existing else {}),
            parser_guidance_override=(existing.parser_guidance_override if existing else ""),
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

    def _set_status_detail(self, doc_id: str, message: str) -> None:
        """Long-parser progress surface (issue #39 Q11): write `message`
        onto the SourceDoc's `status_detail` so the FE's indexing poll
        shows it live. Swallows update errors — a transient DB blip
        must not crash the parser mid-doc; the next call retries."""
        drm = self._spec.get_resource_manager(SourceDoc)
        try:
            sd = drm.get(doc_id).data
            assert isinstance(sd, SourceDoc)
            drm.update(doc_id, msgspec.structs.replace(sd, status_detail=message))
        except Exception:  # noqa: BLE001
            logger.warning("status_detail update failed for %s", doc_id, exc_info=True)

    def _parse_config_for(
        self, parser: IParser, *, collection_id: str, doc_id: str
    ) -> dict[str, Any] | None:
        """The effective config (#328) to feed ``parser.parse(config=...)``, or
        ``None`` when this parser declares no tunable knobs (the common case —
        then ``parse`` is called WITHOUT ``config``, exactly as before the seam).

        Resolves parser defaults < the collection's ``parser_configs`` < the
        doc's ``parser_config_overrides`` (the per-doc escape hatch), all keyed
        by parser id. The Collection / SourceDoc fetches are skipped entirely
        for knob-less parsers so the hot path is untouched."""
        if not parser.config_fields():
            return None
        coll = self._spec.get_resource_manager(Collection).get(collection_id).data
        assert isinstance(coll, Collection)
        doc = self._spec.get_resource_manager(SourceDoc).get(doc_id).data
        assert isinstance(doc, SourceDoc)
        return effective_config(
            parser,
            collection_configs=coll.parser_configs,
            doc_overrides=doc.parser_config_overrides,
        )

    def _parse_guidance_for(self, collection_id: str, doc_id: str) -> str:
        """The effective ``parser_guidance`` for this doc — appended to every
        prompt-driven parser's base prompt. The doc's ``parser_guidance_override``
        (#356) REPLACES the collection's ``parser_guidance`` when non-empty;
        otherwise the doc inherits the collection's. Empty ``""`` (the common
        case) ⇒ the ingestor never threads ``guidance`` (zero-churn for
        parsers/collections that don't use it)."""
        doc = self._spec.get_resource_manager(SourceDoc).get(doc_id).data
        assert isinstance(doc, SourceDoc)
        if doc.parser_guidance_override:
            return doc.parser_guidance_override
        coll = self._spec.get_resource_manager(Collection).get(collection_id).data
        assert isinstance(coll, Collection)
        return coll.parser_guidance

    @staticmethod
    def _guidance_kwargs(parser: IParser, guidance: str) -> dict[str, str]:
        """The opt-in ``guidance`` bridge — passed to ``parse`` only when this
        parser consumes guidance AND the collection set some. Mirrors the
        ``config`` bridge: the kwarg is off the IParser ABC, so knob-less /
        non-LLM parsers never see it."""
        return {"guidance": guidance} if guidance and parser.uses_guidance() else {}

    def _index(self, collection_id: str, doc_id: str, path: str, data: bytes) -> _IndexOutput:
        """(Re)build a doc's chunks. Returns the converter ``text`` (persisted on
        SourceDoc.text) + the doc's browser-preview Binary (pipeline mode only)."""
        if self._pipeline is not None:
            return self._index_via_pipeline(collection_id, doc_id, path, data)
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
        return _IndexOutput(text or None, None)

    def _index_via_pipeline(
        self, collection_id: str, doc_id: str, path: str, data: bytes
    ) -> _IndexOutput:
        """Issue #39: pipeline-mode indexing. Routing has three cases:

        1. **Parser(s) match**: every parser whose ``matches(...)``
           returns True runs — parsers take precedence over the inline
           text path so a structured-text type (e.g. JSON, which
           libmagic may sniff as text/plain) is consistently owned by
           its parser. Each parser's output is a separate "packet" of
           LI Documents that pipes through the pipeline (splitter +
           embedder) and lands as DocChunks tagged with
           ``parser_id = type(parser).__name__``. Operator can later
           reindex one parser's chunks without touching the others.
        2. **No parser, text / code**: handled inline — the
           DispatchSplitter inside the pipeline picks the right splitter
           by mime / extension. Chunks have ``parser_id=""``.
        3. **No parser, binary**: log + return. The caller's
           ``index()`` wraps this and flips status=ready with
           chunks=0; the SourceDoc stays on disk so a custom parser
           added later can reindex.

        Long-running parsers (VLM image / VLM slide) call ``on_progress``
        with a short status; the Ingestor writes it onto
        ``SourceDoc.status_detail`` so the FE row shows progress.

        Returns the doc's browser-preview ``Binary`` when a parser
        handed one back via ``on_preview`` (e.g. PptxParser's converted
        PDF; last hand-back wins if several parsers offer one)."""
        assert self._pipeline is not None
        mime = magic.from_buffer(data, mime=True)
        is_code = any(path.lower().endswith(ext) for ext in _CODE_EXTENSIONS)

        def on_progress(message: str) -> None:
            self._set_status_detail(doc_id, message)

        preview: Binary | None = None

        def on_preview(preview_data: bytes, preview_mime: str) -> None:
            nonlocal preview
            preview = Binary(data=preview_data, content_type=preview_mime)

        # Collect (parser_id, list[Document]) packets. Parsers first;
        # the inline text/code packet (parser_id="") is the fallback for
        # plain text no parser claims.
        packets: list[tuple[str, list[Document]]] = []
        guidance = self._parse_guidance_for(collection_id, doc_id)
        with MaterialisedParserInput(data, filename=path) as source:
            parsers = self._parser_registry.all_matching(filename=path, mime=mime, source=source)
            for parser in parsers:
                parser_id = type(parser).__name__
                cfg = self._parse_config_for(parser, collection_id=collection_id, doc_id=doc_id)
                docs = list(
                    parser.parse(
                        source,
                        filename=path,
                        mime=mime,
                        on_progress=on_progress,
                        on_preview=on_preview,
                        # `config` is opt-in (#328): it's NOT on the IParser ABC
                        # (so knob-less parsers stay Liskov-clean), only on a
                        # config-aware parser's own override. cfg is non-None only
                        # when this parser declared config_fields, i.e. it accepts
                        # the kwarg — the dict-unpack is the intentional bridge.
                        **({"config": cfg} if cfg is not None else {}),  # ty: ignore[invalid-argument-type]
                        # `guidance` (#328) is the parallel opt-in bridge — the
                        # collection's parser_guidance appended to a prompt-driven
                        # parser's base prompt. Same dict-unpack discipline.
                        **self._guidance_kwargs(parser, guidance),
                    )
                )
                for d in docs:
                    d.metadata.setdefault("filename", path)
                    d.metadata.setdefault("mime", mime)
                packets.append((parser_id, docs))
        if not packets:
            if mime in _TEXT_MIMES or is_code:
                text = normalize_text(data.decode("utf-8", errors="replace"))
                packets.append(
                    ("", [Document(text=text, metadata={"filename": path, "mime": mime})])
                )
            else:
                logger.info("no parser for %s (%s) — chunks=0, status=ready", path, mime)
                return _IndexOutput(None, None)

        # The 'text converter' output: the whole-document text BEFORE chunking
        # (issue #86). Joining the parser Documents is overlap-free and
        # breadcrumb-free — unlike re-joining DocChunks — so the wiki maintainer
        # reads clean source text. The chunker downstream still consumes the
        # SAME Documents into DocChunks.
        full_text = "\n\n".join(d.text for _, docs in packets for d in docs).strip()

        # Run each packet through the pipeline. seq is global across
        # packets so adjacency-merge in retrieval stays meaningful
        # (parser A's last chunk is seq N; parser B's first is N+1).
        use_alt = self._should_use_alt_embedder(collection_id)
        seq_offset = 0
        for parser_id, docs in packets:
            seq_offset += self._emit_packet(
                collection_id, doc_id, parser_id, docs, seq_base=seq_offset, use_alt=use_alt
            )
        return _IndexOutput(full_text or None, preview)

    def _emit_packet(
        self,
        collection_id: str,
        doc_id: str,
        parser_id: str,
        docs: list[Document],
        *,
        seq_base: int,
        use_alt: bool,
        deterministic: bool = False,
    ) -> int:
        """Split + embed one parser packet's Documents into ``DocChunk`` rows,
        numbering ``seq`` from ``seq_base``. Returns the node count so the caller
        can advance the offset. ``deterministic`` (#227) mints chunk ids from
        ``(doc_id, seq)`` so a redelivered fan-out process job OVERWRITES its
        slice instead of duplicating it; the single-job path keeps auto ids."""
        chrm = self._spec.get_resource_manager(DocChunk)
        chunks = self._build_chunks(
            collection_id, doc_id, parser_id, docs, seq_base=seq_base, use_alt=use_alt
        )
        for chunk in chunks:
            if deterministic:
                chrm.create_or_update(chunk_id(doc_id, chunk.seq), chunk)
            else:
                chrm.create(chunk)
        return len(chunks)

    def _build_chunks(
        self,
        collection_id: str,
        doc_id: str,
        parser_id: str,
        docs: list[Document],
        *,
        seq_base: int,
        use_alt: bool,
    ) -> list[DocChunk]:
        """Split + embed a parser packet's Documents into ``DocChunk`` objects,
        numbering ``seq`` from ``seq_base`` — but NOT persisted. The shared core
        of ``_emit_packet`` (which persists them) and the #328 dry-run re-parse
        (``dry_run_chunks``, which hands them to the retriever Overlay), so the
        preview ranks on chunks built by the exact same split + embed as the real
        index (no drift)."""
        assert self._pipeline is not None  # only the pipeline path builds chunks
        nodes = self._pipeline.run(documents=docs, show_progress=False)
        alt_vecs: list[list[float]] | None = None
        if use_alt:
            assert self._code_embedder is not None, (
                "Collection has embedder_id != 0 but no code_embedder was wired"
            )
            alt_vecs = self._code_embedder.embed_documents([n.get_content() for n in nodes])
        out: list[DocChunk] = []
        for i, n in enumerate(nodes):
            start = n.start_char_idx if n.start_char_idx is not None else 0
            end = n.end_char_idx if n.end_char_idx is not None else len(n.get_content())
            out.append(
                DocChunk(
                    collection_id=collection_id,
                    source_doc_id=doc_id,
                    seq=seq_base + i,
                    start=start,
                    end=end,
                    text=n.get_content(),
                    embedding=None if use_alt else (n.embedding or None),
                    embedding_alt=alt_vecs[i] if (use_alt and alt_vecs is not None) else None,
                    parser_id=parser_id,
                    provenance=_collect_provenance(n.metadata),
                )
            )
        return out

    def dry_run_chunks(self, doc_id: str, *, guidance: str) -> tuple[list[DocChunk], str]:
        """#328 findability dry-run: re-parse ONE doc with a CANDIDATE
        ``parser_guidance`` and return the (NOT persisted) virtual ``DocChunk``s
        + the re-joined canonical text their offsets index into. The retriever
        ``Overlay`` feeds these in (this doc's stored chunks shadowed) so the
        modal previews "if I re-parsed this doc with THIS guidance, would its
        chunks rank better?" — without touching the index.

        Reuses the real parse + split + embed (``_build_chunks``) so the preview
        can't drift from production. ``guidance`` is the modal's candidate; it
        overrides the collection's stored ``parser_guidance`` for this preview
        only. The branching mirrors ``_index_via_pipeline`` (parser packets, then
        the inline text/code fallback) minus persistence / preview / status."""
        assert self._pipeline is not None  # dry-run is a pipeline-mode feature
        drm = self._spec.get_resource_manager(SourceDoc)
        doc = drm.get(doc_id).data
        assert isinstance(doc, SourceDoc)
        raw = drm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        mime = magic.from_buffer(raw, mime=True)
        is_code = any(doc.path.lower().endswith(ext) for ext in _CODE_EXTENSIONS)
        use_alt = self._should_use_alt_embedder(doc.collection_id)
        packets: list[tuple[str, list[Document]]] = []
        with MaterialisedParserInput(raw, filename=doc.path) as source:
            parsers = self._parser_registry.all_matching(
                filename=doc.path, mime=mime, source=source
            )
            for parser in parsers:
                cfg = self._parse_config_for(parser, collection_id=doc.collection_id, doc_id=doc_id)
                docs = list(
                    parser.parse(
                        source,
                        filename=doc.path,
                        mime=mime,
                        **({"config": cfg} if cfg is not None else {}),  # ty: ignore[invalid-argument-type]
                        **self._guidance_kwargs(parser, guidance),
                    )
                )
                for d in docs:
                    d.metadata.setdefault("filename", doc.path)
                    d.metadata.setdefault("mime", mime)
                packets.append((type(parser).__name__, docs))
        if not packets and (mime in _TEXT_MIMES or is_code):
            text = normalize_text(raw.decode("utf-8", errors="replace"))
            packets.append(
                ("", [Document(text=text, metadata={"filename": doc.path, "mime": mime})])
            )
        virtual_text = "\n\n".join(d.text for _, docs in packets for d in docs).strip()
        chunks: list[DocChunk] = []
        seq_offset = 0
        for parser_id, docs in packets:
            built = self._build_chunks(
                doc.collection_id, doc_id, parser_id, docs, seq_base=seq_offset, use_alt=use_alt
            )
            chunks.extend(built)
            seq_offset += len(built)
        return chunks, virtual_text

    def _should_use_alt_embedder(self, collection_id: str) -> bool:
        """Return True iff the Collection's `embedder_id` selects the alt
        (code-specialised) embedder. Cached lookups are cheap — single GET
        on the manager — and this runs once per ingested doc, not per chunk."""
        coll = self._spec.get_resource_manager(Collection).get(collection_id).data
        assert isinstance(coll, Collection)
        return coll.embedder_id != 0

    # ── fan-out (#227): split a large index into per-unit-range process jobs ──
    def fanout_units(self, doc_id: str) -> tuple[int, str]:
        """Plan the fan-out for ``doc_id``: ``(unit_count, parser_id)``. Fan-out
        applies only when EXACTLY one parser claims the doc (pipeline mode) and
        it reports more than one unit — then the index splits into per-unit-range
        process jobs. Otherwise returns ``(1, "")`` ⇒ index as a single job (the
        unchanged whole-doc path: multi-parser, no-parser, legacy chunker, or a
        genuinely small file)."""
        if self._pipeline is None:
            return (1, "")
        drm = self._spec.get_resource_manager(SourceDoc)
        doc = drm.get(doc_id).data
        assert isinstance(doc, SourceDoc)
        raw = drm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        mime = magic.from_buffer(raw, mime=True)
        with MaterialisedParserInput(raw, filename=doc.path) as source:
            parsers = self._parser_registry.all_matching(
                filename=doc.path, mime=mime, source=source
            )
            if len(parsers) != 1:
                return (1, "")
            units = parsers[0].count_units(source, filename=doc.path, mime=mime)
        return (max(1, units), type(parsers[0]).__name__)

    def prepare_fanout(self, doc_id: str) -> None:
        """Clear the doc's chunks ONCE before the fan-out's process jobs each
        (re)create their own deterministic slice."""
        self._delete_chunks(doc_id)

    def index_units(
        self,
        doc_id: str,
        unit_range: tuple[int, int],
        *,
        seq_base: int,
        source_doc_rm: IResourceManager[SourceDoc] | None = None,
    ) -> str:
        """Parse + chunk + embed ONLY units ``[start, end)`` of the doc's single
        parser, writing deterministic-id ``DocChunk`` rows (idempotent under
        redelivery). Returns the clean text of those units, which the finalize
        step rejoins in order into ``SourceDoc.text``."""
        assert self._pipeline is not None
        drm = (
            source_doc_rm
            if source_doc_rm is not None
            else self._spec.get_resource_manager(SourceDoc)
        )
        doc = drm.get(doc_id).data
        assert isinstance(doc, SourceDoc)
        raw = drm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)
        mime = magic.from_buffer(raw, mime=True)
        use_alt = self._should_use_alt_embedder(doc.collection_id)
        with MaterialisedParserInput(raw, filename=doc.path) as source:
            parsers = self._parser_registry.all_matching(
                filename=doc.path, mime=mime, source=source
            )
            assert len(parsers) == 1, "fanout_units guarantees a single parser"
            parser = parsers[0]
            cfg = self._parse_config_for(parser, collection_id=doc.collection_id, doc_id=doc_id)
            guidance = self._parse_guidance_for(doc.collection_id, doc_id)
            docs = list(
                parser.parse(
                    source,
                    filename=doc.path,
                    mime=mime,
                    # #248: NO per-page status_detail on the fan-out path — N
                    # parallel batches each overwrite the one shared field, so the
                    # value is meaningless (last-writer-wins, non-monotonic). The
                    # FE reads real progress from IndexRun.units_done/total instead.
                    on_progress=None,
                    unit_range=unit_range,
                    # opt-in config + guidance bridges — see the other parse site.
                    **({"config": cfg} if cfg is not None else {}),  # ty: ignore[invalid-argument-type]
                    **self._guidance_kwargs(parser, guidance),
                )
            )
            for d in docs:
                d.metadata.setdefault("filename", doc.path)
                d.metadata.setdefault("mime", mime)
        self._emit_packet(
            doc.collection_id,
            doc_id,
            type(parser).__name__,
            docs,
            seq_base=seq_base,
            use_alt=use_alt,
            deterministic=True,
        )
        return "\n\n".join(d.text for d in docs).strip()
