"""#390 cross-path index-result cache."""

from __future__ import annotations

from workspace_app.kb.index_cache import compute_cache_key
from workspace_app.resources import IndexCache
from workspace_app.resources.kb import CachedChunk


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
