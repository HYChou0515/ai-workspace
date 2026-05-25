"""SourceDoc ids must be slash-free (specstar can't store '/' in an id) and
stable (the same natural key always maps to the same id, for dedup)."""

from workspace_app.kb.doc_id import encode_doc_id


def test_encoded_id_has_no_slash():
    enc = encode_doc_id("collection:c1", "alice", "manuals/reflow/guide.md")
    assert "/" not in enc


def test_same_natural_key_is_stable():
    a = encode_doc_id("c1", "u", "guide.md")
    b = encode_doc_id("c1", "u", "guide.md")
    assert a == b


def test_distinct_keys_get_distinct_ids():
    assert encode_doc_id("c1", "u", "a.md") != encode_doc_id("c1", "u", "b.md")
    assert encode_doc_id("c1", "alice", "a.md") != encode_doc_id("c1", "bob", "a.md")
