"""Issue #263: resolve a user-supplied filename → its source-doc id within the
active collections. The agent's interface currency is the filename; the opaque
id (and its `∕` separator) stays server-side — we read the resolved record's id,
never hand-build it."""

from __future__ import annotations

from specstar.types import Binary

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.doc_resolve import resolve_document
from workspace_app.resources.kb import Collection, SourceDoc


def _coll(spec, name="kb"):
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _add_doc(spec, cid, path):
    rm = spec.get_resource_manager(SourceDoc)
    rm.create(
        SourceDoc(collection_id=cid, path=path, content=Binary(data=b"x")),
        resource_id=encode_doc_id(cid, path),
    )


def test_resolve_by_exact_path(spec):
    cid = _coll(spec)
    _add_doc(spec, cid, "reports/Q3.xlsx")
    res = resolve_document(spec, [cid], "reports/Q3.xlsx")
    assert res.status == "ok"
    assert res.doc_id == encode_doc_id(cid, "reports/Q3.xlsx")


def test_resolve_by_basename_when_user_omits_the_folder(spec):
    cid = _coll(spec)
    _add_doc(spec, cid, "reports/Q3.xlsx")
    _add_doc(spec, cid, "notes.md")
    res = resolve_document(spec, [cid], "Q3.xlsx")  # just the filename
    assert res.status == "ok"
    assert res.doc_id == encode_doc_id(cid, "reports/Q3.xlsx")
    # A different basename ("Q3-old") must NOT collide — basename is exact, not
    # a substring (see #181 / the `.contains` refinement).
    _add_doc(spec, cid, "Q3-old.xlsx")
    assert resolve_document(spec, [cid], "Q3.xlsx").doc_id == encode_doc_id(cid, "reports/Q3.xlsx")


def test_resolve_ambiguous_basename_lists_candidates(spec):
    cid = _coll(spec)
    _add_doc(spec, cid, "2023/report.pdf")
    _add_doc(spec, cid, "2024/report.pdf")
    res = resolve_document(spec, [cid], "report.pdf")
    assert res.status == "ambiguous"
    assert res.candidates == ["2023/report.pdf", "2024/report.pdf"]


def test_resolve_not_found(spec):
    cid = _coll(spec)
    _add_doc(spec, cid, "notes.md")
    assert resolve_document(spec, [cid], "missing.pdf").status == "not_found"


def test_resolve_with_no_collections_is_not_found(spec):
    assert resolve_document(spec, [], "anything.pdf").status == "not_found"


def test_resolve_uncanonicalisable_name_falls_back_to_not_found(spec):
    cid = _coll(spec)
    _add_doc(spec, cid, "notes.md")
    # A path that escapes its root can't be canonicalised; resolution degrades
    # to a plain (unmatched) lookup rather than raising.
    assert resolve_document(spec, [cid], "../../etc/passwd").status == "not_found"


def test_resolve_is_scoped_to_the_given_collections(spec):
    a, b = _coll(spec, "a"), _coll(spec, "b")
    _add_doc(spec, b, "secret.pdf")
    # Searching only collection A must not reach collection B's doc.
    assert resolve_document(spec, [a], "secret.pdf").status == "not_found"
    assert resolve_document(spec, [a, b], "secret.pdf").status == "ok"


def test_resolve_folder_is_a_recursive_prefix_that_does_not_bleed_into_siblings(spec):
    """plan-rag-context P3: "the documents under this folder". `a` covers `a/x`
    and `a/b/y` but NOT `ab/z` — the match is on `a/`, never on the bare `a`."""
    from workspace_app.kb.doc_resolve import resolve_folder

    cid = _coll(spec)
    for path in ("a/x.md", "a/b/y.md", "ab/z.md", "c.md"):
        _add_doc(spec, cid, path)
    inside = {encode_doc_id(cid, "a/x.md"), encode_doc_id(cid, "a/b/y.md")}
    assert resolve_folder(spec, [cid], "a") == inside
    assert resolve_folder(spec, [cid], "a/") == inside  # a trailing slash is the same folder
    assert resolve_folder(spec, [cid], "/a") == inside  # so is a leading one
    assert resolve_folder(spec, [cid], "a/b") == {encode_doc_id(cid, "a/b/y.md")}
    assert resolve_folder(spec, [cid], "nope") == frozenset()
    assert resolve_folder(spec, [], "a") == frozenset()


def test_resolve_document_drops_the_speakers_denied_documents_before_deciding(spec):
    """#308 × #263: a denied document is neither a match nor a candidate. Without
    this, `report.pdf` with a denied `hr/report.pdf` and a readable
    `pub/report.pdf` answered "ambiguous" and LISTED the denied path — an
    existence-and-path oracle for someone barred from it."""
    cid = _coll(spec)
    _add_doc(spec, cid, "hr/report.pdf")
    _add_doc(spec, cid, "pub/report.pdf")
    denied = frozenset({encode_doc_id(cid, "hr/report.pdf")})
    res = resolve_document(spec, [cid], "report.pdf", exclude=denied)
    assert res.status == "ok" and res.path == "pub/report.pdf"
    # The denied document by its exact path gets EXACTLY the answer a
    # nonexistent path gets — here both fall through to the basename match
    # and land on the readable sibling, so nothing distinguishes them.
    as_denied = resolve_document(spec, [cid], "hr/report.pdf", exclude=denied)
    as_missing = resolve_document(spec, [cid], "nope/report.pdf", exclude=denied)
    assert as_denied == as_missing
    assert as_denied.path == "pub/report.pdf"


def test_resolve_folder_excludes_the_speakers_denied_documents(spec):
    from workspace_app.kb.doc_resolve import resolve_folder

    cid = _coll(spec)
    _add_doc(spec, cid, "hr/a.md")
    _add_doc(spec, cid, "hr/b.md")
    denied = frozenset({encode_doc_id(cid, "hr/a.md")})
    assert resolve_folder(spec, [cid], "hr", exclude=denied) == {encode_doc_id(cid, "hr/b.md")}
    # a folder holding only denied documents is empty — indistinguishable from
    # one that does not exist
    both = frozenset({encode_doc_id(cid, "hr/a.md"), encode_doc_id(cid, "hr/b.md")})
    assert resolve_folder(spec, [cid], "hr", exclude=both) == frozenset()
