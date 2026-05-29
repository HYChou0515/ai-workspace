"""KB (knowledge-base chatbot) HTTP routes — collections, document upload/list,
and the document render endpoint. Registered onto the app by `create_app`.
"""

from __future__ import annotations

import asyncio
import posixpath
from datetime import datetime

import msgspec
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..kb.cited import chunk_cited, collection_cited, doc_cited
from ..kb.code_repo import CodeRepoIngestor, CodeRepoSyncError
from ..kb.ingest import Ingestor, normalize_text
from ..kb.links import rewrite_md_links
from ..resources.kb import Collection, DocChunk, SourceDoc

_DEFAULT_USER = "default-user"  # v1: no auth; uploads are attributed to this user


async def _index_in_thread(ingestor: Ingestor, doc_id: str) -> None:
    """Background indexing as an async task that immediately offloads the
    blocking embed/IO work to a worker thread — so the event loop keeps
    serving other requests while a doc embeds."""
    await asyncio.to_thread(ingestor.index, doc_id)


class _CollectionBody(BaseModel):
    name: str
    description: str = ""
    icon: str = "layers"
    # P3.0 code-repo fields. Setting git_url makes this a code Collection
    # syncable via POST /sync. embedder_id=1 routes its chunks through the
    # code-specialised embedder onto DocChunk.embedding_alt.
    git_url: str | None = None
    git_branch: str | None = None
    git_token: str | None = None  # write-only; never echoed in responses
    embedder_id: int = 0
    sync_interval_hours: int | None = None


class CollectionOut(BaseModel):
    """A collection as the card grid needs it — its own fields plus aggregates
    derived from its documents (count / total bytes / latest update)."""

    resource_id: str
    name: str
    description: str
    icon: str
    cited: int
    doc_count: int
    size: int  # total bytes across the collection's documents
    updated_at: int  # epoch ms — the most recently updated doc (or the collection)
    owner: str  # created_by
    # P3.0 code-repo metadata (None for non-code Collections). `git_token` is
    # write-only and NEVER returned — it's a secret.
    git_url: str | None = None
    git_branch: str | None = None
    git_last_sha: str | None = None
    git_last_pulled_at: int | None = None
    embedder_id: int = 0
    sync_interval_hours: int | None = None


class SyncOut(BaseModel):
    """Result of POST /kb/collections/:id/sync — the cloned HEAD sha + status."""

    status: str
    git_last_sha: str | None = None


class ReindexOut(BaseModel):
    """Result of scheduling a (re)index — how many docs were queued."""

    reindexed: int
    status: str = "indexing"


class RenderedDoc(BaseModel):
    """A source document rendered for the viewer drawer: the markdown body plus
    the metadata its header (meta strip) + actions (download / re-index / remove)
    need. `file_id` is the blob hash → download via specstar's GET /blobs/{id}."""

    document_id: str
    filename: str
    collection_id: str
    markdown: str
    file_id: str
    content_type: str
    size: int
    chunks: int
    cited: int
    created_by: str
    updated_at: int  # epoch ms
    status: str


