"""KB (knowledge-base chatbot) HTTP routes — collections, document upload/list,
and the document render endpoint. Registered onto the app by `create_app`.
"""

from __future__ import annotations

import asyncio
import posixpath
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import msgspec
from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.aggregates import Count, ForeignAggregate, Max, Sum
from specstar.types import Binary, ResourceIDNotFoundError

from ..files.zip_download import (
    DownloadPrepared,
    prepare_zip,
    prepared_path,
    safe_zip_filename,
    stream_prepared_zip,
)
from ..kb.chunk_counts import doc_chunks_for_ids
from ..kb.cited import chunk_cited, collection_cited, doc_cited_count, doc_cited_for_ids
from ..kb.code_repo import CodeRepoIngestor, CodeRepoSyncError
from ..kb.collection_export import (
    build_collection_zip,
    build_kb_subtree_zip,
    collection_zip_filename,
)
from ..kb.collection_import import import_collection
from ..kb.doc_id import canonical_path, encode_doc_id
from ..kb.findability import (
    ProbeResult,
    ProbeSide,
    answer_from_passages,
    doc_passages_in_top_k,
    probe_findability,
)
from ..kb.index_run import IndexRunStore
from ..kb.ingest import Ingestor
from ..kb.links import rewrite_md_links
from ..kb.llm import ILlm
from ..kb.preview import preview_markdown
from ..kb.retriever import Retriever
from ..kb.upload_checks import UploadRejected
from ..perm import VERBS, Actor, Permission, Verb, Visibility, authorize
from ..resources.kb import Collection, DocChunk, SourceDoc
from .events import AgentEvent, MessageDelta, RunDone, RunError, to_sse
from .notifications import notify

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


class _PermissionBody(BaseModel):
    """#262 — body of `PUT /kb/collections/{id}/permission`: the full desired
    access state (PUT = replace). `visibility` decides whether the grant lists are
    enforced; the lists always persist, so toggling public↔restricted↔private never
    loses settings. Each grant entry is a subject token (`user:<id>` / `group:<id>`
    / `all`)."""

    visibility: str  # public | restricted | private (validated against Permission)
    read_meta: list[str] = []
    write_meta: list[str] = []
    read_content: list[str] = []
    add_content: list[str] = []
    edit_content: list[str] = []
    read_chat: list[str] = []
    converse: list[str] = []
    execute: list[str] = []
    use_terminal: list[str] = []
    change_permission: list[str] = []


class PermissionOut(BaseModel):
    """The persisted permission after a set — the FE refreshes the collection
    card from it (and re-reads the list, since visibility may now hide it)."""

    resource_id: str
    visibility: str
    notified: list[str]  # users newly granted access who got a `share` notification


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
    # #88: chunk-based token estimate — SUM of each ready doc's token_count (a
    # CJK-aware estimate of the EXTRACTED text). Replaces the FE's old raw-blob
    # bytes/4 guess, which was wildly wrong for binary formats.
    tokens: int
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
    # #105: the per-collection quality rubric, so the editor can prefill it.
    # Blank ⇒ the collection is not scored.
    quality_rubric: str = ""
    # #328: the per-collection parser guidance (appended to prompt-driven parsers),
    # so the findability modal can prefill the editor. Blank ⇒ no steering.
    parser_guidance: str = ""


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


class _ProbeBody(BaseModel):
    """#328 findability probe request. ``guidance`` is the modal's CANDIDATE
    parser_guidance: ``None`` ⇒ report current ranks only; a string (incl. ``""``)
    ⇒ also re-parse this doc under it (dry-run) and report the after-ranks."""

    doc_id: str
    question: str
    guidance: str | None = None
    # #356: the modal's slider — the top-k cutoff that flags which passages a user
    # sees. The service always ranks deeper (max(DEFAULT_DEPTH, k)) so buried ranks
    # still show.
    k: int = 5


