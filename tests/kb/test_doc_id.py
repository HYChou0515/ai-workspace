"""SourceDoc ids must contain no ASCII '/' (they're embedded in kb://doc/{id}
paths) and be stable (same natural key → same id, for dedup). They stay
readable: only '/' is swapped for the look-alike '∕' (U+2215); ':', spaces,
unicode etc. are left as-is."""

from workspace_app.kb.doc_id import encode_doc_id


def test_encoded_id_has_no_ascii_slash_but_stays_readable():
    enc = encode_doc_id("collection:c1", "alice", "manuals/reflow/guide.md")
    assert "/" not in enc  # safe to embed in kb://doc/{id}
    # readable: only the slashes changed, everything else verbatim (no quoting)
    assert enc == "collection:c1∕alice∕manuals∕reflow∕guide.md"


def test_same_natural_key_is_stable():
    a = encode_doc_id("c1", "u", "guide.md")
    b = encode_doc_id("c1", "u", "guide.md")
    assert a == b


def test_distinct_keys_get_distinct_ids():
    assert encode_doc_id("c1", "u", "a.md") != encode_doc_id("c1", "u", "b.md")
    assert encode_doc_id("c1", "alice", "a.md") != encode_doc_id("c1", "bob", "a.md")
    # nested-path slashes are all swapped, so distinct nestings stay distinct
    assert encode_doc_id("c1", "u", "a/b.md") != encode_doc_id("c1", "u", "a/c.md")
