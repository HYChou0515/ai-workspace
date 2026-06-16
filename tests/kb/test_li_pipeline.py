"""LlamaIndex ingestion pipeline — the P1 replacement for Ingestor's hand-
rolled chunk+embed loop. See docs/plan-llamaindex-ingest.md §2.

Each test exercises the Ingestor through its `pipeline=` injection, against
real LI splitters + the deterministic `HashEmbedder` (no LLM). The pipeline
is what production wires into the Ingestor; tests construct it directly.
"""

from __future__ import annotations

from specstar import QB, SpecStar

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk


def _new_collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _chunks_of(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    rs = rm.list_resources((QB["source_doc_id"] == doc_id).build())
    return [r.data for r in rs]  # ty: ignore[invalid-return-type]


def test_dispatch_splitter_routes_python_to_code_splitter(spec: SpecStar, embedder: HashEmbedder):
    """A `.py` file goes through CodeSplitter (tree-sitter), producing
    function/class-boundary chunks — not sentence-window chunks. Tracer
    bullet for P3.0 code-QA support."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    # A bigger file to force CodeSplitter to actually split (tree-sitter
    # respects function boundaries; small files stay as one chunk).
    py_src = (
        "def authenticate_user(username: str, password: str) -> bool:\n"
        "    return username.lower() == password.lower()\n"
        "\n"
        "\n"
        "def calculate_score(answers: list[int]) -> float:\n"
        "    if not answers:\n"
        "        return 0.0\n"
        "    return sum(answers) / len(answers)\n"
        "\n"
        "\n"
        "class Validator:\n"
        "    def __init__(self, schema: dict) -> None:\n"
        "        self.schema = schema\n"
        "\n"
        "    def validate(self, payload: dict) -> bool:\n"
        "        for key in self.schema:\n"
        "            if key not in payload:\n"
        "                return False\n"
        "        return True\n"
    ) * 4  # repeat 4x so we force chunk splitting

    ids = ingestor.ingest(collection_id=cid, user="alice", filename="auth.py", data=py_src.encode())
    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) >= 2, "CodeSplitter should produce multiple chunks for a multi-function file"
    # Chunks land at function/class boundaries — text starts at a `def` or
    # `class` line (not mid-statement like SentenceSplitter would).
    starts = [c.text.lstrip()[:5] for c in chunks]
    assert any(s.startswith(("def", "class")) for s in starts), starts


def test_markdown_chunks_carry_heading_breadcrumb_in_text(spec: SpecStar, embedder: HashEmbedder):
    """A markdown doc with H1/H2 → chunks whose `text` (what gets embedded)
    includes the heading hierarchy as a prefix. This is the headline P1
    improvement: structure-aware embeddings via LI's MarkdownNodeParser, vs
    our old whitespace-windowing chunker that lost section context."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    data = (
        b"# Build & Deploy\n"
        b"\n"
        b"Top intro paragraph that mentions building images.\n"
        b"\n"
        b"## Docker\n"
        b"\n"
        b"The image is built from a multi-stage Dockerfile with build cache.\n"
        b"\n"
        b"## K8s\n"
        b"\n"
        b"Deploy via helm chart against the staging cluster.\n"
    )

    ids = ingestor.ingest(collection_id=cid, user="alice", filename="build.md", data=data)
    assert ids == [encode_doc_id(cid, "build.md")]

    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) >= 1
    # Every chunk is properly embedded at the right dim.
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)
    # At least one chunk for body content under "Docker" carries BOTH the H1
    # and the H2 in its text — that's the breadcrumb prepend.
    docker_chunks = [c for c in chunks if "Dockerfile" in c.text]
    assert docker_chunks, "expected at least one chunk containing the Docker body"
    assert any("Build & Deploy" in c.text and "Docker" in c.text for c in docker_chunks), (
        "Docker-body chunk should include its heading hierarchy"
    )


_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
    b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
    b"3 0 obj <</Type /Page /Parent 2 0 R /Contents 4 0 R /Resources <</Font <</F1 "
    b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>>>>>>> endobj\n"
    b"4 0 obj <</Length 44>> stream\n"
    b"BT /F1 12 Tf 100 700 Td (Hello PDF World) Tj ET\nendstream endobj\n"
    b"xref\n"
    b"0 5\n"
    b"0000000000 65535 f\n"
    b"0000000009 00000 n\n"
    b"0000000055 00000 n\n"
    b"0000000098 00000 n\n"
    b"0000000182 00000 n\n"
    b"trailer <</Size 5 /Root 1 0 R>>\n"
    b"startxref\n275\n%%EOF\n"
)


