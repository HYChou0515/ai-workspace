"""LlamaIndex `IngestionPipeline` build helpers — the P1 replacement for
Ingestor's hand-rolled chunk+embed loop. See docs/plan-llamaindex-ingest.md
§2 for scope and rationale.

We treat LI as ingest-only plumbing: the pipeline runs splitter → embedder
adapter, and Ingestor maps the resulting LI `BaseNode`s back to our
`DocChunk` storage. The Embedder Protocol and DocChunk schema are unchanged
— LI is internal to ingest.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import (
    CodeSplitter,
    JSONNodeParser,
    MarkdownNodeParser,
    SentenceSplitter,
)
from llama_index.core.schema import BaseNode, NodeRelationship, TextNode, TransformComponent

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
                pieces = self._split_markdown(node)
            elif mime == "application/json" or filename.endswith((".json", ".jsonl")):
                # JSON nodes are `key path value` renderings, not slices — there
                # is nothing to locate; they keep the parser's (absent) span.
                pieces = self.json_parser.get_nodes_from_documents([node])
            elif code_lang is not None:
                pieces = self._split_code(node, code_lang)
            else:
                pieces = self.sentence_splitter.get_nodes_from_documents([node])
                _relocate(node.get_content(), pieces, overlap_of=self._sentence_overlap)
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
        # to, so they must be positions (P8), not first occurrences.
        _relocate(
            node.get_content(), sections, overlap_of=lambda _prev: 0
        )  # sections never overlap
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

    def _sentence_overlap(self, prev: str) -> int:
        """How far back into the previous sentence-split piece the next one can
        start: the splitter's overlap in tokens, as a char bound (a generous
        chars-per-token), never more than the piece itself. See `_locate`."""
        return min(len(prev), self.sentence_splitter.chunk_overlap * _MAX_CHARS_PER_TOKEN)

    def _prose_nodes(self, body: str, base: int, breadcrumb: str) -> list[BaseNode] | None:
        """plan-rag-context P6: window a Markdown prose region that is larger
        than the sentence splitter's chunk, or return ``None`` when it fits.

        `MarkdownNodeParser` splits on headings only, with no size cap — a
        heading-less `.md` of 50,000 chars was ONE chunk and ONE vector, its
        meaning averaged into a point (measured through this pipeline), and
        every VLM description is Markdown too. Each window's span is
        `base + where the piece sits in the region` (`_relocate`: by position,
        not first occurrence — P8), into the canonical text that citations and
        the context walk index; a piece the splitter's phrase fallback
        rewrote (it drops consecutive punctuation, so `exec(...)` comes back
        as `exec(.)` — 4.6% of windows on real docs) is anchored on its longest
        verbatim head and tail, and one that cannot be anchored at all spans
        the whole region rather than a made-up sub-span. Every window carries
        the breadcrumb like every other Markdown chunk. A region that fits
        returns ``None`` so the caller keeps the section parser's own node: the
        common case stays byte-identical (the #390 index cache keys on the
        chunk set)."""
        pieces = self.sentence_splitter.get_nodes_from_documents([TextNode(text=body)])
        if len(pieces) <= 1:
            return None
        _relocate(body, pieces, overlap_of=self._sentence_overlap)
        out: list[BaseNode] = []
        for piece in pieces:
            assert isinstance(piece, TextNode)  # SentenceSplitter only emits TextNodes
            if piece.start_char_idx is None or piece.end_char_idx is None:
                rel_start, rel_end = 0, len(body)
            else:
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
        # P8: before the fold below hides the verbatim text. The overlap is in
        # LINES here: the next chunk reaches back at most the previous chunk's
        # last `chunk_lines_overlap` lines.
        overlap_lines = splitter.chunk_lines_overlap
        _relocate(source, chunks, overlap_of=lambda prev: _tail_lines_len(prev, overlap_lines))
        # `_split_code` is only reached for a filename that `code_language_for`
        # matched, so `path` is always a non-empty code filename.
        path = str(node.metadata.get("filename", "")).strip()
        for n in chunks:
            assert isinstance(n, TextNode)  # CodeSplitter only emits TextNodes
            symbols = symbol_path(language, source, n.start_char_idx or 0)
            crumb = f"{path} > {' > '.join(symbols)}" if symbols else path
            n.text = f"{crumb}\n\n{n.get_content()}"
        return chunks


# A non-verbatim piece is anchored on its longest verbatim head; a head shorter
# than this (or than the piece) is a coincidence, not an anchor.
_ANCHOR_MIN = 8


# A char bound on one token, for turning the sentence splitter's overlap
# (tokens) into "how far back into the previous piece the next piece can
# start". Generous on purpose: too small only costs a periodic document a drift
# of one period; too large lets the walk land a period early.
_MAX_CHARS_PER_TOKEN = 6


def _tail_lines_len(text: str, lines: int) -> int:
    """The length of the last ``lines`` lines of ``text`` — a line-overlap
    splitter's reach back into the previous chunk, exactly."""
    if lines <= 0:
        return 0
    parts = text.split("\n")
    return len("\n".join(parts[-lines:]))


