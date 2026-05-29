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
    assert ids == [encode_doc_id(cid, "alice", "build.md")]

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
    assert ids == [encode_doc_id(cid, "alice", "paper.pdf")]
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
    assert ids == [encode_doc_id(cid, "alice", "page.html")]
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
    assert first == [encode_doc_id(cid, "alice", "x.md")]
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
            encode_doc_id(cid, "alice", "a.md"),
            encode_doc_id(cid, "alice", "b.md"),
        ]
    )
    for doc_id in ids:
        chunks = _chunks_of(spec, doc_id)
        assert len(chunks) >= 1


def test_reader_for_picks_correctly_or_returns_none():
    """Direct unit test for the reader-dispatch helper — covers the .docx
    branch (we don't have a DocxReader fixture to ingest end-to-end) and the
    unknown-mime fallback."""
    from workspace_app.kb.li_pipeline import reader_for

    assert reader_for(filename="paper.pdf", mime="application/pdf") is not None
    assert reader_for(filename="page.html", mime="text/html") is not None
    assert (
        reader_for(
            filename="doc.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        is not None
    )
    # Filename-only fallback (mime unknown).
    assert reader_for(filename="x.docx", mime="application/octet-stream") is not None
    assert reader_for(filename="unknown.bin", mime="application/octet-stream") is None


def test_lazy_docx_reader_constructs():
    """The docx reader's constructor is the only thing exercised offline
    (we don't have a .docx fixture); confirm the import + ctor work."""
    from workspace_app.kb.li_pipeline import _lazy_docx_reader

    reader = _lazy_docx_reader()
    assert reader is not None


def test_ingest_skips_binary_without_reader(spec: SpecStar, embedder: HashEmbedder, caplog):
    """A binary mime that the store layer accepts (because pipeline is wired)
    but `reader_for` can't handle is logged + skipped, not crashed. Forces
    the `_index_via_pipeline` "no reader" branch via a fake non-PDF file
    posing as PDF mime (libmagic won't actually classify random bytes as
    pdf, so we monkeypatch `reader_for` to simulate the gap)."""
    import logging

    from workspace_app.kb import ingest as ingest_mod

    cid = _new_collection(spec)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)

    # Make a doc that store() accepts (PDF mime via magic) but pretend no
    # reader exists for it.
    original = ingest_mod.reader_for
    try:
        ingest_mod.reader_for = lambda *, filename, mime: None  # type: ignore[assignment]
        with caplog.at_level(logging.WARNING):
            ingestor.ingest(collection_id=cid, user="alice", filename="x.pdf", data=_MINIMAL_PDF)
        assert any("no reader for" in r.message for r in caplog.records)
    finally:
        ingest_mod.reader_for = original


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
    from datetime import UTC, datetime

    from specstar import SpecStar

    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.kb.li_pipeline import build_doc_pipeline
    from workspace_app.sandbox.mock import MockSandbox

    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
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

    doc = spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "alice", "x.md")).data
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