def test_ingests_pdf_via_pdfreader_into_chunks(spec: SpecStar, embedder: HashEmbedder):
    """A PDF upload routes through `PDFReader` for text extraction, then
    through the sentence-splitter fallback. Headline new capability: until
    P1 we only ingested text/markdown."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    ids = ingestor.ingest(collection_id=cid, user="alice", filename="paper.pdf", data=_MINIMAL_PDF)
    assert ids == [encode_doc_id(cid, "paper.pdf")]
    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) >= 1
    # The extracted PDF text made it into at least one chunk.
    assert any("Hello PDF World" in c.text for c in chunks)
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)


def test_ingests_html_via_reader_into_chunks(spec: SpecStar, embedder: HashEmbedder):
    """HTML uploads → `HTMLTagReader` extracts the `<body>` text, then the
    sentence-splitter chunks it. Tags are stripped."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    data = (
        b"<!DOCTYPE html><html><head><title>x</title></head><body>"
        b"<h1>Welcome</h1><p>This is a paragraph in the page body about widgets.</p>"
        b"<script>alert('xss')</script>"
        b"</body></html>"
    )
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="page.html", data=data)
    assert ids == [encode_doc_id(cid, "page.html")]
    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) >= 1
    # Body text survived the tag-strip; the script alert text did NOT
    # (HTMLTagReader pulls just the <body>'s rendered content).
    assert any("widgets" in c.text for c in chunks)
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)


def test_reingesting_identical_bytes_does_not_churn(spec: SpecStar, embedder: HashEmbedder):
    """The pipeline path must NOT break the existing `_store_file` xxh3 guard:
    re-ingesting the same bytes returns no touched ids (so the slow indexing
    step never runs), preserving doc revision history."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    data = b"# Hello\n\nIdentical bytes on both calls."
    first = ingestor.ingest(collection_id=cid, user="alice", filename="x.md", data=data)
    second = ingestor.ingest(collection_id=cid, user="alice", filename="x.md", data=data)
    assert first == [encode_doc_id(cid, "x.md")]
    # Second ingest is a no-op at the store layer — no doc id "touched".
    assert second == []


def test_chunk_char_offsets_are_within_canonical_text_bounds(
    spec: SpecStar, embedder: HashEmbedder
):
    """Citation highlight relies on `text[start:end]` being a valid slice
    of the doc's canonical text. The SentenceSplitter records correct
    `start_char_idx`/`end_char_idx`; we must persist those (not garbage)."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    data = (b"Lorem ipsum dolor sit amet. " * 40).strip()
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="lorem.txt", data=data)
    chunks = _chunks_of(spec, ids[0])
    canonical = data.decode("utf-8")
    for c in chunks:
        assert 0 <= c.start <= c.end <= len(canonical), (c.start, c.end, len(canonical))