# How far past the previous piece the next one can begin: pieces are
# contiguous, so at most the whitespace the splitter dropped between them.
# Bounding every search to this keeps the walk linear — a failed unbounded
# `find` scans to the end, which made a 3 MB document of rewritten pieces take
# 50 s (review round 4).
_NEAR_SLACK = 8192


def _locate(
    text: str, piece: str, *, prev_start: int | None, prev_len: int, max_overlap: int
) -> tuple[int, int] | None:
    """Where ``piece`` sits in ``text``, given the previous piece.

    plan-rag-context P8. LlamaIndex stamps each split with
    ``text.find(piece)`` — the FIRST occurrence — so a document that repeats a
    paragraph put 36 of 38 chunks inside its first 1.8k chars and the context
    walk went 18k chars back and 0 forward. Consecutive pieces are contiguous:
    the next one starts no earlier than the previous one's END minus the
    splitter's overlap (``max_overlap`` chars, a bound) — searching from THERE
    lands on the cut, not on an earlier repetition of the same text (review
    round 4: searching from the previous START still put a periodic document's
    chunks one period apart instead of one chunk apart). What remains is the
    limit of locating by text: text that repeats with a period shorter than
    the overlap bound can drift by up to one period.

    Order of preference, every search bounded to the neighbourhood the next
    piece can be in (`_NEAR_SLACK`):
    1. verbatim, from the previous END minus the overlap bound;
    2. verbatim, from just after the previous START — an overlap larger than
       the bound (unusually long tokens / lines);
    3. verbatim AT the previous start — the sentence splitter closes a short
       sentence as its own chunk and then carries it whole into the next chunk
       as overlap, so two pieces can share a start (round 4: skipping this
       pinned a 1.2k-char chunk to a later 58-char recurrence of its head);
    4. a piece the splitter's phrase fallback rewrote (it drops consecutive
       punctuation, ~5% of windows on real docs): its longest verbatim head
       (binary search — occurrence is monotone in length) fixes the start, and
       the smallest region from there that contains the piece as a
       SUBSEQUENCE (greedy, exact) fixes the end — the span covers the piece;
    5. verbatim anywhere after the previous start, unbounded — a piece far from
       its predecessor (a pathological whitespace run);
    6. ``None``: keep whatever span the node carries."""
    if not piece:
        return None
    if prev_start is None:
        loose = strict = 0
    else:
        loose = prev_start + 1
        strict = max(loose, prev_start + prev_len - max_overlap)
    horizon = strict + len(piece) + _NEAR_SLACK
    for floor in (strict, loose):
        pos = text.find(piece, floor, horizon + len(piece))
        if pos >= 0:
            return pos, pos + len(piece)
    if prev_start is not None and text.startswith(piece, prev_start):
        return prev_start, prev_start + len(piece)
    for floor in (strict, loose):
        head = _longest(
            lambda n, floor=floor: text.find(piece[:n], floor, horizon + n) >= 0, len(piece)
        )
        if head >= min(_ANCHOR_MIN, len(piece)):
            start = text.find(piece[:head], floor, horizon + head)
            end = _cover_end(text, piece, start, start + 2 * len(piece))
            return start, end if end is not None else start + head
    pos = text.find(piece, loose)
    if pos >= 0:
        return pos, pos + len(piece)
    return None


def _cover_end(text: str, piece: str, start: int, limit: int) -> int | None:
    """The smallest ``end`` such that ``piece`` is a subsequence of
    ``text[start:end]`` — greedy earliest matching is minimal. ``None`` when
    the piece is not contained before ``limit`` (the head anchor was a
    coincidence)."""
    i = start
    for ch in piece:
        j = text.find(ch, i, limit)
        if j < 0:
            return None
        i = j + 1
    return i


def _longest(holds: Callable[[int], bool], upper: int) -> int:
    """The largest ``n`` in ``[0, upper]`` for which ``holds(n)`` — ``holds``
    is monotone (true up to some length, false beyond) and ``holds(0)`` is
    taken as true."""
    lo, hi = 0, upper
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if holds(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def _relocate(text: str, nodes: Sequence[BaseNode], *, overlap_of: Callable[[str], int]) -> None:
    """Stamp each node's char span with where its content sits in ``text``,
    walking forward from the previous piece (`_locate`; ``overlap_of(prev)`` is
    how far back into the previous piece's content the next one can start —
    the splitter's overlap, in that splitter's unit). A node whose content is
    not in ``text`` at all (a breadcrumb already folded in, a `col: value` row)
    keeps the span it carries."""
    prev_start: int | None = None
    prev_content = ""
    for n in nodes:
        if not isinstance(n, TextNode):
            continue
        content = n.get_content()
        span = _locate(
            text,
            content,
            prev_start=prev_start,
            prev_len=len(prev_content),
            max_overlap=overlap_of(prev_content) if prev_content else 0,
        )
        if span is not None:
            n.start_char_idx, n.end_char_idx = span
        if n.start_char_idx is not None:
            prev_start = max(prev_start or 0, n.start_char_idx)
            prev_content = content


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
