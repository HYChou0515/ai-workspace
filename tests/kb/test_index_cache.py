"""#390 cross-path index-result cache."""

from __future__ import annotations

from workspace_app.resources import IndexCache
from workspace_app.resources.kb import CachedChunk


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