def test_zip_archive_expands_and_ingests_each_member(spec: SpecStar, embedder: HashEmbedder):
    """Regression: the archive-expansion path (zip/tar) still runs under the
    pipeline mode. Each text/markdown member becomes its own SourceDoc."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.md", "# A\n\nfirst doc body.")
        z.writestr("b.md", "# B\n\nsecond doc body.")
    archive = buf.getvalue()

    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    ids = ingestor.ingest(collection_id=cid, user="alice", filename="bundle.zip", data=archive)
    assert sorted(ids) == sorted(
        [
            encode_doc_id(cid, "a.md"),
            encode_doc_id(cid, "b.md"),
        ]
    )
    for doc_id in ids:
        chunks = _chunks_of(spec, doc_id)
        assert len(chunks) >= 1


# Issue #39: the legacy `test_reader_for_picks_correctly_or_returns_none`
# + `test_lazy_docx_reader_constructs` tests covered the if/elif chain
# in `kb/li_pipeline.py::reader_for`, which is gone now. Equivalent
# coverage lives under `tests/kb/parsers/test_llamaindex_readers.py`
# (each parser's `matches(...)` + the lazy reader constructors as
# module helpers on `kb/parsers/llamaindex_readers.py`).


def test_parser_text_is_persisted_on_sourcedoc_pre_chunk(spec: SpecStar, embedder: HashEmbedder):
    """#86: the 'text converter' (parser) output — the whole-Document text
    BEFORE the chunker — is persisted on SourceDoc.text so the wiki reads clean
    text. It must be the joined parser Documents (overlap-free, breadcrumb-free),
    never the raw upload bytes that previously blew up the wiki agent's context.
    """
    from llama_index.core.schema import Document

    from workspace_app.kb.parsers import IParser, ParserRegistry
    from workspace_app.resources.kb import SourceDoc

    class TwoDocParser(IParser):
        def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
            return filename.endswith(".img")

        def parse(self, source, *, filename, mime, on_progress=None, on_preview=None):  # type: ignore[no-untyped-def]
            return [
                Document(text="VLM description of the diagram", metadata={}),
                Document(text="second region text", metadata={}),
            ]

    registry = ParserRegistry().register(TwoDocParser())
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder, parser_registry=registry)
    cid = _new_collection(spec)
    ids = ingestor.ingest(
        collection_id=cid, user="alice", filename="diagram.img", data=b"\x89PNG\x00\x01rawbytes"
    )

    doc = spec.get_resource_manager(SourceDoc).get(ids[0]).data
    assert isinstance(doc, SourceDoc)
    assert doc.text == "VLM description of the diagram\n\nsecond region text"
    assert "rawbytes" not in (doc.text or "")  # the #86 blowup: never the raw bytes


def test_inline_text_is_persisted_on_sourcedoc(spec: SpecStar, embedder: HashEmbedder):
    """A noop 'text converter' (md/txt: no parser claims it) still persists its
    normalized text on SourceDoc.text — and WITHOUT the heading breadcrumbs the
    chunker folds into DocChunk.text, so the wiki gets the clean source."""
    from workspace_app.resources.kb import SourceDoc

    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)
    data = b"# Title\n\nBody under the heading.\n"
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="n.md", data=data)

    doc = spec.get_resource_manager(SourceDoc).get(ids[0]).data
    assert isinstance(doc, SourceDoc)
    assert doc.text is not None
    assert "Body under the heading." in doc.text
    # clean source text, not the breadcrumbed chunk representation
    assert doc.text == data.decode().strip()


def test_ingest_stores_unknown_type_with_zero_chunks(spec: SpecStar, embedder: HashEmbedder):
    """Issue #39 Q9b: pipeline mode stores every upload regardless of
    whether a parser claims it. When no parser matches (empty registry
    here, even though magic sees `application/pdf`), the doc lands as
    `status=ready` with zero chunks — so a custom parser registered
    later can reindex without re-uploading the bytes."""
    from workspace_app.kb.parsers import ParserRegistry
    from workspace_app.resources.kb import SourceDoc

    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    empty_registry = ParserRegistry()
    ingestor = Ingestor(
        spec,
        pipeline=pipeline,
        embedder=embedder,
        parser_registry=empty_registry,
    )

    ids = ingestor.ingest(collection_id=cid, user="alice", filename="x.pdf", data=_MINIMAL_PDF)
    assert ids == [encode_doc_id(cid, "x.pdf")]
    chunks = _chunks_of(spec, ids[0])
    assert chunks == []
    # Doc is reachable as ready (not "indexing"), so the FE doesn't
    # show a stuck spinner on an upload that no parser claims.
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(ids[0]).data
    assert isinstance(doc, SourceDoc)
    assert doc.status == "ready"
    # #86: a binary no parser claims has NO extracted text — leave it None
    # rather than persisting (then later decoding) the raw bytes.
    assert doc.text is None


def test_dispatch_splitter_routes_json_to_json_node_parser(embedder: HashEmbedder):
    """Issue #39 P7: a `.json` Document goes through `JSONNodeParser`
    (structure-aware: one node per top-level array element, each leaf
    rendered as a `key path value` line carrying ancestor keys) — NOT
    through the SentenceSplitter, which would cut mid-record."""
    from llama_index.core.schema import Document

    from workspace_app.kb.li_pipeline import DispatchSplitter

    doc = Document(
        text='[{"name": "Bob", "contact": {"email": "bob@x.com"}}, {"name": "Amy"}]',
        metadata={"filename": "users.json", "mime": "application/json"},
    )
    nodes = DispatchSplitter()([doc])
    # One node per array element — records never straddle chunks.
    assert len(nodes) == 2
    # Leaf lines carry the ancestor key path ("contact email bob@x.com").
    assert "name Bob" in nodes[0].get_content()
    assert "contact email bob@x.com" in nodes[0].get_content()
    assert "name Amy" in nodes[1].get_content()


def test_ingest_json_end_to_end_tags_chunks_with_json_parser(
    spec: SpecStar, embedder: HashEmbedder
):
    """End-to-end: a .json upload routes through JsonParser (parser_id
    on every chunk) and the JSON splitter branch — key-path lines in
    the chunk text make field semantics searchable."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    data = b'{"incident": {"root_cause": "etch chamber drift", "severity": "high"}}'
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="rca.json", data=data)
    assert ids == [encode_doc_id(cid, "rca.json")]
    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) >= 1
    assert all(c.parser_id == "JsonParser" for c in chunks)
    assert any("incident root_cause etch chamber drift" in c.text for c in chunks)
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)


