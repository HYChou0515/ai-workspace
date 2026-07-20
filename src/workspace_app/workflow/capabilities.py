"""Platform capabilities a workflow can invoke (#100, manual §8).

Capabilities are the reliable side-effects a *deterministic* node performs (never
the agent — manual §7). They reach platform subsystems (here: KB ingestion) and are,
in production, exposed over HTTP for sandbox code to call with the run-scoped
credential; the core logic lives here and is unit-tested directly. The HTTP +
credential wrapper is wired by the run endpoint.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from specstar import SpecStar

from ..filestore.protocol import FileStore
from ..kb.doc_id import encode_doc_id
from ..kb.ingest import Ingestor
from ..resources.kb import Collection, ContextCard


class CollectionNotFound(LookupError):
    """The target collection does not exist — manual §8 requires it to exist (no
    auto-create; creating collections is a separate admin action)."""


class CardNotFound(LookupError):
    """The target context card id does not exist (#111) — update operates on an
    existing card; a missing id is an error, never a silent create."""


class CardConflict(ValueError):
    """The caller's view of the card's current body no longer matches what's stored
    (#111) — a read-before-write guard that blocks an overwrite based on a stale read,
    forcing the AI to re-read the card first."""


def _abs(path: str) -> str:
    return path if path.startswith("/") else "/" + path


def resolve_collection_id(spec: SpecStar, ref: str) -> str:
    """Resolve a collection reference to its resource id. A workflow profile names
    its collections by their human-readable ``name`` (manual §20 ``wf.config
    ["collections"]``), but a few callers already hold the opaque id — accept either:
    treat ``ref`` as an id first, else match it against collection names. Raises
    ``CollectionNotFound`` when neither resolves (manual §8: it must already exist)."""
    from specstar import QB

    coll_rm = spec.get_resource_manager(Collection)
    try:
        coll_rm.get(ref)
        return ref  # ref is already a resource id
    except Exception:  # noqa: BLE001 — not an id; fall through to a name lookup
        pass
    for r in coll_rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        if isinstance(r.data, Collection) and r.data.name == ref:
            return r.info.resource_id  # ty: ignore[unresolved-attribute]
    raise CollectionNotFound(ref)


def collection_has_doc(spec: SpecStar, *, collection: str, path: str) -> bool:
    """Has ``path`` landed in ``collection`` (a name or id)? Backs ``check.collection_has``
    (§8). #234: ingest is async (store + enqueue), so ``landed`` means the SourceDoc EXISTS
    at its natural-key id — the deterministic upload succeeded — NOT that the background
    chunk+embed has flipped it to ``ready`` (it usually hasn't yet when the run checks).
    A missing collection or doc is simply ``False`` (fail-closed)."""
    from specstar.types import ResourceIDNotFoundError

    from ..resources.kb import SourceDoc

    try:
        collection_id = resolve_collection_id(spec, collection)
    except CollectionNotFound:
        return False
    doc_id = encode_doc_id(collection_id, path.lstrip("/"))
    try:
        doc = spec.get_resource_manager(SourceDoc).get(doc_id).data
    except ResourceIDNotFoundError:
        return False
    return isinstance(doc, SourceDoc)


async def ingest_to_collection(
    spec: SpecStar,
    ingestor: Ingestor,
    store: FileStore,
    *,
    workspace_id: str,
    collection: str,
    path: str,
    user: str,
    enqueue: Callable[[str, str], object],
    journal_dir: str = "/.workflow/_default",
) -> str:
    """Upload a workspace file into an existing KB collection as ``user`` (manual §8).

    #234: STORE only (fast — the SourceDoc lands as ``status="indexing"``), then hand
    each stored doc to ``enqueue(doc_id, collection_id)`` — the IndexCoordinator's
    ``enqueue``, which queues a durable index job a background consumer drains off the
    request path (chunk + embed). This is the same upload → auto-index path the KB
    upload endpoint takes, so the deterministic node never blocks on a slow embedder.

    Idempotent: the SourceDoc id is ``encode_doc_id(collection, path)``, so a re-run
    upserts rather than duplicating; identical bytes are a no-op (``store`` returns
    ``[]`` → nothing to enqueue). Writes a ``<journal_dir>/step_ingest/<path>.done``
    receipt so the deterministic node is checkpointable on re-run (manual §9); the
    receipt lives under the run's journal folder (#136) — ``journal_dir`` is the run
    handle's ``journal_dir`` (legacy/no-run callers fall back to ``_default``).
    Blocking ``store`` is offloaded so it never sits on the event loop. Returns the
    SourceDoc id.
    """
    collection_id = resolve_collection_id(spec, collection)

    filename = path.lstrip("/")
    data = await store.read(workspace_id, _abs(path))
    ids = await asyncio.to_thread(
        ingestor.store, collection_id=collection_id, user=user, filename=filename, data=data
    )
    # Queue the slow chunk+embed off the request path; a no-op re-upload returns [] so
    # nothing is enqueued (the doc already exists at its natural-key id, manual §8).
    for stored_id in ids:
        enqueue(stored_id, collection_id)
    doc_id = ids[0] if ids else encode_doc_id(collection_id, filename)
    receipt = json.dumps({"doc_id": doc_id, "collection": collection_id, "path": filename})
    await store.write(workspace_id, f"{journal_dir}/step_ingest/{filename}.done", receipt.encode())
    return doc_id


async def convert_upload(
    ingestor: Ingestor,
    store: FileStore,
    *,
    workspace_id: str,
    src: str,
    dest: str,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[str | None, str]:
    """Convert a staged upload to text BEFORE it is filed (#324). Reads ``src`` from the
    workspace, runs the SAME KB parsers (``Ingestor.convert`` — parse only, no chunk/embed,
    no SourceDoc), and stages the converted artifact at a path whose extension matches its
    actual content so the collection stays self-consistent (no ``.pptx`` name on markdown):

      - ``markdown`` (a parser read a binary/structured upload) → text written to ``dest.md``;
      - ``passthrough`` (already plain text/code) → the normalized text written to ``dest``;
      - ``none`` (no parser could read it) → nothing written; ``(None, "none")`` so the
        caller skips it rather than filing raw bytes.

    Returns ``(out_path, kind)`` — the bare workspace path the caller files next (so the doc
    lands at its coherent name), or ``(None, "none")`` to skip. ``dest`` carries the original
    extension so the parsers route on it. Blocking parse/VLM is offloaded off the loop;
    ``on_progress`` forwards a long parser's status to the caller."""
    data = await store.read(workspace_id, _abs(src))
    text, kind = await asyncio.to_thread(
        ingestor.convert, path=dest, data=data, on_progress=on_progress
    )
    if text is None:
        return None, "none"
    out_path = f"{dest}.md" if kind == "markdown" else dest
    await store.write(workspace_id, _abs(out_path), text.encode())
    return out_path, kind


def create_context_card(
    spec: SpecStar,
    *,
    collection: str,
    keys: list[str],
    title: str,
    body: str,
    user: str,
    reference_doc_ids: list[str] | None = None,
) -> str:
    """Create a ``ContextCard`` (#106) on an EXISTING collection as ``user`` (manual
    §8). Used by the agent's ``create_context_card`` tool (after its own exists-check)
    and as the create half of ``upsert_context_card`` (the ``→collections`` workflow's
    reliable commit of a filled glossary entry).

    Reuses #106's author logic: ``norm_keys`` is the derived, indexed lookup surface
    (``derive_norm_keys``), and an entry with no usable key falls back to the title
    (so it stays findable). Raises ``CollectionNotFound`` when the collection is
    missing. Re-run idempotency is the deterministic node's job (the ``step_card``
    receipt via ``WorkflowHandle.upsert_context_card``); this core just authors one
    card. Returns the new card's resource id.

    #518: ``reference_doc_ids`` links the documents that back the card (``None`` ⇒ no
    links, today's behaviour).
    """
    from ..kb.context_cards import derive_norm_keys
    from ..resources.kb import ContextCard

    collection_id = resolve_collection_id(spec, collection)
    eff_keys = list(keys)
    if not derive_norm_keys(eff_keys) and title.strip():
        eff_keys = [title]
    rm = spec.get_resource_manager(ContextCard)
    with rm.using(user=user):
        rev = rm.create(
            ContextCard(
                collection_id=collection_id,
                keys=eff_keys,
                norm_keys=derive_norm_keys(eff_keys),
                title=title,
                body=body,
                reference_doc_ids=list(reference_doc_ids or []),
            )
        )
    return rev.resource_id


def update_context_card(
    spec: SpecStar,
    *,
    card_id: str,
    keys: list[str],
    title: str,
    body: str,
    user: str,
    expected_body: str | None = None,
    reference_doc_ids: list[str] | None = None,
) -> str:
    """Full overwrite of an EXISTING ``ContextCard`` by id (#111) as ``user`` — the
    update counterpart of ``create_context_card``. New ``keys``/``title``/``body``
    replace the old ones; ``norm_keys`` is re-derived so lookup follows the new keys.
    The ``collection_id`` is immutable (a card stays in its collection). Empty/blank
    keys fall back to the title (mirror create) so the card stays findable. Returns
    the (unchanged) card id.

    ``expected_body`` is an optional read-before-write guard: when given, it must equal
    the card's currently-stored ``body`` or a ``CardConflict`` is raised (the agent
    surface passes the body it read so a stale read can't silently overwrite a newer
    one; the deterministic workflow surface passes ``None`` = last-write-wins).

    #518: ``reference_doc_ids`` is tri-state, because this is a FULL overwrite —
    ``None`` KEEPS the card's existing links, a list replaces them, ``[]`` clears. A
    caller that only means to refresh a definition must not cost the card the evidence
    someone curated onto it."""
    from specstar.types import ResourceIDNotFoundError

    from ..kb.context_cards import derive_norm_keys
    from ..resources.kb import ContextCard

    rm = spec.get_resource_manager(ContextCard)
    try:
        existing = rm.get(card_id).data
    except ResourceIDNotFoundError as exc:
        raise CardNotFound(card_id) from exc
    assert isinstance(existing, ContextCard)  # narrow Struct|Unset for ty
    if expected_body is not None and expected_body != existing.body:
        raise CardConflict(card_id)
    eff_keys = list(keys)
    if not derive_norm_keys(eff_keys) and title.strip():
        eff_keys = [title]
    with rm.using(user=user):
        rm.update(
            card_id,
            ContextCard(
                collection_id=existing.collection_id,  # immutable across edits
                keys=eff_keys,
                norm_keys=derive_norm_keys(eff_keys),
                title=title,
                body=body,
                # #518: absent ⇒ carry the card's existing links across the overwrite.
                reference_doc_ids=(
                    list(existing.reference_doc_ids)
                    if reference_doc_ids is None
                    else list(reference_doc_ids)
                ),
            ),
        )
    return card_id


def find_overwrite_target(
    spec: SpecStar, *, collection: str, keys: list[str], title: str
) -> tuple[ContextCard | None, int]:
    """The existing card a deterministic ``upsert_context_card(collection, keys, title)``
    would OVERWRITE, plus how many cards share the matched key (#205 — the diff "before").

    Mirrors ``upsert``'s resolution EXACTLY so the snapshot the human reviews is what the
    commit actually overwrites: same collection resolve, same ``eff_keys`` (title fallback),
    the FIRST key with a hit wins and its first card is the target. Returns ``(None, 0)``
    when no key matches — a brand-new card (no "before"). The second element is the count of
    cards carrying the matched key: ``>1`` means the term is ambiguous (names several cards)
    and only the first is overwritten — surfaced in the review summary so it isn't silent.
    """
    from ..kb.context_cards import derive_norm_keys, find_cards_by_key

    collection_id = resolve_collection_id(spec, collection)
    eff_keys = list(keys)
    if not derive_norm_keys(eff_keys) and title.strip():
        eff_keys = [title]
    for key in eff_keys:
        existing = find_cards_by_key(spec, collection_id, key)
        if existing:
            return existing[0][1], len(existing)
    return None, 0


def upsert_context_card(
    spec: SpecStar,
    *,
    collection: str,
    keys: list[str],
    title: str,
    body: str,
    user: str,
    retries: int = 3,
    reference_doc_ids: list[str] | None = None,
) -> str:
    """Create-or-update a ``ContextCard`` by key (#111) — the workflow commit path's
    ‘有就更新、沒才新增’. Resolve the collection, then for the first usable key that
    already names a card in it, overwrite that card; if no key matches, create a new one.
    Idempotent by key, so a re-run updates rather than duplicating. Returns the card id.

    #429 P5: OPTIMISTIC + conflict-retrying (the same shape as ``update_entity``) — it
    reads the card's current body, overwrites with an ``expected_body`` guard, and on a
    ``CardConflict`` (a *parallel run* moved the card between the read and the write)
    re-reads and retries up to ``retries`` times. So two workflow runs upserting the same
    card don't silently lost-update — consistency with the entity path, not a resource-by-
    resource ‘entity is guarded but a card is last-write-wins’ split. (The *agent* surface
    keeps its own create-refuses / update-with-read-guard pair.)"""
    from ..kb.context_cards import derive_norm_keys, find_cards_by_key

    collection_id = resolve_collection_id(spec, collection)
    eff_keys = list(keys)
    if not derive_norm_keys(eff_keys) and title.strip():
        eff_keys = [title]
    for _ in range(retries + 1):
        target: tuple[str, ContextCard] | None = None
        for key in eff_keys:
            existing = find_cards_by_key(spec, collection_id, key)
            if existing:
                target = existing[0]
                break
        if target is None:  # no card names this key yet → create (no conflict possible)
            return create_context_card(
                spec,
                collection=collection_id,
                keys=eff_keys,
                title=title,
                body=body,
                user=user,
                reference_doc_ids=reference_doc_ids,
            )
        try:
            return update_context_card(
                spec,
                card_id=target[0],
                keys=eff_keys,
                title=title,
                body=body,
                user=user,
                expected_body=target[1].body,  # read-before-write guard
                # #518: None here means "say nothing about links" → the update keeps
                # whatever the card already carries.
                reference_doc_ids=reference_doc_ids,
            )
        except CardConflict:
            continue  # a parallel run moved it — re-read the current card + retry
    raise CardConflict(
        f"upsert_context_card for {eff_keys!r}: too many conflicts (retried {retries} times) "
        "— a parallel run keeps moving the card"
    )
