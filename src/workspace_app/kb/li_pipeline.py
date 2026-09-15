"""LlamaIndex `IngestionPipeline` build helpers — the P1 replacement for
Ingestor's hand-rolled chunk+embed loop. See docs/plan-llamaindex-ingest.md
§2 for scope and rationale.

We treat LI as ingest-only plumbing: the pipeline runs splitter → embedder
adapter, and Ingestor maps the resulting LI `BaseNode`s back to our
`DocChunk` storage. The Embedder Protocol and DocChunk schema are unchanged
— LI is internal to ingest.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from llama_index.core.bridge.pydantic import PrivateAttr
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import (
    CodeSplitter,
    JSONNodeParser,
    MarkdownNodeParser,
    SentenceSplitter,
)
from llama_index.core.node_parser.node_utils import build_nodes_from_splits
from llama_index.core.node_parser.text.sentence import _Split
from llama_index.core.schema import (
    BaseNode,
    Document,
    MetadataMode,
    NodeRelationship,
    TextNode,
    TransformComponent,
)

from .code_lang import code_language_for, symbol_path
from .embedder import Embedder
from .markdown_table import find_markdown_tables, row_as_col_value


@dataclass
class _OffsetSplit(_Split):
    """A `SentenceSplitter` split that knows where it sits in the text."""

    offset: int = 0


class OffsetSentenceSplitter(SentenceSplitter):
    """LlamaIndex's `SentenceSplitter`, whose nodes carry the TRUE char span of
    each chunk (plan-rag-context P14).

    The stock splitter stamps `start_char_idx` with `text.find(chunk)` — the
    first occurrence — and three review rounds showed that no search over the
    text can recover the position: the floor has to approximate an overlap
    the splitter computes as a token sum over whole splits, and every char
    bound was either inert (CJK: a 256-token chunk of the test's sentence is
    192 chars, under the 192-char bound) or a period too loose
    (an English sentence one word longer than the test's), with the error
    compounding per chunk. The splitter knows where it cut. This subclass
    carries the offset of every split through `_split` (its own recursive
    splitter, with each split located in ITS parent — exact, since the split
    functions return the text's pieces in order) and reconstructs each chunk's
    span from the run of splits `_merge` joined it from: a chunk is a run of
    consecutive splits, and the run carried into the next chunk as overlap is
    the maximal tail whose token sizes sum to at most `chunk_overlap` — the
    rule `_merge` applies. The span of a chunk the phrase fallback rewrote
    (it drops consecutive punctuation) is therefore the run's extent, which
    covers the dropped characters. `_merge` itself is not copied; if an
    upgrade changes how chunks are formed, `_spans_of_runs` raises rather
    than stamping plausible-looking offsets (the ingest marks the document
    `error`).

    Per-call state (the raw, unstripped chunks the base class hands to
    `_postprocess_chunks`, the spans) lives in a thread-local: the pipeline is
    shared by concurrent ingests. A pydantic private attribute, not a plain
    `__dict__` entry (round 6): the base component's `__getstate__` strips
    unpicklable `__dict__` keys from the LIVE instance on copy / pickle, and
    `to_json()` would choke on it."""

    _p14: threading.local = PrivateAttr(default_factory=threading.local)

    def _split(self, text: str, chunk_size: int) -> list[_Split]:  # type: ignore[override]
        return self._split_at(text, chunk_size, 0)

    def _split_at(self, text: str, chunk_size: int, base: int) -> list[_Split]:
        token_size = self._token_size(text)
        if token_size <= chunk_size:
            return [_OffsetSplit(text, True, token_size, base)]
        pieces, is_sentence = self._get_splits_by_fns(text)
        out: list[_Split] = []
        cursor = 0
        for piece in pieces:
            # The split functions return the text's pieces in order — contiguous
            # (separators kept, sentence spans) or with dropped punctuation
            # between them (the phrase regex) — so the first occurrence at or
            # after the previous piece's end is the piece.
            pos = text.find(piece, cursor)
            if pos < 0:  # pragma: no cover — a split function that rewrites text
                raise RuntimeError("LlamaIndex split function returned text not in its input")
            cursor = pos + len(piece)
            size = self._token_size(piece)
            if size <= chunk_size:
                out.append(_OffsetSplit(piece, is_sentence, size, base + pos))
            else:
                out.extend(self._split_at(piece, chunk_size, base + pos))
        return out

    def _postprocess_chunks(self, chunks: list[str]) -> list[str]:
        self._p14.raw = list(chunks)  # the unstripped runs, before blanks are dropped
        return super()._postprocess_chunks(chunks)

    def _split_text(self, text: str, chunk_size: int) -> list[str]:
        if text == "":
            self._p14.spans = [(0, 0)]
            return [text]
        splits = self._split(text, chunk_size)
        chunks = self._merge(list(splits), chunk_size)  # `_merge` pops its list
        self._p14.spans = _spans_of_runs(splits, self._p14.raw, chunk_overlap=self.chunk_overlap)
        if len(self._p14.spans) != len(chunks):  # pragma: no cover — guarded in _spans_of_runs
            raise RuntimeError("LlamaIndex changed how chunks are post-processed")
        return chunks

    def _parse_nodes(  # type: ignore[override]
        self, nodes: Sequence[BaseNode], show_progress: bool = False, **kwargs: Any
    ) -> list[BaseNode]:
        # The base class's loop, plus the span each chunk was cut at — remembered
        # by node id, because `NodeParser._postprocess_parsed_nodes` runs AFTER
        # this and stamps `parent_doc.text.find(chunk)` (the first occurrence)
        # over whatever the node carries; the override below puts ours back.
        all_nodes: list[BaseNode] = []
        spans_by_id: dict[str, tuple[int, int]] = {}
        for node in nodes:
            metadata_str = self._get_metadata_str(node)
            chunks = self.split_text_metadata_aware(
                node.get_content(metadata_mode=MetadataMode.NONE), metadata_str=metadata_str
            )
            spans = list(self._p14.spans)
            built = build_nodes_from_splits(chunks, node, id_func=self.id_func)
            for n, (start, end) in zip(built, spans, strict=True):
                n.start_char_idx, n.end_char_idx = start, end
                spans_by_id[n.node_id] = (start, end)
            all_nodes.extend(built)
        self._p14.spans_by_id = spans_by_id
        return all_nodes

    def _postprocess_parsed_nodes(  # type: ignore[override]
        self, nodes: list[BaseNode], parent_doc_map: dict[str, Document]
    ) -> list[BaseNode]:
        out = super()._postprocess_parsed_nodes(nodes, parent_doc_map)
        spans_by_id: dict[str, tuple[int, int]] = getattr(self._p14, "spans_by_id", {})
        for n in out:
            span = spans_by_id.get(n.node_id)
            if span is not None:
                n.start_char_idx, n.end_char_idx = span
        return out


def _spans_of_runs(
    splits: Sequence[_Split], raw_chunks: Sequence[str], *, chunk_overlap: int
) -> list[tuple[int, int]]:
    """The char span of every non-blank chunk, from the runs of consecutive
    splits `_merge` joined them from (see `OffsetSentenceSplitter`). Raises
    when a chunk is not such a run — a changed LlamaIndex, loudly."""
    spans: list[tuple[int, int]] = []
    a = 0
    for raw in raw_chunks:
        b, length = a, 0
        while length < len(raw) and b < len(splits):
            length += len(splits[b].text)
            b += 1
        if length != len(raw) or "".join(sp.text for sp in splits[a:b]) != raw:
            raise RuntimeError("LlamaIndex changed how chunks are merged; spans cannot be trusted")
        if raw.strip():
            spans.append(_run_span(splits[a:b]))
        # The overlap carried into the next chunk: the maximal tail of this run
        # whose token sizes fit in `chunk_overlap` — `_merge`'s rule.
        n, tokens = 0, 0
        for sp in reversed(splits[a:b]):
            if tokens + sp.token_size > chunk_overlap:
                break
            tokens += sp.token_size
            n += 1
        a = b - n
    return spans


def _run_span(run: Sequence[_Split]) -> tuple[int, int]:
    """The extent of a run of splits with the chunk's own leading / trailing
    whitespace stripped — walked split by split, so a gap the phrase fallback
    dropped between two splits never enters the arithmetic."""
    head = 0
    while head < len(run) and not run[head].text.strip():
        head += 1
    tail = len(run) - 1
    while tail >= head and not run[tail].text.strip():
        tail -= 1
    first, last = run[head], run[tail]
    assert isinstance(first, _OffsetSplit) and isinstance(last, _OffsetSplit)
    start = first.offset + (len(first.text) - len(first.text.lstrip()))
    end = last.offset + len(last.text.rstrip())
    return start, end


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
    sentence_splitter: OffsetSentenceSplitter
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
            sentence_splitter=OffsetSentenceSplitter(
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
                pieces = self._split_markdown(node)
            elif mime == "application/json" or filename.endswith((".json", ".jsonl")):
                # JSON nodes are `key path value` renderings, not slices — there
                # is nothing to locate; they keep the parser's (absent) span.
                pieces = self.json_parser.get_nodes_from_documents([node])
            elif code_lang is not None:
                pieces = self._split_code(node, code_lang)
            else:
                # `OffsetSentenceSplitter`: every piece already carries its
                # true span (P14) — no locating.
                pieces = self.sentence_splitter.get_nodes_from_documents([node])
            # plan-rag-context P8: every chunk knows which Document it came from
            # — `Ingestor._build_chunks` turns a Document-relative span into an
            # offset into the canonical text (one Document per PDF page / slide /
            # CSV row, joined with "\n\n"). The sub-splitters set this for their
            # own output; the nodes this class builds itself (`_table_node`, the
            # P6 prose windows re-split from a scratch TextNode) did not.
            source = node.as_related_node_info()  # once: it re-hashes the whole text
            for n in pieces:
                n.relationships[NodeRelationship.SOURCE] = source
            out.extend(pieces)
        for n in out:
            _fold_section(n)
        return out

    def _split_markdown(self, node: BaseNode) -> list[BaseNode]:
        """Run LI's `MarkdownNodeParser`, prepend each chunk's heading
        hierarchy ('H1 > H2') so the embedding sees the structural context,
        and (issue #116) row-explode any large Markdown table within a section
        into `col: value` row chunks."""
        out: list[BaseNode] = []
        sections = self.markdown_parser.get_nodes_from_documents([node])
        # The section spans are the base every table / window span below adds
        # to, so they must be positions (P8), not first occurrences: sections
        # are disjoint and in order, so each sits at its first occurrence
        # after the previous one's end.
        _place_after(node.get_content(), sections)
        for n in sections:
            assert isinstance(n, TextNode)  # MarkdownNodeParser only emits TextNodes
            breadcrumb = _heading_breadcrumb(n)
            content = n.get_content()
            # The heading line MarkdownNodeParser folds into the content is
            # already captured by the breadcrumb; strip it (tracking its length
            # so absolute offsets stay correct) before scanning for tables.
            body, body_offset = _strip_leading_heading(content, n)
            tables = find_markdown_tables(body)
            base = (n.start_char_idx or 0) + body_offset
            if not tables:
                # plan-rag-context P6: a section larger than the sentence window
                # is windowed (see `_prose_nodes`); one that fits stays the
                # section parser's own node, byte-identical to before.
                windows = self._prose_nodes(body, base, breadcrumb)
                if windows is None:
                    if breadcrumb:
                        n.text = f"{breadcrumb}\n\n{content}"
                    out.append(n)
                    continue
                pieces = windows
            else:
                pieces = self._emit_table_segments(body, base, tables, breadcrumb)
            # P9: a node built FROM a section (a window, a table, a row, the
            # prose beside a table) stands in for it, so it carries the
            # section's metadata — the page / outline section the parser knew
            # (`DocChunk.provenance`) and the `section` the #254 fold reads. The
            # bare TextNodes `_table_node` makes had none, so every dense page's
            # VLM description (always windowed) lost its page on the way out.
            for piece in pieces:
                piece.metadata = dict(n.metadata)
            out.extend(pieces)
        return out

    def _prose_nodes(self, body: str, base: int, breadcrumb: str) -> list[BaseNode] | None:
        """plan-rag-context P6: window a Markdown prose region that is larger
        than the sentence splitter's chunk, or return ``None`` when it fits.

        `MarkdownNodeParser` splits on headings only, with no size cap — a
        heading-less `.md` of 50,000 chars was ONE chunk and ONE vector, its
        meaning averaged into a point (measured through this pipeline), and
        every VLM description is Markdown too. Each window's span is
        `base + the splitter's own span for the piece` (`OffsetSentenceSplitter`,
        P14) into the canonical text that citations and the context walk
        index — exact for a verbatim piece, and for one the splitter's phrase
        fallback rewrote (it drops consecutive punctuation, so `exec(...)`
        comes back as `exec(.)` — 4.6% of Markdown prose windows on real docs,
        0.9% of all sentence-split chunks) the run of
        splits it was merged from, which covers the dropped characters. Every
        window carries the breadcrumb like every other Markdown chunk. A
        region that fits returns ``None`` so the caller keeps the section
        parser's own node: the common case stays byte-identical (the #390
        index cache keys on the chunk set)."""
        pieces = self.sentence_splitter.get_nodes_from_documents([TextNode(text=body)])
        if len(pieces) <= 1:
            return None
        out: list[BaseNode] = []
        for piece in pieces:
            assert isinstance(piece, TextNode)  # SentenceSplitter only emits TextNodes
            assert piece.start_char_idx is not None and piece.end_char_idx is not None
            rel_start, rel_end = piece.start_char_idx, piece.end_char_idx
            out.append(
                _table_node(breadcrumb, piece.get_content(), base + rel_start, base + rel_end)
            )
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
                out.extend(self._prose_segment(prose, base + cursor, breadcrumb))
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
            out.extend(self._prose_segment(tail, base + cursor, breadcrumb))
        return out

    def _prose_segment(self, prose: str, base: int, breadcrumb: str) -> list[BaseNode]:
        """A prose region between / around tables: one node when it fits (as
        before), windowed when it does not (P6 — the same rule as a whole
        section, so a long run of prose next to a table is not one chunk either)."""
        windows = self._prose_nodes(prose, base, breadcrumb)
        if windows is not None:
            return windows
        return [_table_node(breadcrumb, prose.strip(), base, base + len(prose))]

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
        # P8/P14: before the fold below hides the verbatim text. This
        # `CodeSplitter` chunks by `max_chars` only — contiguous, no overlap
        # (`chunk_lines_overlap` is declared and never read, review round 5) —
        # so each chunk sits at its first occurrence after the previous end.
        _place_after(source, chunks)
        # `_split_code` is only reached for a filename that `code_language_for`
        # matched, so `path` is always a non-empty code filename.
        path = str(node.metadata.get("filename", "")).strip()
        for n in chunks:
            assert isinstance(n, TextNode)  # CodeSplitter only emits TextNodes
            symbols = symbol_path(language, source, n.start_char_idx or 0)
            crumb = f"{path} > {' > '.join(symbols)}" if symbols else path
            n.text = f"{crumb}\n\n{n.get_content()}"
        return chunks


def _place_after(text: str, nodes: Sequence[BaseNode]) -> None:
    """Stamp each node's span with its first occurrence at or after the
    previous node's end — exact for a splitter whose chunks are disjoint and
    in order (Markdown sections, this `CodeSplitter`). A node whose content is
    not in ``text`` (a byte-sliced fragment) keeps the span it carries."""
    cursor = 0
    for n in nodes:
        if not isinstance(n, TextNode):
            continue
        content = n.get_content()
        pos = text.find(content, cursor) if content else -1
        if pos >= 0:
            n.start_char_idx, n.end_char_idx = pos, pos + len(content)
        if n.end_char_idx is not None:
            cursor = max(cursor, n.end_char_idx)


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
    `DocChunk` storage.

    LlamaIndex's per-pipeline transformation cache is OFF (P8): on a hit it
    hands back the nodes computed for an EARLIER run's Documents — same text,
    different objects — so their SOURCE relationship named Documents this run
    never passed in, and `_build_chunks` could not map a chunk back to the
    Document it came from (the #328 dry-run re-parses the same bytes and hit
    this every time). Dedup of identical content is ours to do, and is: the
    #390 index cache."""
    return IngestionPipeline(
        transformations=[
            DispatchSplitter(),
            EmbedderAdapter(embedder),
        ],
        disable_cache=True,
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
