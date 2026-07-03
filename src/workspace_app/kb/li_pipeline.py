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

from .code_lang import code_language_for, symbol_path
from .embedder import Embedder
from .markdown_table import find_markdown_tables, row_as_col_value


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
    # Issue #116: a Markdown table with MORE than this many data rows is
    # "large" → exploded into one `col: value` chunk per row (each spanning the
    # whole table); at-or-below it stays one chunk. A re-tunable hyperparameter.
    table_max_rows: int

    def __init__(
        self,
        *,
        sentence_max_tokens: int = 256,
        sentence_overlap: int = 32,
        table_max_rows: int = 10,
    ) -> None:
        super().__init__(
            sentence_splitter=SentenceSplitter(
                chunk_size=sentence_max_tokens,
                chunk_overlap=sentence_overlap,
            ),
            markdown_parser=MarkdownNodeParser(),
            json_parser=JSONNodeParser(),
            code_splitters={},
            table_max_rows=table_max_rows,
        )

    def __call__(self, nodes: Sequence[BaseNode], **_kw: Any) -> list[BaseNode]:  # type: ignore[override]
        out: list[BaseNode] = []
        for node in nodes:
            mime = str(node.metadata.get("mime", "")).lower()
            filename = str(node.metadata.get("filename", "")).lower()
            # Parsers whose OUTPUT text format differs from the source file
            # (VLM image/PDF/PPTX → Markdown, issue #115) declare it via
            # `content_format`. It wins over mime/extension: a PNG's Markdown
            # description must split on headings, not as raw token windows.
            content_format = str(node.metadata.get("content_format", "")).lower()
            code_lang = code_language_for(filename)
            if content_format == "markdown" or mime == "text/markdown" or filename.endswith(".md"):
                out.extend(self._split_markdown(node))
            elif mime == "application/json" or filename.endswith((".json", ".jsonl")):
                out.extend(self.json_parser.get_nodes_from_documents([node]))
            elif code_lang is not None:
                out.extend(self._split_code(node, code_lang))
            else:
                out.extend(self.sentence_splitter.get_nodes_from_documents([node]))
        for n in out:
            _fold_section(n)
        return out

    def _split_markdown(self, node: BaseNode) -> list[BaseNode]:
        """Run LI's `MarkdownNodeParser`, prepend each chunk's heading
        hierarchy ('H1 > H2') so the embedding sees the structural context,
        and (issue #116) row-explode any large Markdown table within a section
        into `col: value` row chunks."""
        out: list[BaseNode] = []
        for n in self.markdown_parser.get_nodes_from_documents([node]):
            assert isinstance(n, TextNode)  # MarkdownNodeParser only emits TextNodes
            breadcrumb = _heading_breadcrumb(n)
            content = n.get_content()
            # The heading line MarkdownNodeParser folds into the content is
            # already captured by the breadcrumb; strip it (tracking its length
            # so absolute offsets stay correct) before scanning for tables.
            body, body_offset = _strip_leading_heading(content, n)
            tables = find_markdown_tables(body)
            if not tables:
                if breadcrumb:
                    n.text = f"{breadcrumb}\n\n{content}"
                out.append(n)
                continue
            base = (n.start_char_idx or 0) + body_offset
            out.extend(self._emit_table_segments(body, base, tables, breadcrumb))
        return out

    def _emit_table_segments(
        self, body: str, base: int, tables: list, breadcrumb: str
    ) -> list[BaseNode]:
        """Walk a section body as alternating prose / table segments. Prose
        stays one chunk; a small table stays one chunk (its Markdown); a large
        table explodes into one `col: value` chunk per row — every row chunk
        spanning the WHOLE table so the structural merge rebuilds it and
        citations resolve. Char spans are absolute (offset by `base`)."""
        out: list[BaseNode] = []
        cursor = 0
        for t in tables:
            prose = body[cursor : t.start]
            if prose.strip():
                out.append(_table_node(breadcrumb, prose.strip(), base + cursor, base + t.start))
            span_start, span_end = base + t.start, base + t.end
            if len(t.rows) <= self.table_max_rows:
                out.append(_table_node(breadcrumb, body[t.start : t.end], span_start, span_end))
            else:
                for row in t.rows:
                    # Well-formed rows → col: value (column names travel);
                    # ragged rows are kept raw, never dropped.
                    rendered = (
                        row_as_col_value(t.header, row)
                        if len(row) == len(t.header)
                        else " | ".join(row)
                    )
                    out.append(_table_node(breadcrumb, rendered, span_start, span_end))
            cursor = t.end
        tail = body[cursor:]
        if tail.strip():
            out.append(_table_node(breadcrumb, tail.strip(), base + cursor, base + len(body)))
        return out

    def _split_code(self, node: BaseNode, language: str) -> list[BaseNode]:
        """Run LI's tree-sitter `CodeSplitter` for `language` (instantiated on
        first use, cached per-instance), then prepend a `path > Class > func`
        breadcrumb to each chunk (issue #389).

        A raw code chunk embeds poorly — the file path and the enclosing symbol
        chain are the strongest retrieval signals, and they're exactly what a
        char-window loses. Prepending that locating context before embedding is
        the lightweight "contextual retrieval" the literature recommends
        (Anthropic, *Introducing Contextual Retrieval*, 2024) — here recovered
        deterministically from the AST instead of via an LLM. The breadcrumb is
        folded into `text` (what the embedder + BM25 see) while the char span
        keeps pointing at the breadcrumb-free code, so citations still slice the
        canonical source — the same contract as the Markdown heading /
        outline-section folds."""
        splitter = self.code_splitters.get(language)
        if splitter is None:
            splitter = CodeSplitter(language=language)
            self.code_splitters[language] = splitter
        chunks = splitter.get_nodes_from_documents([node])
        source = node.get_content()
        # `_split_code` is only reached for a filename that `code_language_for`
        # matched, so `path` is always a non-empty code filename.
        path = str(node.metadata.get("filename", "")).strip()
        for n in chunks:
            assert isinstance(n, TextNode)  # CodeSplitter only emits TextNodes
            symbols = symbol_path(language, source, n.start_char_idx or 0)
            crumb = f"{path} > {' > '.join(symbols)}" if symbols else path
            n.text = f"{crumb}\n\n{n.get_content()}"
        return chunks


