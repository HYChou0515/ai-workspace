"""CardGenSources (#415) — the card-gen source reader that resolves a picked id
to text, whether it's a SourceDoc or an LLM wiki page. The picker mixes wiki
page ids (WikiPage._rid) into the same ``doc_ids`` list as documents, so this
reader must fall back to reading a wiki page when the SourceDoc lookup misses.
"""

from __future__ import annotations

from specstar.types import Binary

from workspace_app.kb.card_gen_sources import CardGenSources
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


def test_resolves_a_source_document_like_the_wiki_reader() -> None:
    spec = make_spec()
    doc_id = _add_source(spec, "c1", "reflow.md", "RZ3 is the third reflow zone")
    ref = CardGenSources(spec, "c1").ref_by_id(doc_id)
    assert ref is not None
    assert ref.path == "reflow.md"
    assert ref.text == "RZ3 is the third reflow zone"


def test_falls_back_to_a_wiki_page_when_the_source_lookup_misses() -> None:
    spec = make_spec()
    wiki_id = _add_wiki(spec, "c1", "/index.md", "# Index\nRZ3 explained here")
    ref = CardGenSources(spec, "c1").ref_by_id(wiki_id)
    assert ref is not None
    assert ref.path == "/index.md"  # cited by its wiki-page path
    assert "RZ3 explained here" in ref.text


def test_returns_none_when_the_id_is_neither_a_doc_nor_a_wiki_page() -> None:
    spec = make_spec()
    assert CardGenSources(spec, "c1").ref_by_id("does-not-exist") is None
