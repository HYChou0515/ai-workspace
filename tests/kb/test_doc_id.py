"""SourceDoc ids must contain no ASCII '/' (they're embedded in kb://doc/{id}
paths) and be stable (same natural key → same id, for dedup). They stay
readable: only '/' is swapped for the look-alike '∕' (U+2215); ':', spaces,
unicode etc. are left as-is. The key is collection + path only — NOT per-user —
so a collection is a shared space (one path = one doc whoever uploads it)."""

import pytest

from workspace_app.kb.doc_id import canonical_path, encode_doc_id


def test_canonical_path_strips_leading_slash():
    # A leading slash and its absence are the SAME logical doc — they must
    # collapse to one form so encode_doc_id mints one id, not two.
    assert canonical_path("/a.md") == canonical_path("a.md") == "a.md"


def test_canonical_path_collapses_slashes_and_dot_segments():
    # Surface noise — repeated slashes, "." segments, an inner ".." — all
    # resolve to one canonical relative path so the id stays stable.
    assert canonical_path("a//b.md") == "a/b.md"
    assert canonical_path("./a/./b.md") == "a/b.md"
    assert canonical_path("a/sub/../b.md") == "a/b.md"
    assert canonical_path("/a/b/") == "a/b"


def test_canonical_path_rejects_escape_above_root():
    # A path that climbs above its own root is nonsense for a doc key — reject
    # it loudly rather than silently clamp to something the caller didn't mean.
    with pytest.raises(ValueError):
        canonical_path("../escape.md")
    with pytest.raises(ValueError):
        canonical_path("a/../../escape.md")


def test_encoded_id_has_no_ascii_slash_but_stays_readable():
    enc = encode_doc_id("collection:c1", "manuals/reflow/guide.md")
    assert "/" not in enc  # safe to embed in kb://doc/{id}
    # readable: only the slashes changed, everything else verbatim (no quoting)
    assert enc == "collection:c1∕manuals∕reflow∕guide.md"


def test_same_natural_key_is_stable():
    assert encode_doc_id("c1", "guide.md") == encode_doc_id("c1", "guide.md")


def test_a_path_is_one_shared_id_regardless_of_uploader():
    # The id no longer encodes a user, so the SAME path in a collection is the
    # SAME doc whoever wrote it — that's what makes overwrite a shared-drive op.
    assert encode_doc_id("c1", "a.md") == encode_doc_id("c1", "a.md")


def test_distinct_paths_get_distinct_ids():
    assert encode_doc_id("c1", "a.md") != encode_doc_id("c1", "b.md")
    # nested-path slashes are all swapped, so distinct nestings stay distinct
    assert encode_doc_id("c1", "a/b.md") != encode_doc_id("c1", "a/c.md")
    # …and distinct collections never collide
    assert encode_doc_id("c1", "a.md") != encode_doc_id("c2", "a.md")
