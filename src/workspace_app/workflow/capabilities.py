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

from specstar import SpecStar

from ..filestore.protocol import FileStore
from ..kb.doc_id import encode_doc_id
from ..kb.ingest import Ingestor
from ..resources.kb import Collection


class CollectionNotFound(LookupError):
    """The target collection does not exist — manual §8 requires it to exist (no
    auto-create; creating collections is a separate admin action)."""


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


async def ingest_to_collection(
    spec: SpecStar,
    ingestor: Ingestor,
    store: FileStore,
    *,
    workspace_id: str,
    collection: str,
    path: str,
    user: str,
) -> str:
    """Ingest a workspace file into an existing KB collection as ``user`` (manual §8).

    Idempotent: the SourceDoc id is ``encode_doc_id(collection, path)``, so a re-run
    upserts rather than duplicating. Writes a ``step_ingest/<path>.done`` receipt so
    the deterministic node is checkpointable on re-run (manual §9). Blocking ingest
    is offloaded so it never sits on the event loop. Returns the SourceDoc id.
    """
    collection_id = resolve_collection_id(spec, collection)

    filename = path.lstrip("/")
    data = await store.read(workspace_id, _abs(path))
    ids = await asyncio.to_thread(
        ingestor.ingest, collection_id=collection_id, user=user, filename=filename, data=data
    )
    # Re-ingesting identical bytes is a no-op (returns []); the doc already exists
    # at its natural-key id (manual §8 idempotency).
    doc_id = ids[0] if ids else encode_doc_id(collection_id, filename)
    receipt = json.dumps({"doc_id": doc_id, "collection": collection_id, "path": filename})
    await store.write(workspace_id, f"/step_ingest/{filename}.done", receipt.encode())
    return doc_id
