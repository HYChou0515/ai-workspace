"""Issue #101: collection export — build a round-trippable ZIP of a collection.

The archive holds every SourceDoc's ORIGINAL bytes at its relative ``path`` plus
a ``.kb-collection/manifest.json`` (a reserved dot-dir the importer skips, so a
real doc literally named ``manifest.json`` never collides). The manifest records
the collection settings, the document list, and the context cards so the import
endpoint can reconstruct the collection.

Download is two-step: ``prepare`` writes the zip to a temp file under
``downloads_dir()`` (off the event loop), and ``stream`` serves it once and
deletes it. ``sweep_stale_downloads`` reaps temp files a caller never streamed.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from specstar import QB

from ..resources.kb import Collection, ContextCard, SourceDoc

if TYPE_CHECKING:
    from specstar import SpecStar

# Reserved, dot-prefixed location so it sorts/clusters away from real docs and
# the importer can skip the whole dir without guessing at a bare filename.
MANIFEST_PATH = ".kb-collection/manifest.json"
MANIFEST_DIR = ".kb-collection/"
MANIFEST_VERSION = 1
# Abandoned prepares (no stream call) are reaped after this long.
DOWNLOAD_TTL_SECONDS = 3600


def downloads_dir() -> Path:
    """The temp directory holding prepared (but not-yet-streamed) export zips."""
    d = Path(tempfile.gettempdir()) / "workspace_kb_downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sweep_stale_downloads(ttl_seconds: int = DOWNLOAD_TTL_SECONDS) -> None:
    """Delete prepared zips older than ``ttl_seconds`` (callers who never
    streamed their download). Best-effort: races/permission errors are ignored."""
    now = time.time()
    for f in downloads_dir().glob("*.zip"):
        try:
            if now - f.stat().st_mtime > ttl_seconds:
                f.unlink()
        except OSError:  # pragma: no cover - defensive against races
            pass


def collection_zip_filename(name: str) -> str:
    """A filesystem-safe ``{name}.zip`` for the Content-Disposition header."""
    safe = re.sub(r"[^\w.\- ]+", "_", name).strip()
    return f"{safe or 'collection'}.zip"


def _content_type(doc: SourceDoc) -> str:
    ct = doc.content.content_type
    return ct if isinstance(ct, str) else "application/octet-stream"


def build_collection_zip(spec: SpecStar, collection_id: str, out_path: Path) -> None:
    """Write the export zip for ``collection_id`` to ``out_path``.

    Raises ``ResourceIDNotFoundError`` (via the resource manager) when the
    collection does not exist.
    """
    coll = spec.get_resource_manager(Collection).get(collection_id).data
    assert isinstance(coll, Collection)
    doc_rm = spec.get_resource_manager(SourceDoc)
    card_rm = spec.get_resource_manager(ContextCard)

    cards: list[dict[str, Any]] = []
    for rev in card_rm.list_resources((QB["collection_id"] == collection_id).build()):
        card = rev.data
        assert isinstance(card, ContextCard)
        cards.append(
            {
                # norm_keys is server-derived on import (never hand-set), so it
                # is NOT exported — the author keys/title/body are the seed.
                "keys": card.keys,
                "title": card.title,
                "body": card.body,
                "created_by": rev.meta.created_by,  # ty: ignore[unresolved-attribute]  # informational
            }
        )

    documents: list[dict[str, Any]] = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rev in doc_rm.list_resources((QB["collection_id"] == collection_id).build()):
            doc = rev.data
            assert isinstance(doc, SourceDoc)
            raw = doc_rm.restore_binary(doc).content.data
            assert isinstance(raw, bytes)
            zf.writestr(doc.path, raw)
            documents.append(
                {
                    "path": doc.path,
                    # created_by is informational: import re-stamps the importer.
                    "created_by": rev.meta.created_by,  # ty: ignore[unresolved-attribute]
                    "content_type": _content_type(doc),
                    "status": doc.status,
                }
            )
        manifest = {
            "version": MANIFEST_VERSION,
            "collection": {
                "name": coll.name,
                "description": coll.description,
                "icon": coll.icon,
                "use_rag": coll.use_rag,
                "use_wiki": coll.use_wiki,
                "wiki_maintainer_guidance": coll.wiki_maintainer_guidance,
                "wiki_reader_guidance": coll.wiki_reader_guidance,
                # embedder_id is deployment-specific: recorded for reference,
                # NOT applied on import.
                "embedder_id": coll.embedder_id,
            },
            "documents": documents,
            "context_cards": cards,
        }
        zf.writestr(MANIFEST_PATH, json.dumps(manifest, indent=2, ensure_ascii=False))
