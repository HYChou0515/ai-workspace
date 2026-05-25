import io
import tarfile
import zipfile

from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk, SourceDoc


def _new_collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _chunks_of(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    rs = rm.list_resources((QB["source_doc_id"] == doc_id).build())
    return [r.data for r in rs]  # ty: ignore[invalid-return-type]


def test_ingest_markdown_creates_sourcedoc_and_embedded_chunks(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _new_collection(spec)
    ingestor = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"# Guide\none two three four five"
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="guide.md", data=data)

    assert ids == [encode_doc_id(cid, "alice", "guide.md")]
    doc = spec.get_resource_manager(SourceDoc).get(ids[0]).data
    assert doc.path == "guide.md"
    assert doc.collection_id == cid
    assert doc.content.content_type in ("text/plain", "text/markdown")

    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) >= 1
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)
    assert all(c.collection_id == cid and c.source_doc_id == ids[0] for c in chunks)
    # chunk spans are verbatim slices of the (normalized) document text
    text = b"# Guide\none two three four five".decode()
    assert all(text[c.start : c.end] == c.text for c in chunks)


def test_reingesting_identical_bytes_is_a_noop(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"# Guide\none two three"
    (doc_id,) = ing.ingest(collection_id=cid, user="alice", filename="g.md", data=data)
    before = len(_chunks_of(spec, doc_id))

    again = ing.ingest(collection_id=cid, user="alice", filename="g.md", data=data)
    assert again == []  # identical bytes → skipped, no new revision
    assert len(_chunks_of(spec, doc_id)) == before  # chunks unchanged


def test_reingesting_changed_content_replaces_chunks(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    (doc_id,) = ing.ingest(collection_id=cid, user="a", filename="g.md", data=b"alpha beta gamma")
    touched = ing.ingest(collection_id=cid, user="a", filename="g.md", data=b"delta epsilon zeta")

    assert touched == [doc_id]  # same id → updated in place (new revision)
    joined = " ".join(c.text for c in _chunks_of(spec, doc_id))
    assert "delta" in joined and "alpha" not in joined  # old chunks replaced


def test_ingest_zip_unpacks_text_members_and_skips_others(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("docs/"), b"")  # directory entry → skipped
        z.writestr("docs/a.md", "# A\nalpha beta gamma")
        z.writestr("docs/b.txt", "bravo charlie delta")
        z.writestr("img.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00binarynonsense")  # skipped
    cid = _new_collection(spec)
    ids = Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="a", filename="docs.zip", data=buf.getvalue()
    )
    assert set(ids) == {
        encode_doc_id(cid, "a", "docs/a.md"),
        encode_doc_id(cid, "a", "docs/b.txt"),
    }  # png skipped
    a = spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "a", "docs/a.md")).data
    assert a.path == "docs/a.md"  # archive-relative path preserved


def test_ingest_tar_gz_unpacks_text_members(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        d = tarfile.TarInfo("notes")
        d.type = tarfile.DIRTYPE  # directory member → skipped
        t.addfile(d)
        body = b"alpha beta gamma"
        info = tarfile.TarInfo("notes/x.md")
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))
    cid = _new_collection(spec)
    ids = Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="a", filename="notes.tar.gz", data=buf.getvalue()
    )
    assert ids == [encode_doc_id(cid, "a", "notes/x.md")]


def test_ingest_unsupported_single_file_is_skipped(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _new_collection(spec)
    ids = Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="a", filename="logo.png", data=b"\x89PNG\r\n\x1a\n\x00\x00binary"
    )
    assert ids == []  # non-text, non-archive → nothing ingested


def test_store_then_index_lifecycle_sets_status(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    [doc_id] = ing.store(collection_id=cid, user="u", filename="a.md", data=b"hello world")

    drm = spec.get_resource_manager(SourceDoc)
    assert drm.get(doc_id).data.status == "indexing"  # stored, not yet embedded
    assert _chunks_of(spec, doc_id) == []  # no chunks until index()

    ing.index(doc_id)
    assert drm.get(doc_id).data.status == "ready"
    assert _chunks_of(spec, doc_id)  # chunks now exist


def test_index_marks_doc_error_when_embedding_fails(spec: SpecStar, chunker: FixedTokenChunker):
    class _BoomEmbedder:
        dim = EMBED_DIM

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("model down")

        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("model down")

    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=_BoomEmbedder())
    [doc_id] = ing.store(collection_id=cid, user="u", filename="a.md", data=b"hello world")
    ing.index(doc_id)  # embedding fails → recorded as error, not raised
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.status == "error"
