"""LlamaIndex `IngestionPipeline` build helpers — the P1 replacement for
Ingestor's hand-rolled chunk+embed loop. See docs/plan-llamaindex-ingest.md
§2 for scope and rationale.

We treat LI as ingest-only plumbing: the pipeline runs splitter → embedder
adapter, and Ingestor maps the resulting LI `BaseNode`s back to our
`DocChunk` storage. The Embedder Protocol and DocChunk schema are unchanged
— LI is internal to ingest.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import BaseNode, Document, TextNode, TransformComponent

from .embedder import Embedder


class DispatchSplitter(TransformComponent):
    """Pick a splitter per node based on the source file's mime / extension.

    LlamaIndex pipelines apply one transformation to the whole batch, but our
    sources are heterogeneous (markdown / html / plain text / PDF text). We
    set `Document.metadata["mime"]` and `["filename"]` upstream; this
    component inspects them and routes each doc to its best splitter, then
    flattens the results back into one node list.

    On a markdown doc, we *manually prepend the heading breadcrumb* to each
    node's text so the embedding captures structure — LI's MarkdownNodeParser
    populates `metadata["header_path"]` but doesn't fold it into `text` by
    default, and the embedder embeds `node.text`.
    """

    # Default sub-splitters; overridable per instance for tests/tuning.
    sentence_splitter: SentenceSplitter
    markdown_parser: MarkdownNodeParser

    def __init__(
        self,
        *,
        sentence_max_tokens: int = 256,
        sentence_overlap: int = 32,
    ) -> None:
        super().__init__(
            sentence_splitter=SentenceSplitter(
                chunk_size=sentence_max_tokens,
                chunk_overlap=sentence_overlap,
            ),
            markdown_parser=MarkdownNodeParser(),
        )

    def __call__(self, nodes: Sequence[BaseNode], **_kw: Any) -> list[BaseNode]:  # type: ignore[override]
        out: list[BaseNode] = []
        for node in nodes:
            mime = str(node.metadata.get("mime", "")).lower()
            filename = str(node.metadata.get("filename", "")).lower()
            if mime == "text/markdown" or filename.endswith(".md"):
                sub = self.markdown_parser.get_nodes_from_documents([node])
                # Prepend heading hierarchy to each chunk's text so the
                # embedding sees the structural context, not just body lines.
                for n in sub:
                    breadcrumb = _heading_breadcrumb(n)
                    if breadcrumb and isinstance(n, TextNode):
                        n.text = f"{breadcrumb}\n\n{n.text}"
                out.extend(sub)
            else:
                out.extend(self.sentence_splitter.get_nodes_from_documents([node]))
        return out


def _heading_breadcrumb(node: BaseNode) -> str:
    """Join the H1…Hn metadata MarkdownNodeParser puts on a node into a
    single 'H1 > H2 > H3' breadcrumb. Returns '' if no headers were captured
    (e.g. content before the first heading)."""
    parts: list[str] = []
    md = node.metadata
    # MarkdownNodeParser uses keys "Header_1", "Header_2", ... in order
    # (verified against llama-index-core 0.10.x; tolerant of variants).
    for i in range(1, 7):
        v = md.get(f"Header_{i}") or md.get(f"Header {i}") or md.get(f"header_{i}")
        if v:
            parts.append(str(v).strip())
    return " > ".join(parts)


class EmbedderAdapter(TransformComponent):
    """Wraps our `Embedder` Protocol as an LI `TransformComponent` so it
    plugs into `IngestionPipeline.transformations`. Calls
    `embed_documents([n.text for n in nodes])` in one batch and writes each
    vector back to `node.embedding`. No prefix logic here — the wrapped
    `LitellmEmbedder` already applies asymmetric document prefixes."""

    # Typed `Any` because LI's `TransformComponent` is a pydantic model and
    # would isinstance-check the Embedder Protocol (which is not
    # runtime-checkable). The constructor takes a real `Embedder`.
    embedder: Any

    def __init__(self, embedder: Embedder) -> None:
        super().__init__(embedder=embedder)

    def __call__(self, nodes: Sequence[BaseNode], **_kw: Any) -> list[BaseNode]:  # type: ignore[override]
        vecs = self.embedder.embed_documents([n.get_content() for n in nodes])
        for n, v in zip(nodes, vecs, strict=True):
            n.embedding = v
        return list(nodes)


ReaderFn = Callable[[bytes], list[Document]]


def reader_for(*, filename: str, mime: str) -> ReaderFn | None:
    """Pick a LlamaIndex Reader for a binary upload (PDF / HTML / DOCX). The
    Readers want a path on disk, so we wrap them as `bytes → list[Document]`
    that materialise the bytes into a tempfile, call the Reader, and clean
    up. Returns `None` when no Reader handles this type — the caller skips
    the doc (logged) rather than crashing the pipeline."""
    f = filename.lower()
    if mime == "application/pdf" or f.endswith(".pdf"):
        return _wrap_file_reader(_lazy_pdf_reader, suffix=".pdf")
    if mime == "text/html" or f.endswith((".html", ".htm")):
        return _wrap_file_reader(_lazy_html_reader, suffix=".html")
    if (
        mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or f.endswith(".docx")
    ):
        return _wrap_file_reader(_lazy_docx_reader, suffix=".docx")
    return None


def _wrap_file_reader(get_reader: Callable[[], Any], *, suffix: str) -> ReaderFn:
    """Materialise `bytes` to a tempfile so the Reader (which wants a path)
    can run; clean up regardless of success."""

    def _read(data: bytes) -> list[Document]:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            p = Path(f.name)
        try:
            return get_reader().load_data(file=p)
        finally:
            p.unlink(missing_ok=True)

    return _read


# Lazy reader constructors — keep the module import light when only some
# format families are exercised.
def _lazy_pdf_reader() -> Any:
    from llama_index.readers.file import PDFReader

    return PDFReader()


def _lazy_html_reader() -> Any:
    from llama_index.readers.file import HTMLTagReader

    # HTMLTagReader's default is "section"; for whole-page text we'd use
    # the BS4-backed one. We don't need tag scoping yet — extract <body>.
    return HTMLTagReader(tag="body")


def _lazy_docx_reader() -> Any:
    from llama_index.readers.file import DocxReader

    return DocxReader()


def build_doc_pipeline(*, embedder: Embedder) -> IngestionPipeline:
    """The production doc-ingest pipeline: dispatch-split → embed. The
    Ingestor feeds `Document` objects (carrying mime + filename metadata)
    into `pipeline.run`, then maps the resulting embedded nodes back to
    `DocChunk` storage."""
    return IngestionPipeline(
        transformations=[
            DispatchSplitter(),
            EmbedderAdapter(embedder),
        ],
    )
