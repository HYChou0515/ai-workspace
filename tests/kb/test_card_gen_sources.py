"""CardGenSources (#415) — the card-gen source reader that resolves a picked id
to text, whether it's a SourceDoc or an LLM wiki page. The picker mixes wiki
page ids into the same ``doc_ids`` list as documents, TYPE-TAGGED with a
``wiki:`` prefix so a wiki page and a document sharing a path (which encode to
the SAME resource id) stay distinguishable — the tag routes resolution.
"""

from __future__ import annotations

from specstar.types import Binary

from workspace_app.kb.card_gen_sources import WIKI_ID_PREFIX, CardGenSources
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.wiki.store import _rid
from workspace_app.resources import SourceDoc, WikiPage, make_spec


def _add_source(spec, cid: str, path: str, text: str) -> str:
    rm = spec.get_resource_manager(SourceDoc)
    rm.create(
        SourceDoc(
            collection_id=cid,
            path=path,
            content=Binary(data=text.encode()),
            text=text,
            status="ready",
        ),
        resource_id=encode_doc_id(cid, path),
    )
    return encode_doc_id(cid, path)


def _add_wiki(spec, cid: str, path: str, text: str) -> str:
    rm = spec.get_resource_manager(WikiPage)
    rm.create(
        WikiPage(collection_id=cid, path=path, content=Binary(data=text.encode())),
        resource_id=_rid(cid, path),
    )
    return _rid(cid, path)


def test_resolves_a_source_document_by_its_untagged_id() -> None:
    spec = make_spec()
    doc_id = _add_source(spec, "c1", "reflow.md", "RZ3 is the third reflow zone")
    ref = CardGenSources(spec, "c1").ref_by_id(doc_id)
    assert ref is not None
    assert ref.path == "reflow.md"
    assert ref.text == "RZ3 is the third reflow zone"


def test_resolves_a_wiki_page_by_its_tagged_id() -> None:
    spec = make_spec()
    wiki_id = _add_wiki(spec, "c1", "/index.md", "# Index\nRZ3 explained here")
    ref = CardGenSources(spec, "c1").ref_by_id(WIKI_ID_PREFIX + wiki_id)
    assert ref is not None
    assert ref.path == "/index.md"  # cited by its wiki-page path
    assert "RZ3 explained here" in ref.text


def test_type_tag_disambiguates_a_doc_and_wiki_page_that_share_a_path() -> None:
    # A document at ``index.md`` and a wiki page at ``/index.md`` encode to the
    # SAME resource id — without the tag, picking the wiki page would silently
    # digest the document instead. The ``wiki:`` prefix keeps them distinct.
    spec = make_spec()
    doc_id = _add_source(spec, "c1", "index.md", "DOC body: RZ3 is a zone")
    wiki_id = _add_wiki(spec, "c1", "/index.md", "WIKI body: RZ3 explained")
    assert doc_id == wiki_id  # the collision this test guards against
    sources = CardGenSources(spec, "c1")

    doc_ref = sources.ref_by_id(doc_id)
    wiki_ref = sources.ref_by_id(WIKI_ID_PREFIX + wiki_id)

    assert doc_ref is not None and "DOC body" in doc_ref.text
    assert wiki_ref is not None and "WIKI body" in wiki_ref.text
    assert wiki_ref.path == "/index.md"


def test_untagged_id_never_falls_back_to_a_wiki_page() -> None:
    # An untagged id is a document reference; a miss means the doc was deleted
    # (digested to nothing), NOT a wiki page — even if a same-path wiki exists.
    spec = make_spec()
    _add_wiki(spec, "c1", "/gone.md", "WIKI body")
    stale_doc_id = encode_doc_id("c1", "gone.md")  # collides with the wiki id
    assert CardGenSources(spec, "c1").ref_by_id(stale_doc_id) is None


def test_returns_none_when_a_tagged_wiki_id_does_not_exist() -> None:
    spec = make_spec()
    assert CardGenSources(spec, "c1").ref_by_id(WIKI_ID_PREFIX + "nope") is None


def test_returns_none_when_the_id_is_neither_a_doc_nor_a_wiki_page() -> None:
    spec = make_spec()
    assert CardGenSources(spec, "c1").ref_by_id("does-not-exist") is None