class _AnswerBody(BaseModel):
    """#356 "Try answer": stream the answer a question gets from ONLY this doc's
    passages that land in the top-``k``. ``guidance is None`` ⇒ the current indexed
    chunks (the Before box); a string ⇒ the candidate re-parse (the After box)."""

    doc_id: str
    question: str
    k: int = 5
    guidance: str | None = None


class _DocGuidanceBody(BaseModel):
    """#356: the per-doc parser-guidance override to persist. ``""`` clears it."""

    guidance: str = ""


class DocGuidanceOut(BaseModel):
    """Echo of the persisted per-doc override (the FE re-syncs its editor)."""

    parser_guidance_override: str


class ProbePassageOut(BaseModel):
    rank: int
    in_top_k: bool
    text: str
    location: str


class ProbeSideOut(BaseModel):
    passages: list[ProbePassageOut]
    best_rank: int | None = None


class ProbeResultOut(BaseModel):
    """#328 findability probe response — where this doc's content ranks for the
    question now (``before``) and under a candidate guidance (``after``, null when
    none was given)."""

    top_k: int
    depth: int
    before: ProbeSideOut
    after: ProbeSideOut | None = None


def _probe_side_out(side: ProbeSide) -> ProbeSideOut:
    return ProbeSideOut(
        passages=[
            ProbePassageOut(rank=p.rank, in_top_k=p.in_top_k, text=p.text, location=p.location)
            for p in side.passages
        ],
        best_rank=side.best_rank,
    )


def _probe_result_out(result: ProbeResult) -> ProbeResultOut:
    return ProbeResultOut(
        top_k=result.top_k,
        depth=result.depth,
        before=_probe_side_out(result.before),
        after=_probe_side_out(result.after) if result.after is not None else None,
    )


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
    # #105: the AI quality verdict shown when the doc is opened — the holistic
    # 0–100 `quality_score` (null = un-scored), the `quality_rationale` ("why
    # good/bad"), and the per-dimension `quality_breakdown` (keys named by the
    # collection's rubric). Display-only.
    quality_score: int | None = None
    quality_rationale: str = ""
    quality_breakdown: dict[str, float] = {}
    # #356: this doc's per-doc parser-guidance override (the escape hatch the
    # Tune-parsing modal writes). "" ⇒ the doc inherits the collection guidance;
    # the modal prefills its editor from this (else the collection's).
    parser_guidance_override: str = ""


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
    # #248: real fan-out progress for the FE bar — units (e.g. PDF pages) done /
    # total. 0/0 for a doc with no in-flight fan-out (small / single-job / ready),
    # which the FE reads as "no unit bar". Monotonic while indexing (see IndexRun).
    units_done: int = 0
    units_total: int = 0
    # #105: the AI's quality grade (0–100) for this doc as a knowledge source, or
    # null when un-scored (no rubric / not yet judged) — the FE draws a quality
    # badge from it and can sort the list by it. The short `quality_rationale`
    # rides the row too so the doc IDE's status bar can show "why" without a
    # per-doc render call; the (larger) per-dimension breakdown stays on the
    # `render_document` detail.
    quality_score: int | None = None
    quality_rationale: str = ""
    # #356: this doc's per-doc parser-guidance override (the Tune-parsing escape
    # hatch). "" ⇒ inherits the collection guidance. Rides the row so the modal
    # prefills its editor without a separate render call.
    parser_guidance_override: str = ""


class CollectionImported(BaseModel):
    """Issue #101: result of importing an exported zip. `collection_id` is the
    target — a freshly created one (new-collection import) or the existing one
    merged into. Documents land as `status="indexing"` and re-index off-request,
    so the FE refetches the collection to watch them flip to `ready`."""

    collection_id: str
    document_ids: list[str]
    status: str = "indexing"


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


class UploadAccepted(BaseModel):
    """The upload endpoint's success shape: the SourceDoc ids that now
    need indexing (new or changed bytes) and the doc status. Indexing runs
    in the background, so ``status`` is always ``"indexing"`` here."""

    document_ids: list[str]
    status: str