def test_ingest_csv_end_to_end_one_row_one_chunk_with_headers(
    spec: SpecStar, embedder: HashEmbedder
):
    """End-to-end CSV (#39 P8): each row lands as its own chunk whose
    text carries the column names (`name: Bob`) — and is tagged
    parser_id="CsvParser"."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    data = b"name,email\nBob,bob@x.com\nAmy,amy@x.com\n"
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="users.csv", data=data)
    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) == 2
    assert all(c.parser_id == "CsvParser" for c in chunks)
    texts = sorted(c.text for c in chunks)
    assert texts == ["name: Amy\nemail: amy@x.com", "name: Bob\nemail: bob@x.com"]


def test_get_doc_pipeline_factory_constructs():
    """`get_doc_pipeline(settings, embedder)` wires the production pipeline."""
    from workspace_app.factories import Settings, get_doc_pipeline

    settings = Settings()
    pipeline = get_doc_pipeline(settings, HashEmbedder(dim=EMBED_DIM))
    assert pipeline is not None
    # Two transformations: DispatchSplitter + EmbedderAdapter.
    assert len(pipeline.transformations) == 2  # type: ignore[attr-defined]


def test_create_app_accepts_kb_pipeline():
    """`create_app(kb_pipeline=...)` routes through the new pipeline path
    instead of the legacy chunker — exercises the if-branch in create_app."""

    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.kb.li_pipeline import build_doc_pipeline
    from workspace_app.sandbox.mock import MockSandbox

    spec = make_spec(default_user="u")
    embedder = HashEmbedder(dim=EMBED_DIM)
    pipeline = build_doc_pipeline(embedder=embedder)
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        kb_embedder=embedder,
        kb_pipeline=pipeline,
    )
    assert app is not None  # construction succeeded → pipeline branch ran


def test_embedder_dim_mismatch_is_caught(spec: SpecStar):
    """If someone wires an embedder whose `dim` doesn't match `EMBED_DIM`
    (the DocChunk Vector column width), the ingest must fail loudly — not
    write corrupt vectors. Pipeline mode preserves this check."""

    wrong = HashEmbedder(dim=EMBED_DIM + 1)
    pipeline = build_doc_pipeline(embedder=wrong)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=wrong)
    cid = _new_collection(spec)
    # `ingest()` calls `store()` then `index()`; `index()` catches the failure
    # internally and flips status to "error", logging it. The store call
    # succeeds (it doesn't embed); the second call exposes via SourceDoc status.
    ingestor.ingest(collection_id=cid, user="alice", filename="x.md", data=b"hello")
    from workspace_app.resources.kb import SourceDoc

    doc = spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "x.md")).data
    assert doc.status == "error", "wrong-dim embed must flip doc status to error"


def test_unknown_mime_falls_back_to_sentence_splitter(spec: SpecStar, embedder: HashEmbedder):
    """A `.txt` (or anything not specifically handled) goes through the
    sentence-splitter fallback. Must still produce valid embedded chunks —
    no path crashes the pipeline on an unfamiliar source type."""
    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    # Plain text, no headings, no markdown — should hit the fallback splitter.
    data = (b"Plain text without any structure. " * 30).strip()
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="notes.txt", data=data)
    chunks = _chunks_of(spec, ids[0])

    assert len(chunks) >= 1
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)
    # The fallback did NOT prepend a markdown heading breadcrumb — text is
    # the raw sentence content.
    assert not any(c.text.startswith("# ") or " > " in c.text[:50] for c in chunks)


def test_set_status_detail_writes_and_swallows_missing_doc(spec: SpecStar, embedder: HashEmbedder):
    """Issue #39 Q11 plumbing: `_set_status_detail` writes the message
    onto the SourceDoc; when the doc vanished mid-parse (or any update
    error) it logs + swallows so the parser is never crashed by a
    progress report."""
    from workspace_app.resources.kb import SourceDoc

    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="a.md", data=b"# a\n\nbody")

    ingestor._set_status_detail(ids[0], "PdfParser: page 2/9 -> VLM")
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(ids[0]).data
    assert isinstance(doc, SourceDoc)
    assert doc.status_detail == "PdfParser: page 2/9 -> VLM"

    # Missing doc → swallowed (no raise).
    ingestor._set_status_detail("no-such-doc", "whatever")


def test_parser_on_progress_lands_on_status_detail_during_index(
    spec: SpecStar, embedder: HashEmbedder
):
    """End-to-end Q11: a parser that reports progress during parse()
    has its message visible on the SourceDoc row WHILE indexing runs
    (observed from inside the parse call — exactly what the FE's poll
    would see)."""
    from workspace_app.kb.parsers import IParser, ParserRegistry
    from workspace_app.resources.kb import SourceDoc

    seen: list[str] = []

    class SlowParser(IParser):
        def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
            return filename.endswith(".bin")

        def parse(self, source, *, filename, mime, on_progress=None, on_preview=None):  # type: ignore[no-untyped-def]
            assert on_progress is not None
            on_progress("SlowParser: step 1/2")
            # Peek at the row mid-parse — the detail must already be live.
            drm = spec.get_resource_manager(SourceDoc)
            doc_id = encode_doc_id(cid, "f.bin")
            sd = drm.get(doc_id).data
            assert isinstance(sd, SourceDoc)
            seen.append(sd.status_detail)
            from llama_index.core.schema import Document

            return [Document(text="payload from slow parser", metadata={})]

    registry = ParserRegistry().register(SlowParser())
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder, parser_registry=registry)
    cid = _new_collection(spec)
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="f.bin", data=b"\x00\x01")
    assert seen == ["SlowParser: step 1/2"]
    # Success clears the detail (the FE shouldn't show a stale step).
    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(ids[0]).data
    assert isinstance(doc, SourceDoc)
    assert doc.status == "ready" and doc.status_detail == ""


def test_office_zip_containers_store_whole_not_exploded(spec: SpecStar, embedder: HashEmbedder):
    """Bug (user report): pptx/xlsx/docx are zip containers — when
    libmagic sniffs them as bare application/zip, store() unpacked
    their internal XML members instead of keeping ONE SourceDoc.
    Rule: an upload ANY parser claims is stored whole; only unclaimed
    archives expand."""
    import io
    import zipfile

    # A minimal zip that *looks* like an office file by extension; magic
    # sees application/zip (no office signature).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/slides/slide1.xml", "<p:sld/>")
    fake_pptx = buf.getvalue()

    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    # PptxParser isn't in the bundled fallback registry, so use xlsx —
    # ExcelParser IS bundled and claims .xlsx by extension.
    touched = ingestor.store(collection_id=cid, user="alice", filename="book.xlsx", data=fake_pptx)
    assert touched == [encode_doc_id(cid, "book.xlsx")]  # ONE doc, not members


def test_plain_zip_still_expands_into_members(spec: SpecStar, embedder: HashEmbedder):
    """The unclaimed-archive path must keep working — a real .zip of
    markdown files still expands one SourceDoc per member."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.md", "# A")
        z.writestr("b.md", "# B")

    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)
    touched = ingestor.store(
        collection_id=cid, user="alice", filename="docs.zip", data=buf.getvalue()
    )
    assert sorted(touched) == sorted([encode_doc_id(cid, "a.md"), encode_doc_id(cid, "b.md")])


