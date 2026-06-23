"""Issue #101: collection import — reconstruct a collection from an exported zip.

The archive's `.kb-collection/manifest.json` (optional) restores the collection
settings + context cards; every other member is stored VERBATIM at its path via
``Ingestor.store_file`` (NOT ``store`` — a member that is itself a ``.zip`` must
not be re-expanded) and queued for indexing.

A manifest-less zip degrades to a plain-files import: a new collection named
after the uploaded file, no settings/cards restored — so the importer doubles as
a batch folder upload. ``created_by`` is the importing user (specstar stamps the
acting user); the manifest's recorded uploader is informational only.

``mode`` governs a path that already exists when importing into an EXISTING
collection: ``overwrite`` (last-write-wins, the default) or ``skip``.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from specstar.types import ResourceIDNotFoundError

from ..resources.kb import Collection, ContextCard, SourceDoc
from .collection_export import MANIFEST_DIR, MANIFEST_PATH
from .context_cards import derive_norm_keys
from .doc_id import canonical_path, encode_doc_id

if TYPE_CHECKING:
    from specstar import SpecStar

    from .index_coordinator import IndexCoordinator
    from .ingest import Ingestor


@dataclass(frozen=True)
class ImportResult:
    collection_id: str
    document_ids: list[str]
    status: str = "indexing"


def read_manifest(zip_data: bytes) -> dict[str, Any] | None:
    """The parsed `.kb-collection/manifest.json`, or ``None`` when the zip has
    none (a plain user-made archive)."""
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        try:
            raw = zf.read(MANIFEST_PATH)
        except KeyError:
            return None
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _create_collection(spec: SpecStar, settings: dict[str, Any], fallback_name: str) -> str:
    coll = Collection(
        name=settings.get("name") or fallback_name,
        description=settings.get("description", ""),
        icon=settings.get("icon", "layers"),
        use_rag=settings.get("use_rag", True),
        use_wiki=settings.get("use_wiki", False),
        wiki_maintainer_guidance=settings.get("wiki_maintainer_guidance", ""),
        wiki_reader_guidance=settings.get("wiki_reader_guidance", ""),
        # embedder_id is deployment-specific — NOT restored (target default).
    )
    rev = spec.get_resource_manager(Collection).create(coll)
    return rev.resource_id


def _restore_cards(spec: SpecStar, collection_id: str, cards: list[dict[str, Any]]) -> None:
    rm = spec.get_resource_manager(ContextCard)
    for card in cards:
        keys = list(card.get("keys", []))
        title = card.get("title", "")
        # Mirror the author action: an entry with no usable key falls back to its
        # title so it stays findable.
        if not derive_norm_keys(keys) and title.strip():
            keys = [title]
        if not derive_norm_keys(keys):
            continue  # nothing to key on → unfindable; skip
        rm.create(
            ContextCard(
                collection_id=collection_id,
                keys=keys,
                norm_keys=derive_norm_keys(keys),
                title=title,
                body=card.get("body", ""),
            )
        )


def _doc_exists(spec: SpecStar, collection_id: str, path: str) -> bool:
    rm = spec.get_resource_manager(SourceDoc)
    try:
        rm.get(encode_doc_id(collection_id, path))
    except ResourceIDNotFoundError:
        return False
    return True


def import_collection(
    *,
    spec: SpecStar,
    ingestor: Ingestor,
    index_coordinator: IndexCoordinator,
    zip_data: bytes,
    user: str,
    fallback_name: str,
    collection_id: str | None = None,
    mode: str = "overwrite",
) -> ImportResult:
    """Import ``zip_data`` into ``collection_id`` (or a new collection when
    ``None``). Blocking (zip parse + blob writes) — call off the event loop."""
    manifest = read_manifest(zip_data) or {}
    if collection_id is None:
        collection_id = _create_collection(spec, manifest.get("collection", {}), fallback_name)

    document_ids: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            # The reserved manifest dir is metadata, never a document.
            if name == MANIFEST_PATH or name.startswith(MANIFEST_DIR):
                continue
            try:
                path = canonical_path(name)
            except ValueError:
                continue  # zip-slip: a member escaping its root — drop it
            if not path:
                continue  # empty after canonicalisation
            if mode == "skip" and _doc_exists(spec, collection_id, path):
                continue
            doc_id = ingestor.store_file(
                collection_id=collection_id, user=user, path=path, data=zf.read(info)
            )
            if doc_id is not None:
                document_ids.append(doc_id)

    for doc_id in document_ids:
        index_coordinator.enqueue(doc_id, collection_id)
    _restore_cards(spec, collection_id, manifest.get("context_cards", []))
    return ImportResult(collection_id=collection_id, document_ids=document_ids)