class DocDeletedOut(BaseModel):
    deleted: str  # the removed document id


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def register_kb_routes(app: FastAPI, spec: SpecStar, ingestor: Ingestor) -> None:
    code_repo = CodeRepoIngestor(spec, ingestor=ingestor)

    def _collection_out(r, cited: dict[str, int]) -> CollectionOut:
        data = r.data
        assert isinstance(data, Collection)
        rid = r.info.resource_id
        doc_rm = spec.get_resource_manager(SourceDoc)
        count = 0
        size = 0
        updated = _ms(r.info.updated_time)
        for d in doc_rm.list_resources((QB["collection_id"] == rid).build()):
            sd = d.data
            assert isinstance(sd, SourceDoc)
            assert isinstance(sd.content.size, int)
            size += sd.content.size
            count += 1
            updated = max(updated, _ms(d.info.updated_time))  # ty: ignore[unresolved-attribute]
        return CollectionOut(
            resource_id=rid,
            name=data.name,
            description=data.description,
            icon=data.icon,
            cited=cited.get(rid, 0),
            doc_count=count,
            size=size,
            updated_at=updated,
            owner=r.info.created_by,
            git_url=data.git_url,
            git_branch=data.git_branch,
            git_last_sha=data.git_last_sha,
            git_last_pulled_at=data.git_last_pulled_at,
            embedder_id=data.embedder_id,
            sync_interval_hours=data.sync_interval_hours,
        )

    @app.post("/kb/collections")
    async def create_collection(body: _CollectionBody) -> CollectionOut:
        rm = spec.get_resource_manager(Collection)
        rev = rm.create(
            Collection(
                name=body.name,
                description=body.description,
                icon=body.icon,
                git_url=body.git_url,
                git_branch=body.git_branch,
                git_token=body.git_token,
                embedder_id=body.embedder_id,
                sync_interval_hours=body.sync_interval_hours,
            )
        )
        return CollectionOut(
            resource_id=rev.resource_id,
            name=body.name,
            description=body.description,
            icon=body.icon,
            cited=0,
            doc_count=0,
            size=0,
            updated_at=_ms(rev.updated_time),
            owner=rev.created_by,
            git_url=body.git_url,
            git_branch=body.git_branch,
            git_last_sha=None,
            git_last_pulled_at=None,
            embedder_id=body.embedder_id,
            sync_interval_hours=body.sync_interval_hours,
        )

    @app.get("/kb/collections")
    async def list_collections() -> list[CollectionOut]:
        rm = spec.get_resource_manager(Collection)
        cited = collection_cited(spec)
        return [_collection_out(r, cited) for r in rm.list_resources(QB.all())]  # ty: ignore[invalid-argument-type]

    @app.post("/kb/collections/{collection_id}/documents")
    async def upload_document(
        collection_id: str,
        background: BackgroundTasks,
        file: UploadFile = File(...),  # noqa: B008
    ) -> dict:
        data = await file.read()
        # store/index are synchronous (libmagic sniff, specstar I/O, embedding
        # HTTP) — never run them inline on the event loop or one upload stalls
        # every other request. Offload both to a worker thread: store is awaited
        # (the response needs the ids); index runs in the background.
        ids = await asyncio.to_thread(
            ingestor.store,
            collection_id=collection_id,
            user=_DEFAULT_USER,
            filename=file.filename or "upload",
            data=data,
        )
        for doc_id in ids:
            background.add_task(_index_in_thread, ingestor, doc_id)
        return {"document_ids": ids, "status": "indexing"}

    @app.post("/kb/collections/{collection_id}/sync")
    async def sync_collection(collection_id: str) -> SyncOut:
        """P3.0: re-clone the Collection's git_url and re-ingest. 400 when
        the Collection isn't a code Collection (no git_url), 502 when the
        clone/auth fails (typed CodeRepoSyncError), 404 when the id is unknown.

        The clone + ingest runs in a worker thread so the event loop keeps
        serving other requests during the (potentially multi-second) sync."""
        rm = spec.get_resource_manager(Collection)
        try:
            coll = rm.get(collection_id).data
        except ResourceIDNotFoundError as e:
            raise HTTPException(status_code=404, detail="collection not found") from e
        assert isinstance(coll, Collection)
        if not coll.git_url:
            raise HTTPException(
                status_code=400, detail="collection has no git_url; not a code collection"
            )
        try:
            await asyncio.to_thread(code_repo.sync, collection_id=collection_id, user=_DEFAULT_USER)
        except CodeRepoSyncError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        # Re-read so we return the freshly-recorded sha.
        refreshed = rm.get(collection_id).data
        assert isinstance(refreshed, Collection)
        return SyncOut(status="ok", git_last_sha=refreshed.git_last_sha)

    @app.post("/kb/collections/{collection_id}/reindex")
    async def reindex_collection(collection_id: str, background: BackgroundTasks) -> ReindexOut:
        # Re-chunk + re-embed every doc in the collection — the recovery path
        # after fixing the embedder (e.g. a missing model). Flip each doc back to
        # `indexing` synchronously (so the UI shows progress + polls), then run
        # the blocking rebuild off the loop, same as upload.
        rm = spec.get_resource_manager(SourceDoc)
        count = 0
        for r in rm.list_resources((QB["collection_id"] == collection_id).build()):
            doc = r.data
            assert isinstance(doc, SourceDoc)
            rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            rm.update(rid, msgspec.structs.replace(doc, status="indexing"))
            background.add_task(_index_in_thread, ingestor, rid)
            count += 1
        return ReindexOut(reindexed=count)

    @app.get("/kb/collections/{collection_id}/documents")
    async def list_documents(collection_id: str) -> list[dict]:
        rm = spec.get_resource_manager(SourceDoc)
        chrm = spec.get_resource_manager(DocChunk)
        cited = doc_cited(spec)
        out: list[dict] = []
        for r in rm.list_resources((QB["collection_id"] == collection_id).build()):
            data = r.data
            assert isinstance(data, SourceDoc)
            rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            chunks = chrm.count_resources((QB["source_doc_id"] == rid).build())
            # specstar computes the blob size on store; updated_time is the
            # current revision's timestamp (epoch ms for the wire).
            size = data.content.size
            assert isinstance(size, int)
            updated = r.info.updated_time  # ty: ignore[unresolved-attribute]
            out.append(
                {
                    "resource_id": rid,
                    "path": data.path,
                    "content_type": data.content.content_type,
                    "created_by": r.info.created_by,  # ty: ignore[unresolved-attribute]
                    "status": data.status,
                    "chunks": chunks,
                    "cited": cited.get(rid, 0),
                    "size": size,
                    "updated_at": int(updated.timestamp() * 1000),
                }
            )
        return out

    @app.get("/kb/documents")
    async def render_document(doc_id: str = Query(alias="id")) -> RenderedDoc:
        # doc_id is the opaque SourceDoc id (query param so the slash-free token
        # round-trips a URL untouched). path / collection / user come from the
        # record + meta — the id is a handle, never parsed.
        rm = spec.get_resource_manager(SourceDoc)
        try:
            rev = rm.get(doc_id)
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        doc = rev.data
        assert isinstance(doc, SourceDoc)
        user = rev.info.created_by
        raw = rm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)  # restore_binary populates the blob bytes
        text = normalize_text(raw.decode("utf-8", errors="replace"))

        def exists(rid: str) -> bool:
            try:
                rm.get(rid)
            except ResourceIDNotFoundError:
                return False
            return True

        markdown = rewrite_md_links(
            text, doc_path=doc.path, collection_id=doc.collection_id, user=user, exists=exists
        )
        chrm = spec.get_resource_manager(DocChunk)
        assert isinstance(doc.content.size, int)
        assert isinstance(doc.content.file_id, str)
        return RenderedDoc(
            document_id=doc_id,
            filename=posixpath.basename(doc.path),
            collection_id=doc.collection_id,
            markdown=markdown,
            file_id=doc.content.file_id,
            content_type=doc.content.content_type or "application/octet-stream",
            size=doc.content.size,
            chunks=chrm.count_resources((QB["source_doc_id"] == doc_id).build()),
            cited=doc_cited(spec).get(doc_id, 0),
            created_by=user,
            updated_at=_ms(rev.info.updated_time),
            status=doc.status,
        )

    @app.post("/kb/documents/reindex")
    async def reindex_document(
        background: BackgroundTasks, doc_id: str = Query(alias="id")
    ) -> ReindexOut:
        rm = spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(doc, SourceDoc)
        rm.update(doc_id, msgspec.structs.replace(doc, status="indexing"))
        background.add_task(_index_in_thread, ingestor, doc_id)
        return ReindexOut(reindexed=1)

    @app.delete("/kb/documents")
    async def delete_document(doc_id: str = Query(alias="id")) -> DocDeletedOut:
        # Cascade: chunks are derived from the doc; specstar's native delete
        # wouldn't drop them, so they'd linger in vector search. Remove the
        # chunks first, then the doc itself (hard delete — current-only data).
        rm = spec.get_resource_manager(SourceDoc)
        try:
            rm.get(doc_id)
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        chrm = spec.get_resource_manager(DocChunk)
        for r in chrm.list_resources((QB["source_doc_id"] == doc_id).build()):
            chrm.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]
        rm.permanently_delete(doc_id)
        return DocDeletedOut(deleted=doc_id)

    @app.get("/kb/documents/chunks")
    async def list_doc_chunks(doc_id: str = Query(alias="id")) -> list[dict]:
        """A document's indexed chunks + their cited counts — the chunks debug
        view behind the doc preview's toggle."""
        chrm = spec.get_resource_manager(DocChunk)
        cited = chunk_cited(spec)
        rows: list[dict] = []
        for r in chrm.list_resources((QB["source_doc_id"] == doc_id).build()):
            d = r.data
            assert isinstance(d, DocChunk)
            rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            rows.append(
                {
                    "chunk_id": rid,
                    "seq": d.seq,
                    "start": d.start,
                    "end": d.end,
                    "text": d.text,
                    "cited": cited.get(rid, 0),
                }
            )
        rows.sort(key=lambda x: x["seq"])
        return rows
