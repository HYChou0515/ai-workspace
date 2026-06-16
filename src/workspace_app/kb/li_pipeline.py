"""LlamaIndex `IngestionPipeline` build helpers — the P1 replacement for
Ingestor's hand-rolled chunk+embed loop. See docs/plan-llamaindex-ingest.md
§2 for scope and rationale.

We treat LI as ingest-only plumbing: the pipeline runs splitter → embedder
adapter, and Ingestor maps the resulting LI `BaseNode`s back to our
`DocChunk` storage. The Embedder Protocol and DocChunk schema are unchanged
— LI is internal to ingest.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import (
    CodeSplitter,
    JSONNodeParser,
    MarkdownNodeParser,
    SentenceSplitter,
)
from llama_index.core.schema import BaseNode, TextNode, TransformComponent

from .embedder import Embedder

# Map source-file extension → tree-sitter language name expected by
# LI's CodeSplitter (which delegates to the `tree_sitter_languages` pack).
# Kept tight to the P3.0 starter set; expand as more languages get demand.
_CODE_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
}


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
    # Issue #39 P7: JSON-aware splitter — one node per top-level array
    # element, leaf lines rendered as "key path value" so the embedding
    # carries ancestor-key context. SentenceSplitter would cut
    # mid-record and orphan values from their keys.
    json_parser: JSONNodeParser
    # Lazily filled cache of CodeSplitter(language=…) — instantiating eagerly
    # would import every tree-sitter grammar just to ingest a .md.
    code_splitters: dict[str, CodeSplitter]

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
            json_parser=JSONNodeParser(),
            code_splitters={},
        )

    def __call__(self, nodes: Sequence[BaseNode], **_kw: Any) -> list[BaseNode]:  # type: ignore[override]
        out: list[BaseNode] = []
        for node in nodes:
            mime = str(node.metadata.get("mime", "")).lower()
            filename = str(node.metadata.get("filename", "")).lower()
            code_lang = _code_language_for(filename)
            if mime == "application/json" or filename.endswith((".json", ".jsonl")):
                out.extend(self.json_parser.get_nodes_from_documents([node]))
            elif mime == "text/markdown" or filename.endswith(".md"):
                sub = self.markdown_parser.get_nodes_from_documents([node])
                # Prepend heading hierarchy to each chunk's text so the
                # embedding sees the structural context, not just body lines.
                for n in sub:
                    breadcrumb = _heading_breadcrumb(n)
                    if breadcrumb and isinstance(n, TextNode):
                        n.text = f"{breadcrumb}\n\n{n.text}"
                out.extend(sub)
            elif code_lang is not None:
                out.extend(self._split_code(node, code_lang))
            else:
                out.extend(self.sentence_splitter.get_nodes_from_documents([node]))
        return out

    def _split_code(self, node: BaseNode, language: str) -> list[BaseNode]:
        """Run LI's tree-sitter `CodeSplitter` for `language`, instantiating
        on first use and caching per-instance."""
        splitter = self.code_splitters.get(language)
        if splitter is None:
            splitter = CodeSplitter(language=language)
            self.code_splitters[language] = splitter
        return splitter.get_nodes_from_documents([node])


def _code_language_for(filename: str) -> str | None:
    """Tree-sitter language name for a code file, or None if not supported."""
    f = filename.lower()
    for ext, lang in _CODE_LANG_BY_EXT.items():
        if f.endswith(ext):
            return lang
    return None


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


# Issue #39: `reader_for(filename, mime)`, the per-extension if/elif
# chain that picked a LlamaIndex Reader for PDF/HTML/DOCX uploads, has
# been superseded by `kb/parsers/llamaindex_readers.py` (the bundled
# `PdfParser` / `HtmlParser` / `DocxParser` IParser wrappers) plus
# `factories.get_parser_registry`. The Ingestor now dispatches via
# the registry, so this module just builds the pipeline.


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


def build_chat_pipeline(*, llm: Any, embedder: Embedder) -> IngestionPipeline:
    """The P2 chat-ingest pipeline: extract insights from a RCA conversation
    via LLM → split (most insights stay as one chunk; long ones split via
    markdown parser, since insight bodies are markdown) → embed. The
    Ingestor feeds a single `Document` (the serialised conversation), then
    writes each insight-node back as a SourceDoc + DocChunk in the
    "Investigations Knowledge" collection."""
    from .insight_extractor import InsightExtractor

    return IngestionPipeline(
        transformations=[
            InsightExtractor(llm=llm),
            DispatchSplitter(),
            EmbedderAdapter(embedder),
        ],
    )
