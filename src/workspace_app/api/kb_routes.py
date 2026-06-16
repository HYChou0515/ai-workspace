"""KB (knowledge-base chatbot) HTTP routes — collections, document upload/list,
and the document render endpoint. Registered onto the app by `create_app`.
"""

from __future__ import annotations

import asyncio
import posixpath
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

import msgspec
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.aggregates import Count, ForeignAggregate, Max, Sum
from specstar.types import Binary, ResourceIDNotFoundError

from ..kb.cited import chunk_cited, collection_cited, doc_cited_count, doc_cited_for_ids
from ..kb.code_repo import CodeRepoIngestor, CodeRepoSyncError
from ..kb.doc_id import canonical_path, encode_doc_id
from ..kb.ingest import Ingestor
from ..kb.links import rewrite_md_links
from ..kb.preview import preview_markdown
from ..resources.kb import Collection, DocChunk, SourceDoc

if TYPE_CHECKING:
    from ..kb.index_coordinator import IndexCoordinator
    from ..kb.wiki.coordinator import WikiMaintenanceCoordinator


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
    # Issue #50: retrieval pipeline toggles (chunk-RAG / LLM wiki).
    use_rag: bool = True
    use_wiki: bool = False


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
    # Issue #50: retrieval pipeline toggles.
    use_rag: bool = True
    use_wiki: bool = False
    # Issue #90: per-collection wiki guidance, so the editor can prefill the
    # current values. Blank ⇒ the bundled wiki prompt is used verbatim.
    wiki_maintainer_guidance: str = ""
    wiki_reader_guidance: str = ""


class SyncOut(BaseModel):
    """Result of POST /kb/collections/:id/sync — the cloned HEAD sha + status."""

    status: str
    git_last_sha: str | None = None


class WikiTreeOut(BaseModel):
    """The LLM wiki's page paths for one collection (#50 P7), for the read-only
    browser's tree. Sorted; empty when the wiki hasn't been built yet."""

    pages: list[str]


class WikiPageOut(BaseModel):
    """One wiki page's raw markdown (#50 P7). The FE renders it read-only and
    resolves [[wikilinks]] / Sources: links client-side."""

    path: str
    content: str


class WikiPageDeletedOut(BaseModel):
    """Result of DELETE /kb/collections/:id/wiki/page — the removed path."""

    deleted: str


class WikiRebuildOut(BaseModel):
    """Result of POST /kb/collections/:id/wiki/rebuild — how many sources were
    queued for re-folding into the wiki."""

    queued: int
    status: str = "rebuilding"


class WikiClearedOut(BaseModel):
    """Result of DELETE /kb/collections/:id/wiki — how many pages were wiped.
    The admin "start the wiki over from scratch" handle (rebuild is incremental;
    this clears first)."""

    cleared: int


class WikiStatusOut(BaseModel):
    """Live wiki-build progress (#50) for the FE's "Updating…" UI. The FE polls
    this while ``building`` to show source-level progress + the current activity
    (``phase``: reading / identifying / writing)."""

    building: bool
    total: int
    done: int
    current: str | None = None
    phase: str | None = None
    # Terminal failures this build — so a maintainer that wrote nothing tells
    # the operator why (e.g. hit the step limit) instead of failing silently.
    errors: int = 0
    last_error: str | None = None


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
    # Issue #39 Q11: short progress / error line ("VlmImageParser:
    # page 12/50", "ValueError: invalid JSON …"); empty when idle.
    status_detail: str = ""
    # Issue #39: blob id of a browser-displayable derivative a parser
    # handed back (PptxParser's soffice-converted PDF) — the FE iframes
    # `/blobs/{preview_file_id}` when set. "" = no preview.
    preview_file_id: str = ""


class DocDeletedOut(BaseModel):
    deleted: str  # the removed document id


class DocMovedOut(BaseModel):
    moved_from: str  # the old document id (now gone)
    moved_to: str  # the new document id (encodes the new path)


