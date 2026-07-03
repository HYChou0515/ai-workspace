"""#415: the card-gen source reader — a collection's ``SourceDoc``s AND its LLM
wiki pages, both pickable so the reviewer can draft cards from either. Documents
and wiki pages are submitted in ONE ``doc_ids`` list, but a wiki page ``/P`` and
a document ``P`` encode to the SAME resource id, so a wiki selection is
TYPE-TAGGED with a ``wiki:`` prefix (``WIKI_ID_PREFIX``). The tag — set by the
FE picker (web/src/pages/kb/cardGenSources.ts) — is what routes resolution:

  - ``wiki:<page-id>`` → read the LLM wiki page, cite it by its page path.
  - anything else       → a ``SourceDoc`` id (a miss = the doc was deleted →
    digested to nothing; NOT a same-path wiki page).

The tag is opaque downstream — it lives inertly in provenance / question source
ids (audit strings, never resolved back to a resource). Only the card-gen path
uses this reader — the wiki agents' own ``SpecstarWikiSources`` stays
sources-only (it must not resolve a synthesised page as if it were a raw source).
"""

from __future__ import annotations

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ..resources import WikiPage
from .wiki.sources import SpecstarWikiSources, WikiSourceRef

#: Prefix marking a submitted id as an LLM wiki page rather than a ``SourceDoc``.
#: Mirrored by the FE picker (web/src/pages/kb/cardGenSources.ts ``WIKI_ID_PREFIX``).
WIKI_ID_PREFIX = "wiki:"


class CardGenSources(SpecstarWikiSources):
    """A ``SpecstarWikiSources`` that also resolves LLM wiki pages by tagged id."""

    def __init__(self, spec: SpecStar, collection_id: str) -> None:
        super().__init__(spec, collection_id)
        self._wiki_rm = spec.get_resource_manager(WikiPage)

    def ref_by_id(self, doc_id: str) -> WikiSourceRef | None:
        if doc_id.startswith(WIKI_ID_PREFIX):
            return self._wiki_ref(doc_id.removeprefix(WIKI_ID_PREFIX))
        return super().ref_by_id(doc_id)

    def _wiki_ref(self, page_id: str) -> WikiSourceRef | None:
        """Read a wiki page by its ``WikiPage`` resource id; cite it by its page
        path. A missing page (deleted since the pick) resolves to nothing."""
        try:
            resource = self._wiki_rm.get(page_id)
        except ResourceIDNotFoundError:
            return None
        page = resource.data
        assert isinstance(page, WikiPage)  # rm is WikiPage-typed; narrows for ty
        data = self._wiki_rm.restore_binary(page).content.data
        assert isinstance(data, bytes)  # restore_binary loaded the blob; narrows for ty
        return WikiSourceRef(
            document_id=WIKI_ID_PREFIX + page_id,
            collection_id=self._cid,
            path=page.path,
            text=data.decode("utf-8", errors="replace"),
        )
