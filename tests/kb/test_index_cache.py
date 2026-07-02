"""#390 cross-path index-result cache."""

from __future__ import annotations

import msgspec
from specstar import QB, SpecStar

from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.index_cache import IndexCacheStore, compute_cache_key
from workspace_app.kb.ingest import Ingestor
from workspace_app.resources import IndexCache
from workspace_app.resources.kb import CachedChunk, Collection, DocChunk, SourceDoc


def _new_collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id


def _chunk_view(spec: SpecStar, doc_id: str) -> list[tuple]:
    rm = spec.get_resource_manager(DocChunk)
    rs = [r.data for r in rm.list_resources((QB["source_doc_id"] == doc_id).build())]
    rs = [c for c in rs if isinstance(c, DocChunk)]
    rs.sort(key=lambda c: c.seq)
    return [(c.seq, c.start, c.end, c.text, tuple(c.embedding or ())) for c in rs]


def _key(**over):
    base = dict(
        content_file_id="hashA",
        guidance="",
        configs={},
        embedder_identity="litellm-m\x00",
    )
    base.update(over)
    return compute_cache_key(**base)


def test_cache_key_is_deterministic_and_slash_free():
    # A specstar resource id can't contain '/', and the key must be stable across
    # calls so the same content resolves the same entry.
    k = _key()
    assert k == _key()
    assert "/" not in k and k  # non-empty, slash-free


def test_cache_key_changes_with_each_component():
    base = _key()
    assert _key(content_file_id="hashB") != base  # different bytes
    assert _key(guidance="be terse") != base  # different prompt
    assert _key(embedder_identity="litellm-n\x00") != base  # different model
    assert _key(configs={"PdfParser": {"k": 1}}) != base  # different parser config


def test_cache_key_ignores_config_dict_ordering():
    # Two dicts that differ only in key insertion order are the SAME settings.
    a = _key(configs={"A": {"x": 1, "y": 2}, "B": {"z": 3}})
    b = _key(configs={"B": {"z": 3}, "A": {"y": 2, "x": 1}})
    assert a == b


def test_index_cache_resource_roundtrips(spec):
    # The cache row is content-addressed (id = the composite key) and stores
    # everything needed to rebuild a doc's chunks without re-embedding: the chunk
    # payloads (incl. raw vectors), the extracted text, and the preview.
    rm = spec.get_resource_manager(IndexCache)
    entry = IndexCache(
        chunks=[
            CachedChunk(
                seq=0,
                start=0,
                end=3,
                text="abc",
                parser_id="PdfParser",
                provenance={"page": 2},
                embedding=[0.1, 0.2, 0.3],
            )
        ],
        text="abc",
    )
    rm.create(entry, resource_id="k1")

    got = rm.get("k1").data
    assert isinstance(got, IndexCache)
    assert got.text == "abc"
    assert len(got.chunks) == 1
    c = got.chunks[0]
    assert (c.seq, c.start, c.end, c.text) == (0, 0, 3, "abc")
    assert c.parser_id == "PdfParser"
    assert c.provenance == {"page": 2}
    assert c.embedding == [0.1, 0.2, 0.3]
    assert c.embedding_alt is None


def test_store_get_returns_none_on_miss(spec):
    assert IndexCacheStore(spec).get("no-such-key") is None


def test_cache_roundtrip_reuses_chunks_across_paths(
    spec, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # Index a doc, cache its result, then a SECOND doc with the SAME bytes at a
    # DIFFERENT path reuses those chunks via the cache — no re-index needed.
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"hello world one two three four five"
    [doc1] = ing.ingest(collection_id=cid, user="u", filename="a.md", data=data)
    ing.write_cache(doc1)

    doc2 = ing.store_file(collection_id=cid, user="u", path="b.md", data=data)
    assert doc2 is not None and doc2 != doc1
    assert _chunk_view(spec, doc2) == []  # stored, not indexed yet

    assert ing.copy_from_cache(doc2) is True
    view1, view2 = _chunk_view(spec, doc1), _chunk_view(spec, doc2)
    assert view2 and view2 == view1  # identical chunks + vectors, reused
    doc = spec.get_resource_manager(SourceDoc).get(doc2).data
    assert isinstance(doc, SourceDoc) and doc.status == "ready"
    assert doc.text == data.decode()


def test_copy_from_cache_miss_returns_false(spec, chunker, embedder):
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    doc = ing.store_file(collection_id=cid, user="u", path="x.md", data=b"never cached")
    assert ing.copy_from_cache(doc) is False


def test_invalidate_drops_the_entry_and_is_noop_when_absent(spec, chunker, embedder):
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    data = b"payload alpha beta gamma delta"
    [doc1] = ing.ingest(collection_id=cid, user="u", filename="a.md", data=data)

    ing.invalidate_cache(doc1)  # nothing cached yet → no-op, must not raise
    ing.write_cache(doc1)
    ing.invalidate_cache(doc1)  # drops the entry

    doc2 = ing.store_file(collection_id=cid, user="u", path="c.md", data=data)
    assert ing.copy_from_cache(doc2) is False


def test_cache_key_reflects_effective_guidance_and_configs(spec, chunker, embedder):
    cid = _new_collection(spec)
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    doc = ing.store_file(collection_id=cid, user="u", path="a.md", data=b"body text here")
    k0 = ing.cache_key(doc)

    # per-doc guidance override + configs both feed the key
    drm = spec.get_resource_manager(SourceDoc)
    d = drm.get(doc).data
    assert isinstance(d, SourceDoc)
    drm.update(
        doc,
        msgspec.structs.replace(
            d, parser_guidance_override="be terse", parser_config_overrides={"P": {"k": 1}}
        ),
    )
    k1 = ing.cache_key(doc)
    assert k1 != k0

    # a collection-level parser config also shifts the key
    crm = spec.get_resource_manager(Collection)
    c = crm.get(cid).data
    assert isinstance(c, Collection)
    crm.update(cid, msgspec.structs.replace(c, parser_configs={"Q": {"m": 2}}))
    assert ing.cache_key(doc) != k1
