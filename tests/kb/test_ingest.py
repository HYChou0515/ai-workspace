import io
import logging
import tarfile
import zipfile

from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor, teardown_doc_chunks
from workspace_app.resources.kb import EMBED_DIM, Collection, DocChunk, SourceDoc


def _new_collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _chunks_of(spec: SpecStar, doc_id: str) -> list[DocChunk]:
    rm = spec.get_resource_manager(DocChunk)
    rs = rm.list_resources((QB["source_doc_id"] == doc_id).build())
    return [r.data for r in rs]  # ty: ignore[invalid-return-type]


def _collection_chunk_ids(spec: SpecStar, cid: str) -> set[str]:
    rm = spec.get_resource_manager(DocChunk)
    return {
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in rm.list_resources((QB["collection_id"] == cid).build())
    }


def test_ingest_markdown_creates_sourcedoc_and_embedded_chunks(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _new_collection(spec)
    ingestor = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"# Guide\none two three four five"
    ids = ingestor.ingest(collection_id=cid, user="alice", filename="guide.md", data=data)

    assert ids == [encode_doc_id(cid, "guide.md")]
    doc = spec.get_resource_manager(SourceDoc).get(ids[0]).data
    assert doc.path == "guide.md"
    assert doc.collection_id == cid
    assert doc.content.content_type in ("text/plain", "text/markdown")

    chunks = _chunks_of(spec, ids[0])
    assert len(chunks) >= 1
    assert all(len(c.embedding) == EMBED_DIM for c in chunks)  # ty: ignore[invalid-argument-type]
    assert all(c.collection_id == cid and c.source_doc_id == ids[0] for c in chunks)
    # chunk spans are verbatim slices of the (normalized) document text
    text = b"# Guide\none two three four five".decode()
    assert all(text[c.start : c.end] == c.text for c in chunks)
    # #86: the converter text (here a noop decode+normalize) is persisted on
    # SourceDoc.text so the wiki reads the whole clean source, not the chunks.
    assert doc.text == text


def test_ingested_chunks_carry_source_file_id_of_their_content(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104: every chunk records the content hash (SourceDoc.content.file_id) of
    # the bytes it was derived from, so identical content across paths can be
    # deduped and resolved without depending on any single (deletable) SourceDoc.
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    (doc_id,) = ing.ingest(collection_id=cid, user="a", filename="g.md", data=b"one two three four")
    doc = spec.get_resource_manager(SourceDoc).get(doc_id).data

    chunks = _chunks_of(spec, doc_id)
    assert chunks
    assert doc.content.file_id  # the blob layer populated the content hash
    assert all(c.source_file_id == doc.content.file_id for c in chunks)


def test_identical_content_at_two_paths_shares_one_chunk_set(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104: uploading the SAME bytes to a second path must NOT re-chunk/embed —
    # the second doc aliases the first's chunk set (0 own chunks) instead of
    # duplicating it, while both paths (SourceDocs) are kept and readable.
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"alpha beta gamma delta epsilon zeta"
    (a,) = ing.ingest(collection_id=cid, user="u", filename="wk1/report.md", data=data)
    (b,) = ing.ingest(collection_id=cid, user="u", filename="wk2/report.md", data=data)

    assert a != b  # two distinct SourceDocs — both paths kept
    assert _chunks_of(spec, a)  # the canonical holds the shared chunk set
    assert _chunks_of(spec, b) == []  # the alias holds none (deduped)

    docs = spec.get_resource_manager(SourceDoc)
    da, db = docs.get(a).data, docs.get(b).data
    assert da.status == "ready" and db.status == "ready"
    assert db.text == da.text  # alias carries the extracted text (wiki/retrieval read it)


def test_teardown_keeps_shared_chunks_when_a_sibling_still_holds_the_content(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104: tearing down ONE holder of shared content must NOT delete the chunk
    # set while another path still holds the same content — the chunks resolve to
    # the surviving sibling by file_id (no re-home). Replaces the owner-rehome
    # interim; deletion is now a collection-scoped content refcount.
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"alpha beta gamma delta epsilon zeta"
    (owner,) = ing.ingest(collection_id=cid, user="u", filename="wk1/report.md", data=data)
    ing.ingest(collection_id=cid, user="u", filename="wk2/report.md", data=data)
    before = _collection_chunk_ids(spec, cid)
    assert before

    teardown_doc_chunks(spec, owner)  # wk2 still holds the same content

    assert _collection_chunk_ids(spec, cid) == before  # shared chunk set preserved


def test_teardown_of_an_alias_keeps_the_shared_content(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # Symmetric: tearing down the alias path leaves the shared chunk set for the
    # surviving sibling that still holds the content.
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"alpha beta gamma delta epsilon zeta"
    ing.ingest(collection_id=cid, user="u", filename="wk1/report.md", data=data)
    (alias,) = ing.ingest(collection_id=cid, user="u", filename="wk2/report.md", data=data)
    before = _collection_chunk_ids(spec, cid)

    teardown_doc_chunks(spec, alias)  # wk1 still holds the content

    assert _collection_chunk_ids(spec, cid) == before


def test_teardown_deletes_the_last_content_holders_chunks(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # No sibling shares the content → this is the LAST holder, so teardown deletes
    # the content chunk set (it governs deletion now that source_doc_id is not a
    # cascade), covering both source_doc_id-owned and content-addressed chunks.
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    (only,) = ing.ingest(collection_id=cid, user="u", filename="solo.md", data=b"one two three")
    assert _collection_chunk_ids(spec, cid)

    teardown_doc_chunks(spec, only)  # sole holder

    assert _collection_chunk_ids(spec, cid) == set()  # content chunk set removed


def test_ingest_canonicalizes_path_so_one_logical_doc_is_one_id(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # A leading slash (or other surface noise) must not split one logical doc
    # into two: the stored path + id are the canonical relative form, so
    # "/sub/g.md" and "sub/g.md" are the SAME shared doc.
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    (slashed,) = ing.ingest(collection_id=cid, user="a", filename="/sub/g.md", data=b"alpha beta")

    assert slashed == encode_doc_id(cid, "sub/g.md")
    doc = spec.get_resource_manager(SourceDoc).get(slashed).data
    assert doc.path == "sub/g.md"  # stored relative, leading slash gone

    again = ing.ingest(collection_id=cid, user="a", filename="sub/g.md", data=b"alpha beta")
    assert again == []  # same canonical id + identical bytes → no second doc


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
        encode_doc_id(cid, "docs/a.md"),
        encode_doc_id(cid, "docs/b.txt"),
    }  # png skipped
    a = spec.get_resource_manager(SourceDoc).get(encode_doc_id(cid, "docs/a.md")).data
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
    assert ids == [encode_doc_id(cid, "notes/x.md")]


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


def test_index_marks_doc_error_when_embedding_fails(
    spec: SpecStar, chunker: FixedTokenChunker, caplog
):
    class _BoomEmbedder:
        dim = EMBED_DIM
        identity = "boom"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("model down")

        def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("model down")

    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=_BoomEmbedder())
    [doc_id] = ing.store(collection_id=cid, user="u", filename="a.md", data=b"hello world")
    with caplog.at_level(logging.ERROR):
        ing.index(doc_id)  # embedding fails → recorded as error, not raised
    assert spec.get_resource_manager(SourceDoc).get(doc_id).data.status == "error"
    # the swallowed cause must be visible in the logs, not silently lost
    assert doc_id in caplog.text
    assert "model down" in caplog.text
