"""IWikiSources — read-only access to a collection's raw source documents
(Karpathy layer 1) for the wiki agents (issue #50).

The maintainer reads the new source AND re-reads existing sources to
cross-reference while updating entity/concept pages; the reader reads a
source to ground/cite an answer. Both go through this seam so the agent
tools stay decoupled from specstar — a fake is injected in tests.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources import SourceDoc
from ..doc_id import encode_doc_id


@dataclass(frozen=True)
class WikiSourceRef:
    """A citable reference to one raw source document — the wiki reader cites
    back to these (option 2: provenance points at the real ``SourceDoc``, not
    the synthesised wiki page). ``document_id`` is the SourceDoc resource id."""

    document_id: str
    collection_id: str
    path: str
    text: str


class IWikiSources(abc.ABC):
    """The collection's raw sources, addressed by their display path."""

    @abc.abstractmethod
    def list(self) -> list[str]:
        """Source paths (e.g. ``reflow-spec.pdf``) in the collection."""

    @abc.abstractmethod
    def read(self, path: str) -> str | None:
        """The extracted text of one source, or None if there's no such path."""

    @abc.abstractmethod
    def ref(self, path: str) -> WikiSourceRef | None:
        """The citable reference for one source (id + collection + text), or
        None if there's no such path. The reader uses this to cite back to the
        underlying ``SourceDoc``."""

    @abc.abstractmethod
    def ref_by_id(self, doc_id: str) -> WikiSourceRef | None:
        """The citable reference for the EXACT source ``doc_id`` (its natural-key
        resource id), or None if it's gone — fetched by id, no path scan."""


class SpecstarWikiSources(IWikiSources):
    """Reads the collection's ``SourceDoc`` rows. Uses the extracted
    ``text`` when present (the meaning the maintainer cares about); falls
    back to decoding the raw blob for plain text/markdown."""

    def __init__(self, spec: SpecStar, collection_id: str) -> None:
        self._rm = spec.get_resource_manager(SourceDoc)
        self._cid = collection_id

    def _resources(self):
        return [
            r
            for r in self._rm.list_resources((QB["collection_id"] == self._cid).build())
            if isinstance(r.data, SourceDoc)
        ]

    def list(self) -> list[str]:
        # A path is one shared doc in the collection, so display labels are just
        # the bare paths (no per-uploader disambiguation needed any more).
        return sorted(r.data.path for r in self._resources())

    def read(self, path: str) -> str | None:
        ref = self.ref(path)
        return ref.text if ref is not None else None

    def ref(self, path: str) -> WikiSourceRef | None:
        # The id is the natural key {collection, path}, so a path resolves to its
        # doc by direct get — O(1), no scan of the whole collection.
        return self.ref_by_id(encode_doc_id(self._cid, path))

    def ref_by_id(self, doc_id: str) -> WikiSourceRef | None:
        try:
            r = self._rm.get(doc_id)
        except ResourceIDNotFoundError:
            return None  # the doc was deleted between enqueue and run
        return self._ref_from(r)

    def _ref_from(self, resource) -> WikiSourceRef:
        """Build a citable ref from a fetched SourceDoc resource — the extracted
        ``text`` (or decoded blob fallback) + its natural-key id and path."""
        d = resource.data
        assert isinstance(d, SourceDoc)  # callers gate on type; narrows for ty
        if d.text is not None:
            # The converter's extracted text (issue #86): VLM description for an
            # image, text-layer+VLM for a PDF, normalized text for md/txt.
            text = d.text
        else:
            # No extracted text on the row. Decode the raw blob ONLY when it's
            # text-like — NEVER feed an image/PDF's raw bytes to the agent (#86:
            # megabytes of UTF-8 garbage that blew up the wiki context window).
            # Binary with no extracted text reads empty until a reindex writes
            # SourceDoc.text. (A missing content_type predates the magic sniff;
            # treat it as text, preserving the legacy md/txt fallback.)
            ct = d.content.content_type
            if ct is None or ct.startswith("text/"):
                raw = self._rm.restore_binary(d).content.data
                text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else ""
            else:
                text = ""
        return WikiSourceRef(
            document_id=resource.info.resource_id,
            collection_id=self._cid,
            path=d.path,
            text=text,
        )