class UploadCheckHintOut(BaseModel):
    """One browser-runnable upload-check descriptor (#325). The FE reads a
    picked file's leading bytes and pre-blocks it when its extension is in
    ``extensions`` AND those bytes match any prefix in ``forbid_magic_hex``,
    showing ``message_key``'s localised copy — the same check the server
    re-runs authoritatively, so the two never disagree."""

    id: str
    extensions: list[str]
    forbid_magic_hex: list[str]
    message_key: str


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _granted_user_ids(perm: Permission | None) -> set[str]:
    """The set of concrete user ids that appear in ANY of a permission's grant
    lists (the `group:` namespace + the `all` wildcard are not addressable
    recipients, so they're skipped). Used to diff old→new for share notifications."""
    if perm is None:
        return set()
    prefix = "user:"
    return {
        subj[len(prefix) :]
        for verb in VERBS
        for subj in perm.grants(verb)
        if subj.startswith(prefix)
    }


def register_kb_routes(
    app: FastAPI | APIRouter,
    spec: SpecStar,
    ingestor: Ingestor,
    wiki_coordinator: WikiMaintenanceCoordinator | None = None,
    *,
    index_coordinator: IndexCoordinator,
    retriever: Retriever,
    get_user_id: Callable[[], str],
    superusers: frozenset[str] = frozenset(),
    answer_llm: ILlm,
    answer_system_prompt: str = "",
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
            tokens=row.token_total or 0,
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
            quality_rubric=data.quality_rubric,
            parser_guidance=data.parser_guidance,
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
            tokens=0,
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

    def _can_read_meta(row) -> bool:
        """#262 — a collection stays in the list only if the caller may see it
        (`read_meta`). `permission is None` ≡ public (back-compat)."""
        data = row.resource.data
        assert isinstance(data, Collection)
        return authorize(
            Actor.human(get_user_id()),
            "read_meta",
            data.permission,
            created_by=row.resource.meta.created_by,
            superusers=superusers,
        )

    def _authorize_collection(collection_id: str, verb: Verb) -> tuple[Collection, str]:
        """#262 — gate a hand-written collection route. Loads the collection
        (these routes use `rm.get`, which is NOT access-scoped), then sequences
        the two checks the auto-CRUD layer composes: `read_meta` first — an actor
        who can't even see the collection gets a uniform 404 (no existence leak) —
        then `verb` itself → 403. Returns the collection + its owner for the
        handler. `permission is None` ≡ public (back-compat)."""
        rm = spec.get_resource_manager(Collection)
        try:
            coll = rm.get(collection_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail="collection not found") from exc
        assert isinstance(coll, Collection)
        created_by = rm.get_meta(collection_id).created_by
        actor = Actor.human(get_user_id())
        if not authorize(
            actor, "read_meta", coll.permission, created_by=created_by, superusers=superusers
        ):
            raise HTTPException(status_code=404, detail="collection not found")
        if not authorize(
            actor, verb, coll.permission, created_by=created_by, superusers=superusers
        ):
            raise HTTPException(status_code=403, detail=f"not authorized to {verb}")
        return coll, created_by

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
                # #88: chunk-based token estimate summed in the SAME pass as size.
                "token_total": ForeignAggregate(doc_rm, by_coll, Sum(QB["token_count"])),
                "latest_doc": ForeignAggregate(doc_rm, by_coll, Max(QB.updated_time())),
            },
        )
        rows = [r for r in rows if _can_read_meta(r)]
        return [_collection_out(r, cited) for r in rows]

    @app.put("/kb/collections/{collection_id}/permission")
    async def set_collection_permission(collection_id: str, body: _PermissionBody) -> PermissionOut:
        """#262 — set a collection's access control (the FE share UI's backend).
        Only the owner / a superuser / a `change_permission` grantee may call it
        (404 if you can't see it, 403 if you can't change it). This is the ONLY
        path that may rewire access control — the generic update/PATCH checker
        rejects a `permission` change without `change_permission`. Newly-granted
        users get a `share` notification (mirrors the chat share)."""
        if body.visibility not in ("public", "restricted", "private"):
            raise HTTPException(status_code=400, detail=f"invalid visibility {body.visibility!r}")
        visibility = cast(Visibility, body.visibility)  # narrowed by the guard above
        rm = spec.get_resource_manager(Collection)
        coll, created_by = _authorize_collection(collection_id, "change_permission")
        new_perm = Permission(
            visibility=visibility,
            read_meta=body.read_meta,
            write_meta=body.write_meta,
            read_content=body.read_content,
            add_content=body.add_content,
            edit_content=body.edit_content,
            read_chat=body.read_chat,
            converse=body.converse,
            execute=body.execute,
            use_terminal=body.use_terminal,
            change_permission=body.change_permission,
        )
        # Persist AS THE OWNER: the per-verb write checker (perm.checker) also
        # gates Collection updates on `write_meta`, which a change_permission-only
        # delegate need not hold — and `change_permission` was just verified here.
        with rm.using(created_by):
            rm.update(collection_id, msgspec.structs.replace(coll, permission=new_perm))
        # Notify users NEWLY granted any access (the actor + already-granted users
        # are not re-notified). Mirrors kb_chat_routes.share_chat.
        me = get_user_id()
        notified = sorted(_granted_user_ids(new_perm) - _granted_user_ids(coll.permission) - {me})
        for uid in notified:
            notify(
                spec,
                recipient=uid,
                kind="share",
                title=f'Shared a collection: "{coll.name}"',
                link=f"/kb/collections/{collection_id}",
                actor=me,
            )
        return PermissionOut(
            resource_id=collection_id, visibility=new_perm.visibility, notified=notified
        )

    @app.get("/kb/upload-checks")
    async def list_upload_checks() -> list[UploadCheckHintOut]:
        """#325: the browser-runnable upload-check descriptors. The FE
        fetches these and pre-blocks the common case (an encrypted Office
        file) before upload — server-only checks (PDF) aren't listed."""
        return [
            UploadCheckHintOut(
                id=hint.id,
                extensions=list(hint.extensions),
                forbid_magic_hex=list(hint.forbid_magic_hex),
                message_key=hint.message_key,
            )
            for hint in ingestor.upload_check_hints()
        ]

    @app.post("/kb/findability/probe")
    async def findability_probe(body: _ProbeBody) -> ProbeResultOut:
        """#328: rank a doc's content for a representative question (``before``)
        and — when a candidate ``guidance`` is given — a non-persisted re-parse of
        this one doc under it (``after``). Read-only: the modal writes nothing; the
        operator persists a good guidance separately via PATCH /collection. The
        re-parse re-runs the parser (VLM), so this is offloaded off the loop."""
        try:
            spec.get_resource_manager(SourceDoc).get(body.doc_id)
        except ResourceIDNotFoundError:
            raise HTTPException(status_code=404, detail="document not found") from None
        result = await asyncio.to_thread(
            probe_findability,
            spec,
            retriever,
            ingestor,
            doc_id=body.doc_id,
            question=body.question,
            guidance=body.guidance,
            k=max(1, min(body.k, 100)),
        )
        return _probe_result_out(result)

    @app.post("/kb/findability/answer")
    async def findability_answer(body: _AnswerBody) -> StreamingResponse:
        """#356 "Try answer": stream the answer ``body.question`` gets from ONLY
        ``body.doc_id``'s passages within the top-``k`` of the real ranked list —
        the kb_chat model answering a FIXED context (no self-search), so the
        operator sees whether their doc actually answers the question. ``guidance``
        re-parses the doc first (the After box). Retrieve / dry-run / LLM stream are
        all blocking, so they run in a worker thread feeding an async SSE queue."""
        try:
            spec.get_resource_manager(SourceDoc).get(body.doc_id)
        except ResourceIDNotFoundError:
            raise HTTPException(status_code=404, detail="document not found") from None
        k = max(1, min(body.k, 100))

        async def gen() -> AsyncIterator[str]:
            queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def emit(event: AgentEvent) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, event)

            def work() -> None:
                try:
                    passages = doc_passages_in_top_k(
                        spec,
                        retriever,
                        ingestor,
                        doc_id=body.doc_id,
                        question=body.question,
                        k=k,
                        guidance=body.guidance,
                    )
                    answer_from_passages(
                        answer_llm,
                        system_prompt=answer_system_prompt,
                        question=body.question,
                        passages=passages,
                        on_chunk=lambda text, reasoning: emit(
                            MessageDelta(text=text, reasoning=reasoning)
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 — failures belong IN the stream
                    emit(RunError(message=str(exc)))
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            task = asyncio.create_task(asyncio.to_thread(work))
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield to_sse(event)
                yield to_sse(RunDone())
            finally:
                await task

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/kb/collections/{collection_id}/documents")
    async def upload_document(
        collection_id: str,
        file: UploadFile = File(...),  # noqa: B008
    ) -> UploadAccepted:
        _authorize_collection(collection_id, "add_content")  # #262
        data = await file.read()
        # store is synchronous (libmagic sniff, specstar I/O) — never run it
        # inline on the event loop or one upload stalls every other request, so
        # offload to a worker thread and await it (the response needs the ids).
        # Indexing (chunk+embed) is enqueued to the durable IndexJob queue and
        # drained by the background consumer — off the request path entirely.
        try:
            ids = await asyncio.to_thread(
                ingestor.store,
                collection_id=collection_id,
                user=get_user_id(),
                filename=file.filename or "upload",
                data=data,
            )
        except UploadRejected as rej:
            # #325: a custom check refused this file (encrypted/unreadable).
            # 422 with the structured verdict so the FE shows actionable copy
            # ("decrypt and re-upload"); nothing was stored.
            raise HTTPException(
                status_code=422,
                detail={
                    "check_id": rej.rejection.check_id,
                    "reason_code": rej.rejection.reason_code,
                    "message_key": rej.rejection.message_key,
                },
            ) from rej
        for doc_id in ids:
            index_coordinator.enqueue(doc_id, collection_id)
        return UploadAccepted(document_ids=ids, status="indexing")

    # ── Issue #101: collection export (two-step prepare → stream) ──────────
    @app.post("/kb/collections/{collection_id}/download/prepare")
    async def prepare_collection_download(collection_id: str) -> DownloadPrepared:
        """Build the export zip to a temp file and hand back a download id. The
        zip build (restore every blob + compress) is blocking, so it runs off
        the event loop. Stale temp files from abandoned prepares are reaped here.
        404 when the collection is unknown."""
        rm = spec.get_resource_manager(Collection)
        try:
            coll = rm.get(collection_id).data
        except ResourceIDNotFoundError as e:
            raise HTTPException(status_code=404, detail="collection not found") from e
        assert isinstance(coll, Collection)
        download_id, size = await prepare_zip(
            lambda out: build_collection_zip(spec, collection_id, out)
        )
        return DownloadPrepared(
            download_id=download_id,
            filename=collection_zip_filename(coll.name),
            size=size,
        )

    @app.get("/kb/collections/{collection_id}/download/{download_id}")
    async def stream_collection_download(collection_id: str, download_id: str) -> FileResponse:
        """Stream a prepared export zip once, then delete it. 404 when the id is
        malformed / already streamed / reaped, or the collection is gone."""
        path = prepared_path(download_id)
        if path is None:
            raise HTTPException(status_code=404, detail="download not found")
        rm = spec.get_resource_manager(Collection)
        try:
            coll = rm.get(collection_id).data
        except ResourceIDNotFoundError as e:
            raise HTTPException(status_code=404, detail="collection not found") from e
        assert isinstance(coll, Collection)
        return stream_prepared_zip(path, collection_zip_filename(coll.name))

    # ── Issue #247: raw folder/root download (no manifest) ────────────────
    @app.post("/kb/collections/{collection_id}/folder-download/prepare")
    async def prepare_folder_download(collection_id: str, prefix: str = "") -> DownloadPrepared:
        """Build a plain ZIP of the raw bytes of every doc under `prefix`
        (`prefix=""` = the whole collection), entries re-rooted at the folder.
        404 when the collection is unknown."""
        rm = spec.get_resource_manager(Collection)
        try:
            coll = rm.get(collection_id).data
        except ResourceIDNotFoundError as e:
            raise HTTPException(status_code=404, detail="collection not found") from e
        assert isinstance(coll, Collection)
        folder = prefix.strip("/").rsplit("/", 1)[-1] or coll.name
        download_id, size = await prepare_zip(
            lambda out: build_kb_subtree_zip(spec, collection_id, prefix, out)
        )
        return DownloadPrepared(
            download_id=download_id,
            filename=safe_zip_filename(folder),
            size=size,
        )

    @app.get("/kb/collections/{collection_id}/folder-download/{download_id}")
    async def stream_folder_download(
        collection_id: str, download_id: str, prefix: str = ""
    ) -> FileResponse:
        """Stream a prepared folder ZIP once, then delete it. 404 when the id is
        malformed / already streamed / reaped, or the collection is gone."""
        path = prepared_path(download_id)
        if path is None:
            raise HTTPException(status_code=404, detail="download not found")
        rm = spec.get_resource_manager(Collection)
        try:
            coll = rm.get(collection_id).data
        except ResourceIDNotFoundError as e:
            raise HTTPException(status_code=404, detail="collection not found") from e
        assert isinstance(coll, Collection)
        folder = prefix.strip("/").rsplit("/", 1)[-1] or coll.name
        return stream_prepared_zip(path, safe_zip_filename(folder))

    @app.post("/kb/collections/import")
    async def import_new_collection(
        file: UploadFile = File(...),  # noqa: B008
    ) -> CollectionImported:
        """Import an exported zip as a NEW collection. The manifest restores the
        collection settings + context cards; a manifest-less zip degrades to a
        plain-files import named after the upload. Blocking work runs off-loop."""
        data = await file.read()
        fallback = Path(file.filename or "import").stem or "imported"
        result = await asyncio.to_thread(
            import_collection,
            spec=spec,
            ingestor=ingestor,
            index_coordinator=index_coordinator,
            zip_data=data,
            user=get_user_id(),
            fallback_name=fallback,
        )
        return CollectionImported(
            collection_id=result.collection_id,
            document_ids=result.document_ids,
            status=result.status,
        )

    @app.post("/kb/collections/{collection_id}/import")
    async def import_into_collection(
        collection_id: str,
        file: UploadFile = File(...),  # noqa: B008
        mode: str = Query("overwrite"),
    ) -> CollectionImported:
        """Merge an exported zip INTO an existing collection. `mode` decides a
        path collision: `overwrite` (last-write-wins) or `skip`. 404 unknown
        collection, 400 bad mode."""
        if mode not in ("overwrite", "skip"):
            raise HTTPException(status_code=400, detail="mode must be 'overwrite' or 'skip'")
        _authorize_collection(collection_id, "add_content")  # #262 (404 unknown / hidden)
        data = await file.read()
        result = await asyncio.to_thread(
            import_collection,
            spec=spec,
            ingestor=ingestor,
            index_coordinator=index_coordinator,
            zip_data=data,
            user=get_user_id(),
            fallback_name="imported",
            collection_id=collection_id,
            mode=mode,
        )
        return CollectionImported(
            collection_id=result.collection_id,
            document_ids=result.document_ids,
            status=result.status,
        )

    @app.post("/kb/collections/{collection_id}/sync")
    async def sync_collection(collection_id: str) -> SyncOut:
        """P3.0: re-clone the Collection's git_url and re-ingest. 400 when
        the Collection isn't a code Collection (no git_url), 502 when the
        clone/auth fails (typed CodeRepoSyncError), 404 when the id is unknown.

        The clone + ingest runs in a worker thread so the event loop keeps
        serving other requests during the (potentially multi-second) sync."""
        rm = spec.get_resource_manager(Collection)
        coll, _ = _authorize_collection(collection_id, "edit_content")  # #262
        if not coll.git_url:
            raise HTTPException(
                status_code=400, detail="collection has no git_url; not a code collection"
            )
        try:
            await asyncio.to_thread(code_repo.sync, collection_id=collection_id, user=get_user_id())
        except CodeRepoSyncError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        # #281 A0: code_repo.sync stores + indexes each file synchronously, which
        # BYPASSES the IndexCoordinator — so on_doc_indexed never fires and the
        # code-wiki would never build on the main flow. Trigger the build
        # explicitly now that sync has returned (all docs are indexed by then, so
        # one build covers them all — no batch-join needed). No-op for non-wiki /
        # non-code collections. create_app always wires a coordinator (same as the
        # delete route's assert), so this never no-ops in practice.
        assert wiki_coordinator is not None
        await wiki_coordinator.trigger_code_build(collection_id, requested_by=get_user_id())
        # Re-read so we return the freshly-recorded sha.
        refreshed = rm.get(collection_id).data
        assert isinstance(refreshed, Collection)
        return SyncOut(status="ok", git_last_sha=refreshed.git_last_sha)

    @app.post("/kb/collections/{collection_id}/reindex")
    async def reindex_collection(
        collection_id: str, only: str | None = Query(default=None)
    ) -> ReindexOut:
        # Re-chunk + re-embed the collection — the recovery path after fixing the
        # embedder (e.g. a missing model). Flip each doc back to `indexing`
        # synchronously (so the UI shows progress + polls), then run the blocking
        # rebuild off the loop, same as upload.
        #
        # `?only=failed` (issue #223) re-queues ONLY docs stuck in `error`, so a
        # transient outage can be recovered without re-embedding every doc that
        # already indexed. `only` is a closed vocabulary: anything else is a 400
        # rather than a silent fall-through to "re-index everything".
        if only is not None and only != "failed":
            raise HTTPException(status_code=400, detail=f"unknown only={only!r}")
        _authorize_collection(collection_id, "edit_content")  # #262
        rm = spec.get_resource_manager(SourceDoc)
        count = 0
        for r in rm.list_resources((QB["collection_id"] == collection_id).build()):
            doc = r.data
            assert isinstance(doc, SourceDoc)
            if only == "failed" and doc.status != "error":
                continue
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

        _authorize_collection(collection_id, "edit_content")  # #262
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
        # #281: a code collection (git_url) rebuilds its whole wiki hierarchically
        # in ONE coalesced build — not a per-source fold loop, which builds nothing
        # when there are no docs yet and is wasteful when there are (the code build
        # reads every source regardless). This same endpoint backs the FE's
        # use_wiki toggle-on for a code collection.
        if coll.git_url:
            await wiki_coordinator.trigger_code_build(collection_id, requested_by=get_user_id())
            return WikiRebuildOut(queued=1)
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
        sort: str = Query("recent"),
    ) -> DocumentsPage:
        """Paged list of a collection's documents. `collection_id` is the
        indexed filter; specstar serves the page through
        `QB[...].offset(offset).limit(limit)` so the BE never fetches the
        full collection just to slice it (see
        `feedback_specstar_indexed_queries`). `total` is computed via the
        same filter — counts only, no row materialisation.

        `sort` (#105): `recent` (default) = newest first; `quality` = worst
        quality first (the indexed `quality_score` ascending) so the FE's "sort
        by quality" surfaces the docs that drag retrieval down. `resource_id` is
        the total-order tiebreak in both."""
        rm = spec.get_resource_manager(SourceDoc)
        q = QB["collection_id"] == collection_id
        total = rm.count_resources(q.build())
        # Sort by IMMUTABLE keys BEFORE paging — `created_time` (the resource's
        # birth stamp, never moves across revisions) newest-first, with
        # `resource_id` as a total-order tiebreak for docs born in the same ms
        # (a bulk folder upload). NOT `updated_time`: re-ingest / re-index bumps
        # it, so a doc indexing *between* the FE's offset fetches would jump to
        # the front and slide the window, double-counting one row and dropping
        # another in the fetch-all loop (#184). Both are meta-level fields
        # specstar indexes implicitly — no `add_model(indexed_fields=...)`.
        # `quality` sort intentionally pages on the (mutable) score — a re-score
        # CAN shift the window, the inherent cost of sorting by a live signal.
        ordering = (
            q.sort(QB["quality_score"].asc(), QB.resource_id().asc())
            if sort == "quality"
            else q.sort(QB.created_time().desc(), QB.resource_id().asc())
        )
        items: list[DocumentRow] = []
        data_page = list(rm.list_resources(ordering.offset(offset).limit(limit).build()))
        # `r.info.resource_id` (NOT `r.meta.*`) is the resource_id stored on
        # `DocChunk.source_doc_id` / on a `CitationEvent.document_id` at ingest —
        # the IN filters keyed below must use the SAME attribute, and the lookups
        # further down read back by it.
        ids = [r.info.resource_id for r in data_page]  # ty: ignore[unresolved-attribute]
        # Per-page counts for THIS page's docs only. Both are scoped, indexed
        # `... IN (ids)` `Count` GROUP BY push-downs returning {doc_id: n}: no
        # global group-by over the whole log, and — crucially for #103 — no
        # chunk-body materialisation just to tally (the old per-page
        # `DocChunk.search_resources` loop streamed every chunk's text + two
        # embedding vectors into Python only to add 1).
        cited = doc_cited_for_ids(spec, ids)
        chunk_counts = doc_chunks_for_ids(spec, ids)

        # #248: unit progress for the docs still fanning out on this page. The run
        # is keyed by doc id and only exists for an in-flight (or just-finished)
        # fan-out, so this is a bounded per-indexing-doc lookup, never a scan.
        runs = IndexRunStore(spec)
        for r in data_page:
            data = r.data
            assert isinstance(data, SourceDoc)
            rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            chunks = chunk_counts.get(rid, 0)
            run = runs.get(rid) if data.status == "indexing" else None
            units_done = run.units_done if run is not None else 0
            units_total = run.units_total if run is not None else 0
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
                    units_done=units_done,
                    units_total=units_total,
                    quality_score=data.quality_score,
                    quality_rationale=data.quality_rationale,
                    parser_guidance_override=data.parser_guidance_override,
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
        # #114: browser-native types (image/pdf) project to "" — the FE shows the
        # blob itself. But for an image VLM-parsed at ingest, the extracted text
        # on `doc.text` is exactly what the retriever cited; surface it below the
        # image so the viewer matches what the chat saw, instead of a blank body.
        if not text and doc.text:
            text = doc.text

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
            quality_score=doc.quality_score,
            quality_rationale=doc.quality_rationale,
            quality_breakdown={
                k: float(v) for k, v in doc.quality_breakdown.items() if isinstance(v, (int, float))
            },
            parser_guidance_override=doc.parser_guidance_override,
        )

    @app.post("/kb/documents/guidance")
    async def set_document_guidance(
        body: _DocGuidanceBody, doc_id: str = Query(alias="id")
    ) -> DocGuidanceOut:
        """#356: write a doc's per-doc ``parser_guidance_override`` (the escape
        hatch). A non-empty value REPLACES the collection guidance for this doc at
        index time; ``""`` clears it (the doc re-inherits the collection's). Persist
        only — like "Apply to collection", it takes effect on the next re-index."""
        rm = spec.get_resource_manager(SourceDoc)
        try:
            doc = rm.get(doc_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail="document not found") from exc
        assert isinstance(doc, SourceDoc)
        rm.update(doc_id, msgspec.structs.replace(doc, parser_guidance_override=body.guidance))
        return DocGuidanceOut(parser_guidance_override=body.guidance)

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