def _fold_section(node: BaseNode) -> None:
    """Issue #254: prepend the outline ``section`` breadcrumb to a node's text
    so the embedding captures the chapter context the bare char span loses.
    Only the section (semantic) is folded — the ``page`` number is pure noise
    to the vector and stays in provenance only. The char span is left pointing
    at the breadcrumb-free canonical text (same contract as the Markdown
    heading breadcrumb). No-op when the node has no section, isn't a TextNode,
    or already opens with the breadcrumb."""
    section = node.metadata.get("section")
    if not section or not isinstance(node, TextNode):
        return
    section = str(section)
    if node.get_content().startswith(section):
        return
    node.text = f"{section}\n\n{node.get_content()}"


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


def _deepest_heading(node: BaseNode) -> str:
    """The section's own heading text (the deepest Header_N MarkdownNodeParser
    captured) — the line it folds into the node content."""
    md = node.metadata
    for i in range(6, 0, -1):
        v = md.get(f"Header_{i}") or md.get(f"Header {i}") or md.get(f"header_{i}")
        if v:
            return str(v).strip()
    return ""


def _strip_leading_heading(content: str, node: BaseNode) -> tuple[str, int]:
    """Drop the heading line MarkdownNodeParser folds into a section's content
    (it's already in the breadcrumb). Returns (body, offset_of_body_in_content)
    so absolute char spans can be reconstructed."""
    heading = _deepest_heading(node)
    if heading and content.startswith(heading):
        stripped = content[len(heading) :].lstrip("\n")
        return stripped, len(content) - len(stripped)
    return content, 0


def _table_node(breadcrumb: str, body: str, start: int, end: int) -> TextNode:
    """A TextNode carrying the heading breadcrumb as context, with an explicit
    char span into the canonical text (issue #116 row/table chunks)."""
    text = f"{breadcrumb}\n\n{body}" if breadcrumb else body
    return TextNode(text=text, start_char_idx=start, end_char_idx=end)


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
