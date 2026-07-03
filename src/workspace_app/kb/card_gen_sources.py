"""#415: the card-gen source reader — a collection's ``SourceDoc``s AND its LLM
wiki pages, both addressed by resource id, so the picker can draft cards from
either. The FE submits a wiki page by its ``WikiPage`` resource id (kb/wiki/
store.py ``_rid``) mixed into the same ``doc_ids`` list as documents; that id
misses the SourceDoc lookup, so this reader falls back to reading the wiki
page's markdown and cites it by its page path.

Only the card-gen path uses this fallback — the wiki agents' own
``SpecstarWikiSources`` stays sources-only (it must not resolve a synthesised
page as if it were a raw source).
"""

from __future__ import annotations

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources import WikiPage
from .wiki.sources import SpecstarWikiSources, WikiSourceRef


class CardGenSources(SpecstarWikiSources):
    """A ``SpecstarWikiSources`` that also resolves LLM wiki pages by id."""

    def __init__(self, spec: SpecStar, collection_id: str) -> None:
        super().__init__(spec, collection_id)
        self._wiki_rm = spec.get_resource_manager(WikiPage)

    def ref_by_id(self, doc_id: str) -> WikiSourceRef | None:
        ref = super().ref_by_id(doc_id)
        if ref is not None:
            return ref
        # Not a SourceDoc — maybe a wiki page the reviewer picked. Its id IS the
        # WikiPage resource id, so read it directly; cite it by its page path.
        try:
            resource = self._wiki_rm.get(doc_id)
        except ResourceIDNotFoundError:
            return None
        page = resource.data
        assert isinstance(page, WikiPage)  # rm is WikiPage-typed; narrows for ty
        data = self._wiki_rm.restore_binary(page).content.data
        assert isinstance(data, bytes)  # restore_binary loaded the blob; narrows for ty
        return WikiSourceRef(
            document_id=doc_id,
            collection_id=self._cid,
            path=page.path,
            text=data.decode("utf-8", errors="replace"),
        )
