"""SpecstarWikiSources (#50) — reads a collection's raw SourceDocs for the wiki
agents: extracted text when present, decoded blob otherwise, None for unknown
paths.
"""

from __future__ import annotations

from specstar.types import Binary

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.wiki.sources import SpecstarWikiSources
from workspace_app.resources import Collection, SourceDoc, make_spec


def _add(spec, cid, path, *, text, data):
    # Create with the real natural-key id (as the Ingestor does) so a
    # path → doc lookup resolves by id.
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(collection_id=cid, path=path, content=Binary(data=data), text=text),
        resource_id=encode_doc_id(cid, path),
    )


def test_reads_text_then_blob_and_none_for_missing():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    _add(spec, cid, "a.md", text="ALPHA", data=b"alpha-blob")  # has extracted text
    _add(spec, cid, "b.md", text=None, data=b"beta-blob")  # blob only

    s = SpecstarWikiSources(spec, cid)
    assert s.list() == ["a.md", "b.md"]
    assert s.read("a.md") == "ALPHA"  # prefers the extracted text
    assert s.read("b.md") == "beta-blob"  # falls back to decoding the blob
    assert s.read("missing.md") is None
    assert s.ref("missing.md") is None

    ref = s.ref("b.md")
    assert ref is not None and ref.collection_id == cid and ref.path == "b.md"


def test_list_reads_metadata_only_never_materializes_source_blobs(monkeypatch):
    """#411: listing a collection's sources (the hot ``list_sources`` wiki
    reader/maintainer/code-wiki tool) must read paths from the meta table —
    ``SourceDoc.path`` is indexed (#263) — NOT fetch every doc's full blob just
    to read a path. Each row's extracted ``text`` alone is multi-KB, so the old
    ``list_resources`` scan streamed the whole collection's text into memory on
    every listing."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    _add(spec, cid, "a.md", text="X" * 10_000, data=b"a")
    _add(spec, cid, "b.md", text="Y" * 10_000, data=b"b")

    s = SpecstarWikiSources(spec, cid)
    rm = s._rm
    calls = {"search": 0, "list": 0}
    real_search, real_list = rm.search_resources, rm.list_resources

    def spy_search(*a, **k):
        calls["search"] += 1
        return real_search(*a, **k)

    def spy_list(*a, **k):
        calls["list"] += 1
        return real_list(*a, **k)

    monkeypatch.setattr(rm, "search_resources", spy_search)
    monkeypatch.setattr(rm, "list_resources", spy_list)

    assert s.list() == ["a.md", "b.md"]  # correctness unchanged
    assert calls["list"] == 0  # blob-fetch path never taken (#411)
    assert calls["search"] >= 1


def test_image_source_never_returns_raw_bytes():
    """#86: an image SourceDoc whose extracted text isn't on the row yet must
    NOT fall back to decoding the raw image bytes — that's megabytes of UTF-8
    garbage that blew up the wiki agent's context window. Binary (non-text)
    content with text=None reads as empty; the VLM description, once on
    SourceDoc.text, reads back verbatim."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    rm = spec.get_resource_manager(SourceDoc)
    big = b"\x89PNG\r\n" + b"\xff\x00" * 100_000  # a chunky binary blob
    rm.create(
        SourceDoc(
            collection_id=cid,
            path="diagram.png",
            content=Binary(data=big, content_type="image/png"),
            text=None,
        ),
        resource_id=encode_doc_id(cid, "diagram.png"),
    )
    rm.create(
        SourceDoc(
            collection_id=cid,
            path="described.png",
            content=Binary(data=big, content_type="image/png"),
            text="A flowchart: 開始 → 驗證 → 結束",
        ),
        resource_id=encode_doc_id(cid, "described.png"),
    )

    s = SpecstarWikiSources(spec, cid)
    # No extracted text yet → empty, NOT the raw bytes.
    assert s.read("diagram.png") == ""
    # Extracted VLM text present → read it back verbatim.
    assert s.read("described.png") == "A flowchart: 開始 → 驗證 → 結束"


def test_ref_by_id_fetches_the_exact_source():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(collection_id=cid, path="guide.md", content=Binary(data=b"x"), text="GUIDE"),
        resource_id=encode_doc_id(cid, "guide.md"),
    )

    s = SpecstarWikiSources(spec, cid)
    ref = s.ref_by_id(encode_doc_id(cid, "guide.md"))
    assert ref is not None and ref.path == "guide.md" and ref.text == "GUIDE"
    assert s.ref_by_id(encode_doc_id(cid, "gone.md")) is None  # deleted / never existed