def test_parser_preview_persists_on_sourcedoc(spec: SpecStar, embedder: HashEmbedder):
    """PPTX preview pipeline (end-to-end at the Ingestor): a parser that
    hands a derivative through `on_preview` gets it persisted on
    `SourceDoc.preview` (separate blob, original untouched). Success
    path keeps the preview alongside status=ready."""
    from specstar.types import Binary  # noqa: F401 — asserts below narrow on it

    from workspace_app.kb.parsers import IParser, ParserRegistry
    from workspace_app.resources.kb import SourceDoc

    class PreviewingParser(IParser):
        def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
            return filename.endswith(".deck")

        def parse(self, source, *, filename, mime, on_progress=None, on_preview=None):  # type: ignore[no-untyped-def]
            assert on_preview is not None
            on_preview(b"%PDF-converted", "application/pdf")
            from llama_index.core.schema import Document

            return [Document(text="slide one text", metadata={})]

    registry = ParserRegistry().register(PreviewingParser())
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder, parser_registry=registry)
    cid = _new_collection(spec)
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="x.deck", data=b"\x00deck")

    drm = spec.get_resource_manager(SourceDoc)
    doc = drm.get(ids[0]).data
    assert isinstance(doc, SourceDoc)
    assert doc.status == "ready"
    assert doc.preview is not None
    restored = drm.restore_binary(doc)
    assert restored.preview is not None and restored.preview.data == b"%PDF-converted"
    # Original upload untouched.
    assert restored.content.data == b"\x00deck"
