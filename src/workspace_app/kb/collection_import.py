"""Issue #101: collection import — reconstruct a collection from an exported zip.

The archive's `.kb-collection/manifest.json` (optional) restores the collection
settings + context cards; every other member is stored VERBATIM at its path via
``Ingestor.store_file`` (NOT ``store`` — a member that is itself a ``.zip`` must
not be re-expanded) and queued for indexing.

A manifest-less zip degrades to a plain-files import: a new collection named
after the uploaded file, no settings/cards restored — so the importer doubles as
a batch folder upload. ``created_by`` is the importing user (specstar stamps the
acting user); the manifest's recorded uploader is informational only.

``mode`` governs a COLLISION when importing into an EXISTING collection —
``overwrite`` (last-write-wins, the default) or ``skip``. A document collides on
its path; a context card collides on its key (#701), resolved by the same rule
the authoring surfaces use.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import msgspec
from specstar.types import ResourceIDNotFoundError

from ..resources.kb import Collection, ContextCard, SourceDoc
from .collection_export import MANIFEST_DIR, MANIFEST_PATH
from .context_cards import (
    CardSnapshot,
    cards_with_ids_for_collections,
    derive_norm_keys,
    effective_keys,
    pick_upsert_target,
)
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


def create_collection_row(spec: SpecStar, settings: dict[str, Any], fallback_name: str) -> str:
    """Create the collection an archive's manifest describes, and nothing else.

    Public because #715's asynchronous route creates the row in the REQUEST — one
    cheap write — so the caller gets a collection id at once and the collection is
    visible while a worker fills it. Copying these few lines into the route would
    put "what an archive's settings restore to" in two places.
    """
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


def document_members(zf: zipfile.ZipFile) -> Iterator[tuple[zipfile.ZipInfo, str]]:
    """The archive members that are DOCUMENTS, each with its canonical path.

    Both importers need exactly this predicate, and it was written out twice: a
    directory entry is not a file, the reserved manifest dir is metadata rather
    than a document, and a member whose name escapes its root (zip-slip) or
    canonicalises to nothing is dropped. Two copies of a rule is one rule that
    will be wrong — a hardening applied here would have left the asynchronous
    path exactly as it was, which is the shape #701 spent four rounds fixing for
    cards and #715's `mode` handling repeated for documents.

    Yields ENTRIES, never names: a malformed archive can hold one name twice and
    `read(name)` resolves to the last of them, so one member would be written
    twice and the other never. Order is the archive's own — a caller that needs a
    reproducible one (``split`` hands batches out by index) sorts it.
    """
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if name == MANIFEST_PATH or name.startswith(MANIFEST_DIR):
            continue
        try:
            path = canonical_path(name)
        except ValueError:
            continue  # zip-slip: a member escaping its root — drop it
        if not path:
            continue  # empty after canonicalisation
        yield info, path


def restore_cards(
    spec: SpecStar, collection_id: str, cards: list[dict[str, Any]], mode: str
) -> None:
    """Restore the manifest's cards, obeying ``mode`` exactly as a colliding document
    does (#701).

    Public because the ASYNCHRONOUS importer (#715) restores cards from a worker
    rather than from the request, and both paths must apply the one rule — the
    whole point of #701 was that a second copy of a card rule drifts.

    This used to create unconditionally, so a card was the one thing a round-trip
    could not survive: re-importing an archive to correct a typo left the collection
    with two of everything. The identity of a card is its key, and the rule for
    resolving that — title fallback, first key with a hit wins — is not restated here
    but shared with the authoring surfaces (``effective_keys`` /
    ``pick_upsert_target``). It was the copy that made this diverge: this function
    carried the title-fallback half of the rule and never the create-or-update half.

    Targets come from a ``CardSnapshot`` of the collection as it stood BEFORE this
    restore, never from live storage. A batch that resolved against live storage would
    see its own writes, so two manifest cards sharing a key would collapse into one —
    trading duplication for silent loss — and it would cost a query per key per card
    on a collection that may hold thousands.
    """
    rm = spec.get_resource_manager(ContextCard)
    snapshot = CardSnapshot(cards_with_ids_for_collections(spec, [collection_id]))
    for card in cards:
        title = card.get("title", "")
        keys = effective_keys(list(card.get("keys", [])), title)
        if not derive_norm_keys(keys):
            continue  # nothing to key on → unfindable; skip
        target = pick_upsert_target(snapshot.candidates, keys)
        if target is not None and mode == "skip":
            # Claim even though nothing is written: the pairing is one-to-one either
            # way. Without this a second manifest card under the same key re-resolves
            # onto this same already-matched card and is skipped too — dropping a card
            # that collided with nothing, where a document import under `skip` would
            # have added it.
            snapshot.claim(target[0])
            continue  # same rule as a colliding path: what is already here stays
        # #518: the manifest carries links as paths (see collection_export), so re-mint
        # them as ids in the collection we are importing INTO — a doc id encodes its
        # collection, so replaying the source's ids would land every link dangling.
        # Tri-state, exactly as `update_context_card` defines it: the field ABSENT means
        # the archive says nothing and the card keeps what it has, a list replaces, and
        # an explicit `[]` clears. Silence is not a claim that there are no links, and
        # deleting curated evidence on a re-import to fix a typo is precisely what that
        # rule exists to prevent. A real export always writes the field, so round-trip
        # fidelity is untouched; silence only ever protects a partial archive.
        paths = card.get("reference_paths")
        links = None if paths is None else [encode_doc_id(collection_id, p) for p in paths]
        restored = ContextCard(
            collection_id=collection_id,
            keys=keys,
            norm_keys=derive_norm_keys(keys),
            title=title,
            body=card.get("body", ""),
            reference_doc_ids=list(links or []),
        )
        if target is None:
            rm.create(restored)
        else:
            # Pair one-to-one: a later manifest card under the same key must fall
            # through to the NEXT card carrying it, or create, never re-target this one.
            snapshot.claim(target[0])
            if links is None:
                restored = msgspec.structs.replace(
                    restored, reference_doc_ids=list(target[1].reference_doc_ids)
                )
            rm.update(target[0], restored)


def doc_exists(spec: SpecStar, collection_id: str, path: str) -> bool:
    """Whether ``path`` already names a document here — the `skip` test.

    Public because BOTH importers ask it (#715). A second copy of "what counts as a
    colliding document" is the same split #701 spent four rounds closing for cards.
    """
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
        collection_id = create_collection_row(spec, manifest.get("collection", {}), fallback_name)

    document_ids: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        for info, path in document_members(zf):
            if mode == "skip" and doc_exists(spec, collection_id, path):
                continue
            doc_id = ingestor.store_file(
                collection_id=collection_id, user=user, path=path, data=zf.read(info)
            )
            if doc_id is not None:
                document_ids.append(doc_id)

    for doc_id in document_ids:
        index_coordinator.enqueue(doc_id, collection_id)
    restore_cards(spec, collection_id, manifest.get("context_cards", []), mode)
    return ImportResult(collection_id=collection_id, document_ids=document_ids)