class DocumentRow(BaseModel):
    """One row in the per-collection document listing — mirrors the dict
    the FE was already consuming, now typed so the OpenAPI shape and the
    FE `KbDocument` type stay in lock-step (see `feedback_pydantic_response_models`)."""

    resource_id: str
    path: str
    content_type: str
    # The content blob id, so the FE can build `/source-doc/{id}/blobs/{file_id}`
    # to resolve a sibling-doc image ref in the doc IDE (#87) without a per-doc
    # render call.
    file_id: str
    created_by: str
    status: str
    # Issue #39 Q11 — see RenderedDoc.status_detail.
    status_detail: str = ""
    chunks: int
    cited: int
    size: int
    updated_at: int  # epoch ms


class DocumentsPage(BaseModel):
    """A page of documents inside a collection. `total` is the FULL collection
    size (not capped at `limit`), so the FE can render `n of N` + jump-to-page
    controls. `has_more` is a convenience for the common 'load more' loop —
    equivalent to `offset + len(items) < total` but spares the caller the
    arithmetic when `total` is large enough to format separately."""

    items: list[DocumentRow]
    total: int
    offset: int
    limit: int
    has_more: bool


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def register_kb_routes(
    app: FastAPI,
    spec: SpecStar,
    ingestor: Ingestor,
    wiki_coordinator: WikiMaintenanceCoordinator | None = None,
    *,
    index_coordinator: IndexCoordinator,
    get_user_id: Callable[[], str],
) -> None:
    # The current user (owner), supplied by create_app — stamped as `created_by`
    # on upload. The doc id is keyed on collection + path only (a path is one
    # shared doc whoever uploads it), so cross-ref resolution no longer depends
    # on the user matching.
    code_repo = CodeRepoIngestor(spec, ingestor=ingestor)

    def _collection_out(row, cited: dict[str, int]) -> CollectionOut:
        """Build a card from one ``exp_aggregate_by`` group row: the Collection
        (``row.resource``) plus its per-collection doc aggregates (count / total
        blob size / newest doc update) folded in by a single query — not a
        per-collection materialise-every-doc scan."""
        res = row.resource  # SearchedResource: data + info + meta
        data = res.data
        assert isinstance(data, Collection)
        rid = res.info.resource_id
        # Latest activity = the collection's own update OR its newest doc's.
        updated = _ms(res.info.updated_time)
        if row.latest_doc is not None:
            updated = max(updated, _ms(row.latest_doc))
        return CollectionOut(
            resource_id=rid,
            name=data.name,
            description=data.description,
            icon=data.icon,
            cited=cited.get(rid, 0),
            doc_count=row.doc_count,
            size=row.size_total or 0,
            updated_at=updated,
            owner=res.meta.created_by,  # resource-level creator (the original owner)
            git_url=data.git_url,
            git_branch=data.git_branch,
            git_last_sha=data.git_last_sha,
            git_last_pulled_at=data.git_last_pulled_at,
            embedder_id=data.embedder_id,
            sync_interval_hours=data.sync_interval_hours,
            use_rag=data.use_rag,
            use_wiki=data.use_wiki,
            wiki_maintainer_guidance=data.wiki_maintainer_guidance,
            wiki_reader_guidance=data.wiki_reader_guidance,
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
                use_rag=body.use_rag,
                use_wiki=body.use_wiki,
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
            use_rag=body.use_rag,
            use_wiki=body.use_wiki,
        )

    @app.get("/kb/collections")
    async def list_collections() -> list[CollectionOut]:
        coll_rm = spec.get_resource_manager(Collection)
        doc_rm = spec.get_resource_manager(SourceDoc)
        cited = collection_cited(spec)
        # ONE pass: group collections by themselves (`by=resource_id` ⇒ each row
        # carries its Collection) and fold in each collection's doc count / total
        # blob size / newest doc update via foreign aggregates — replacing the
        # old per-collection scan that materialised every doc (N+1).
        by_coll = QB["collection_id"]
        rows = coll_rm.exp_aggregate_by(  # ty: ignore[unresolved-attribute]
            by=QB.resource_id(),
            aggregates={
                "doc_count": ForeignAggregate(doc_rm, by_coll, Count()),
                "size_total": ForeignAggregate(doc_rm, by_coll, Sum(QB["content_size"])),
                "latest_doc": ForeignAggregate(doc_rm, by_coll, Max(QB.updated_time())),
            },
        )
        return [_collection_out(r, cited) for r in rows]

    @app.post("/kb/collections/{collection_id}/documents")
    async def upload_document(
        collection_id: str,
        file: UploadFile = File(...),  # noqa: B008
    ) -> dict:
        data = await file.read()
        # store is synchronous (libmagic sniff, specstar I/O) — never run it
        # inline on the event loop or one upload stalls every other request, so
        # offload to a worker thread and await it (the response needs the ids).
        # Indexing (chunk+embed) is enqueued to the durable IndexJob queue and
        # drained by the background consumer — off the request path entirely.
        ids = await asyncio.to_thread(
            ingestor.store,
            collection_id=collection_id,
            user=get_user_id(),
            filename=file.filename or "upload",
            data=data,
        )
        for doc_id in ids:
            index_coordinator.enqueue(doc_id, collection_id)
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
            await asyncio.to_thread(code_repo.sync, collection_id=collection_id, user=get_user_id())
        except CodeRepoSyncError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        # Re-read so we return the freshly-recorded sha.
        refreshed = rm.get(collection_id).data
        assert isinstance(refreshed, Collection)
        return SyncOut(status="ok", git_last_sha=refreshed.git_last_sha)

    @app.post("/kb/collections/{collection_id}/reindex")
    async def reindex_collection(collection_id: str) -> ReindexOut:
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
            index_coordinator.enqueue(rid, collection_id)
            count += 1
        return ReindexOut(reindexed=count)

    # ── LLM wiki browse (#50 P7) — read-only; the wiki is LLM-owned ──────
    @app.get("/kb/collections/{collection_id}/wiki")
    async def list_wiki_pages(collection_id: str) -> WikiTreeOut:
        from ..kb.wiki.store import WikiFileStore

        pages = await WikiFileStore(spec).ls(collection_id)
        return WikiTreeOut(pages=sorted(pages))

    @app.get("/kb/collections/{collection_id}/wiki/page")
    async def get_wiki_page(collection_id: str, path: str = Query(...)) -> WikiPageOut:
        from ..filestore.protocol import FileNotFound
        from ..kb.wiki.store import WikiFileStore

        try:
            data = await WikiFileStore(spec).read(collection_id, path)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=f"no wiki page {path!r}") from exc
        return WikiPageOut(path=path, content=data.decode("utf-8", errors="replace"))

    # ── LLM wiki edit (#D) — the wiki is now an editable filesystem ──────
    # Writes go through the SAME WikiFileStore the maintainer uses; we don't
    # CAS or reindex (the reader reads pages live via ls/read_file/grep, so an
    # edit is effective immediately). A collection is a shared drive: the same
    # path is one page, last write wins (the maintainer may later revise it).
    @app.put("/kb/collections/{collection_id}/wiki/page")
    async def write_wiki_page(
        collection_id: str, request: Request, path: str = Query(...)
    ) -> WikiPageOut:
        from ..kb.wiki.store import WikiFileStore

        data = await request.body()
        store = WikiFileStore(spec)
        with store.acting_as(get_user_id()):
            await store.write(collection_id, path, data)
        return WikiPageOut(path=path, content=data.decode("utf-8", errors="replace"))

    @app.post("/kb/collections/{collection_id}/wiki/move")
    async def move_wiki_page(
        collection_id: str,
        from_: str = Query(..., alias="from"),
        to: str = Query(...),
    ) -> WikiPageOut:
        from ..filestore.protocol import FileNotFound
        from ..kb.wiki.store import WikiFileStore

        store = WikiFileStore(spec)
        try:
            data = await store.read(collection_id, from_)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=f"no wiki page {from_!r}") from exc
        # No native subtree move — recreate at the new path then drop the old.
        with store.acting_as(get_user_id()):
            await store.write(collection_id, to, data)
            await store.delete(collection_id, from_)
        return WikiPageOut(path=to, content=data.decode("utf-8", errors="replace"))

    @app.delete("/kb/collections/{collection_id}/wiki/page")
    async def delete_wiki_page(collection_id: str, path: str = Query(...)) -> WikiPageDeletedOut:
        from ..filestore.protocol import FileNotFound
        from ..kb.wiki.store import WikiFileStore

        store = WikiFileStore(spec)
        try:
            with store.acting_as(get_user_id()):
                await store.delete(collection_id, path)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=f"no wiki page {path!r}") from exc
        return WikiPageDeletedOut(deleted=path)

    @app.get("/kb/collections/{collection_id}/wiki/status")
    async def wiki_status(collection_id: str) -> WikiStatusOut:
        if wiki_coordinator is None:
            return WikiStatusOut(building=False, total=0, done=0)
        st = wiki_coordinator.status(collection_id)
        return WikiStatusOut(
            building=st.building,
            total=st.total,
            done=st.done,
            current=st.current,
            phase=st.phase,
            errors=st.errors,
            last_error=st.last_error,
        )

    @app.post("/kb/collections/{collection_id}/wiki/rebuild")
    async def rebuild_wiki(collection_id: str) -> WikiRebuildOut:
        # Re-fold every source in the collection into its wiki (incremental
        # passes, one per source — the coordinator serialises them). The
        # maintainer updates pages in place; this is the manual "refresh the
        # wiki" path. No-op (queued=0) when the wiki path isn't enabled.
        coll_rm = spec.get_resource_manager(Collection)
        try:
            coll = coll_rm.get(collection_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if wiki_coordinator is None or not (isinstance(coll, Collection) and coll.use_wiki):
            return WikiRebuildOut(queued=0, status="disabled")
        rm = spec.get_resource_manager(SourceDoc)
        queued = 0
        for r in rm.list_resources((QB["collection_id"] == collection_id).build()):
            await wiki_coordinator.on_doc_indexed(r.info.resource_id)  # ty: ignore[unresolved-attribute]
            queued += 1
        return WikiRebuildOut(queued=queued)

    @app.delete("/kb/collections/{collection_id}/wiki")
    async def clear_wiki(collection_id: str) -> WikiClearedOut:
        # Admin handle: wipe the wiki so a rebuild starts from a clean slate
        # (rebuild itself is incremental and never deletes). No FE entry point —
        # this is the deliberate "start over" escape hatch.
        from ..kb.wiki.store import WikiFileStore

        return WikiClearedOut(cleared=await WikiFileStore(spec).clear(collection_id))

    @app.get("/kb/collections/{collection_id}/documents")
    async def list_documents(
        collection_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=500),
    ) -> DocumentsPage:
        """Paged list of a collection's documents. `collection_id` is the
        indexed filter; specstar serves the page through
        `QB[...].offset(offset).limit(limit)` so the BE never fetches the
        full collection just to slice it (see
        `feedback_specstar_indexed_queries`). `total` is computed via the
        same filter — counts only, no row materialisation."""
        rm = spec.get_resource_manager(SourceDoc)
        chrm = spec.get_resource_manager(DocChunk)
        q = QB["collection_id"] == collection_id
        total = rm.count_resources(q.build())
        # Sort by the resource's revision timestamp (newest first) BEFORE
        # paging — without an explicit sort the page would be unstable
        # across mutations (re-ingest, re-index) and the FE's "page 2"
        # could silently overlap or skip rows from "page 1". `updated_time`
        # is an info-level field specstar indexes implicitly, so no
        # `add_model(indexed_fields=...)` change is needed.
        items: list[DocumentRow] = []
        data_page = list(
            rm.list_resources(q.sort(QB.updated_time().desc()).offset(offset).limit(limit).build())
        )
        # Batched chunk-per-doc count: one query against DocChunk filtered
        # by `source_doc_id IN (this page's resource_ids)`, then bucket
        # locally. Replaces N per-doc `count_resources` calls (d530644).
        # `r.info.resource_id` (NOT `r.meta.*`) is the resource_id stored
        # on `DocChunk.source_doc_id` at ingest — the IN filter and the
        # bucket lookup must use the SAME attribute.
        ids = [r.info.resource_id for r in data_page]  # ty: ignore[unresolved-attribute]
        # Cited counts for just THIS page's docs (an indexed `document_id IN`
        # push-down), not a global group-by over the whole citation log.
        cited = doc_cited_for_ids(spec, ids)
        chunk_counts: defaultdict[str, int] = defaultdict(int)
        if ids:
            for ch in chrm.search_resources(QB["source_doc_id"].in_(ids).build()):
                # `indexed_data` is `dict | UnsetType` to ty — assert-narrow
                # (the IN query only returns rows with indexed fields).
                indexed = ch.indexed_data
                assert isinstance(indexed, dict)
                sid = indexed.get("source_doc_id")
                if isinstance(sid, str):
                    chunk_counts[sid] += 1

        for r in data_page:
            data = r.data
            assert isinstance(data, SourceDoc)
            rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            chunks = chunk_counts[rid]
            # specstar computes the blob size on store; updated_time is the
            # current revision's timestamp (epoch ms for the wire).
            size = data.content.size
            assert isinstance(size, int)
            updated = r.info.updated_time  # ty: ignore[unresolved-attribute]
            # specstar's StoredBlob.content_type is `str | UnsetType` — narrow
            # here rather than relying on a runtime sentinel further down. An
            # un-set content_type is a half-ingested doc, treat it as opaque.
            ct = data.content.content_type
            fid = data.content.file_id
            items.append(
                DocumentRow(
                    resource_id=rid,
                    path=data.path,
                    content_type=ct if isinstance(ct, str) else "application/octet-stream",
                    file_id=fid if isinstance(fid, str) else "",
                    # The OWNER is the resource-level creator (the first
                    # uploader), NOT the latest revision's author — so a shared
                    # doc that someone else overwrote still shows its original
                    # owner (`r.info.created_by` would be the last writer).
                    created_by=r.meta.created_by,  # ty: ignore[unresolved-attribute]
                    status=data.status,
                    status_detail=data.status_detail,
                    chunks=chunks,
                    cited=cited.get(rid, 0),
                    size=size,
                    updated_at=_ms(updated),
                )
            )
        return DocumentsPage(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(items) < total,
        )

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
        # The owner is the resource-level creator (first uploader), not the
        # latest revision's author.
        user = rm.get_meta(doc_id).created_by
        ct = doc.content.content_type
        raw = rm.restore_binary(doc).content.data
        assert isinstance(raw, bytes)  # restore_binary populates the blob bytes
        # Issue #39: per-type "file view" projection — text decodes,
        # structured types (json/csv/xlsx/docx) project into markdown,
        # browser-native types (image/pdf/html) ship "" and the FE
        # renders the blob itself. See kb.preview.
        text = preview_markdown(
            path=doc.path,
            content_type=ct if isinstance(ct, str) else "application/octet-stream",
            raw=raw,
        )

        def resolve(rid: str) -> str | None:
            """Map a sibling SourceDoc id to the URL to embed in the
            rendered markdown. Text → `kb://doc/{rid}` (FE turns into
            in-app nav); image → specstar `/blobs/{file_id}` (browser
            loads the bytes natively, specstar marks the
            Content-Type stored at upload)."""
            try:
                sibling = rm.get(rid).data
            except ResourceIDNotFoundError:
                return None
            assert isinstance(sibling, SourceDoc)
            ct = sibling.content.content_type
            file_id = sibling.content.file_id
            if isinstance(ct, str) and ct.startswith("image/") and isinstance(file_id, str):
                return f"/blobs/{file_id}"
            return f"kb://doc/{rid}"

        markdown = rewrite_md_links(
            text, doc_path=doc.path, collection_id=doc.collection_id, resolve=resolve
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
            cited=doc_cited_count(spec, doc_id),
            created_by=user,
            updated_at=_ms(rev.info.updated_time),
            status=doc.status,
            status_detail=doc.status_detail,
            preview_file_id=(
                doc.preview.file_id
                if doc.preview is not None and isinstance(doc.preview.file_id, str)
                else ""
            ),
        )

    @app.post("/kb/documents/reindex")
    async def reindex_document(doc_id: str = Query(alias="id")) -> ReindexOut:
        rm = spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(doc, SourceDoc)
        rm.update(doc_id, msgspec.structs.replace(doc, status="indexing"))
        index_coordinator.enqueue(doc_id, doc.collection_id)
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
        # #43: ask the wiki to un-fold this source BEFORE the row is gone — the
        # remove-pass snapshots its content now (it can't re-read a deleted doc).
        # on_doc_deleted gates on the collection's use_wiki itself; create_app
        # always wires a coordinator.
        assert wiki_coordinator is not None
        await wiki_coordinator.on_doc_deleted(doc_id)
        chrm = spec.get_resource_manager(DocChunk)
        for r in chrm.list_resources((QB["source_doc_id"] == doc_id).build()):
            chrm.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]
        rm.permanently_delete(doc_id)
        return DocDeletedOut(deleted=doc_id)

    @app.post("/kb/documents/move")
    async def move_document(doc_id: str = Query(alias="id"), to: str = Query(...)) -> DocMovedOut:
        """Rename / move a document. The doc id IS the natural key
        ``encode_doc_id(collection, path)``, so a path change re-keys: we
        re-create the doc at the new id with the SAME content (preserving the
        original ``created_by`` via ``rm.using`` — same #83 reasoning as the
        index worker), tear the old one down (chunks + wiki + row, like delete),
        and enqueue a reindex. NOTE: because the id changes, any existing ``[n]``
        citation that pointed at the old doc dangles — unavoidable while the id
        encodes the path."""
        # Canonicalise the target the same way ingest does, so a move lands at
        # the one relative form the rest of the system keys on (no "/b.md" twin).
        try:
            to = canonical_path(to)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rm = spec.get_resource_manager(SourceDoc)
        try:
            rev = rm.get(doc_id)
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        old = rev.data
        assert isinstance(old, SourceDoc)
        # Preserve the ORIGINAL owner (resource creator), not the last writer.
        creator = rm.get_meta(doc_id).created_by
        new_id = encode_doc_id(old.collection_id, to)
        if new_id == doc_id:
            return DocMovedOut(moved_from=doc_id, moved_to=doc_id)  # same path — no-op
        try:
            rm.get(new_id)
            raise HTTPException(status_code=409, detail=f"a document already exists at {to}")
        except ResourceIDNotFoundError:
            pass
        # Re-create at the new id with the same bytes; text/preview regenerate on
        # the reindex below (same as a fresh upload's store → index).
        raw = rm.restore_binary(old).content.data
        assert isinstance(raw, bytes)
        new_doc = SourceDoc(
            collection_id=old.collection_id,
            path=to,
            content=Binary(data=raw),
            status="indexing",
        )
        with rm.using(user=creator):
            rm.create(new_doc, resource_id=new_id)
        # Tear down the old doc, mirroring delete_document (wiki unfold first,
        # then its derived chunks, then the row).
        assert wiki_coordinator is not None
        await wiki_coordinator.on_doc_deleted(doc_id)
        chrm = spec.get_resource_manager(DocChunk)
        for r in chrm.list_resources((QB["source_doc_id"] == doc_id).build()):
            chrm.permanently_delete(r.info.resource_id)  # ty: ignore[unresolved-attribute]
        rm.permanently_delete(doc_id)
        index_coordinator.enqueue(new_id, old.collection_id)
        return DocMovedOut(moved_from=doc_id, moved_to=new_id)

    @app.get("/kb/documents/chunks")
    async def list_doc_chunks(doc_id: str = Query(alias="id")) -> list[dict]:
        """A document's indexed chunks + their cited counts — the chunks debug
        view behind the doc preview's toggle."""
        chrm = spec.get_resource_manager(DocChunk)
        cited = chunk_cited(spec, doc_id)
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
