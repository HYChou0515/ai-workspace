"""Workflow run routes (#54).

The profile/workflow catalog the new-chat picker reads, plus a run's whole lifecycle
over its WORKFLOW CHAT: launch (with optional headless input-file upload), poll /
list / stream, the pre-flight preview, cancel, the human-gate decision, and the
conversational steer + confirm. The routes only validate + translate; the run
mechanics live behind the ``WorkflowOrchestrator`` and the ``WorkflowExecutor``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError
from starlette.datastructures import UploadFile

from ..files import WorkspaceFiles
from ..resources import Conversation
from ..workflow.event_backfill import backfill_trigger_lag, find_trigger_lag
from ..workflow.event_dispatch import EventTriggerDispatcher
from ..workflow.handle import WorkflowHandle
from ..workflow.inputs import resolve_inputs
from ..workflow.orchestrator import (
    ActiveRunExists,
    NotAwaitingDecision,
    NotAwaitingSteer,
    WorkflowOrchestrator,
)
from ..workflow.preflight import can_run as _preflight_can_run
from ..workflow.run import WorkflowRun
from .activity import ActivityLog
from .events import FileChanged
from .item_conversation_perm import item_conversation_mirror
from .locator import ItemLocator
from .schemas import (
    _DecisionBody,
    _PhaseOut,
    _PreflightCheckOut,
    _PreflightPreviewOut,
    _SteerAck,
    _SteerBody,
    _SteerConfirmBody,
    _SteerConfirmOut,
)
from .timeutil import now_ms
from .turns import ChatTurnEngine
from .workflow_exec import WorkflowExecutor

logger = logging.getLogger(__name__)


async def _staged_run_uploads(
    request: Request, workflow_id: str
) -> tuple[str, list[tuple[str, bytes]]]:
    """#197: parse a workflow-run trigger's optional ``multipart/form-data`` body into
    a (resolved ``workflow_id``, staged ``[(workspace_path, bytes)]``) pair so the caller
    can write the files THEN start the run.

    Each ``file`` part's filename IS its workspace path (sub-dirs allowed); ``canonical_path``
    resolves ``.``/``..`` and a path escaping the root raises 400. ALL parts are validated
    before the caller writes any, so one bad upload aborts the whole trigger (nothing is
    half-written, no run starts). The query ``workflow_id`` wins; absent it a ``workflow_id``
    form field is honoured. With no multipart body this is a no-op — the plain trigger the
    FE makes (query param only, empty body) is left completely untouched."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return workflow_id, []
    from ..kb.doc_id import canonical_path

    form = await request.form()
    if not workflow_id:
        field = form.get("workflow_id")
        if isinstance(field, str):
            workflow_id = field
    staged: list[tuple[str, bytes]] = []
    for part in form.getlist("file"):
        # A `file` field carrying a plain string (no filename) is not an upload — skip it.
        if not isinstance(part, UploadFile) or not part.filename:
            continue
        try:
            rel = canonical_path(part.filename)
        except ValueError as exc:
            logger.warning("workflow_routes: upload path escapes workspace: %r", part.filename)
            raise HTTPException(
                status_code=400,
                detail=f"upload path escapes the workspace: {part.filename!r}",
            ) from exc
        staged.append(("/" + rel, await part.read()))
    return workflow_id, staged


def register_workflow_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    files: WorkspaceFiles,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
    activity: ActivityLog,
    turn_engine: ChatTurnEngine,
    workflow_orchestrator: WorkflowOrchestrator,
    workflow_executor: WorkflowExecutor,
    event_dispatcher: EventTriggerDispatcher,
) -> None:
    """Mount the workflow profile + run routes onto ``app``."""
    conv_rm = spec.get_resource_manager(Conversation)

    async def _item_entities_of(investigation_id: str):
        """A ``type_name -> current parsed records`` resolver over the item's entity store —
        the read side the P11 backfill walks to find lag. An undeclared type yields no records
        (a trigger can name a type this item doesn't ship)."""
        from ..entity.catalog import discover_catalog
        from ..entity.store import EntityStore

        catalog, _diags = await discover_catalog(files, investigation_id)
        store = EntityStore(files, investigation_id, catalog)

        async def go(type_name: str):
            if type_name not in catalog:
                return []
            return (await store.query(type_name)).entities

        return go

    async def _workflow_manifest_or_404(slug: str, item_id: str, workflow_id: str = ""):
        """Validate the item belongs to the slug AND carries the requested workflow —
        a package workflow on its profile (manual §4) OR a WORKSPACE-authored
        ``.workflows/<id>.json`` (§22 P4, shadowing same-id package). Returns
        (investigation_id, profile, manifest)."""
        from ..apps.profiles import load_profile_workflow
        from ..workflow.workspace_store import load_workspace_workflow

        investigation_id = locator.require_access(slug, item_id, "read_meta")
        profile = locator.profile_of(investigation_id)
        manifest = load_profile_workflow(slug, profile, workflow_id)
        if manifest is None and workflow_id:  # fall through to a workspace-authored one
            res = await load_workspace_workflow(files, investigation_id, workflow_id)
            manifest = res[1] if res is not None else None
        if manifest is None:
            raise HTTPException(
                status_code=422,
                detail=f"profile {profile!r} of app {slug!r} has no workflow {workflow_id!r}",
            )
        return investigation_id, profile, manifest

    @app.get("/a/{slug}/items/{item_id}/workflows")
    async def list_item_workflows(slug: str, item_id: str) -> list[dict]:
        """#323 P4 (manual §22): the workflows a user co-created in THIS item's
        ``.workflows/`` (id + title + phase skeleton), for the Workflows panel + the Run
        picker. Each manifest as builtins (matching ``/profiles``); malformed defs are
        skipped (``save_workflow`` is the loud guard)."""
        from ..workflow.workspace_store import workspace_workflow_metas

        investigation_id = locator.require_access(slug, item_id, "read_meta")
        metas = await workspace_workflow_metas(files, investigation_id)
        return [msgspec.to_builtins(m) for m in metas]

    @app.get("/a/{slug}/profiles")
    async def list_app_profiles(slug: str) -> list[dict]:
        """#100 (manual §4 & §14): the App's profiles, each with its list of workflow
        MANIFESTS so the FE's new-chat picker can offer every workflow type. Also keeps
        the legacy singular ``workflow`` field for back-compat."""
        from ..apps.catalog import discover_app_slugs
        from ..apps.profiles import list_profiles, load_profile, normalize_workflows

        if slug not in discover_app_slugs():
            raise HTTPException(status_code=404, detail=f"unknown app: {slug!r}")
        out: list[dict] = []
        for name in list_profiles(slug):
            p = load_profile(slug, name)
            workflows = normalize_workflows(p)
            out.append(
                {
                    "name": name,
                    "title": p.title or name,
                    "description": p.description,
                    "has_workflow": bool(workflows),
                    "workflow": msgspec.to_builtins(p.workflow) if p.workflow else None,
                    "workflows": [msgspec.to_builtins(wf) for wf in workflows],
                }
            )
        return out

    @app.post("/a/{slug}/items/{item_id}/run", status_code=status.HTTP_202_ACCEPTED)
    async def run_workflow_item(
        slug: str,
        item_id: str,
        request: Request,
        workflow_id: str = Query(""),
        chat_id: str = Query(""),
    ) -> dict:
        """#100 / topic-hub P8 (manual §3, §4, §14): launch a workflow. Opens a fresh
        WORKFLOW CHAT (a `Conversation` with `run_id`) the run streams into, and returns
        its `chat_id`. ``workflow_id`` selects which of the profile's workflows (§4);
        runs are per-chat, so several may run in parallel on one item (§3). Inputs come
        from the workspace (``MANIFEST.input_json``).

        #343: with a ``chat_id`` the run instead TAKES OVER that existing chat — the one
        the user prepared in — so the workflow's agent nodes inherit the chat's history
        and the run streams into the same thread. The chat must have no active run
        (else 409); once its previous run is terminal it may host another. Without a
        ``chat_id`` the legacy behaviour holds (a fresh workflow chat is opened).

        #197: an external system triggers headlessly by uploading the workflow's input
        FILES in the SAME call — we communicate with workflows through the workspace, not
        a JSON body. A ``multipart/form-data`` body carries ``file`` parts (each part's
        filename IS its workspace path, sub-dirs allowed) and may also carry
        ``workflow_id`` as a form field; the files are written (overwrite) BEFORE the run
        starts. A path that escapes the workspace root aborts the whole call (400) so
        nothing is half-written and no run begins. With no body the call is the plain
        trigger the FE makes — the upload path is skipped entirely."""
        # #306 PR3: launching a workflow drives the agent + stages input files →
        # gate on `converse` BEFORE the upload writes anything.
        locator.require_access(slug, item_id, "converse")
        workflow_id, staged = await _staged_run_uploads(request, workflow_id)
        investigation_id, profile, manifest = await _workflow_manifest_or_404(
            slug, item_id, workflow_id
        )
        # #538: the docstring above promises nothing is half-written, and gating
        # each write would break that — a quota trip mid-loop would leave some
        # inputs staged and no run started. Check the whole staging set once,
        # against what it actually ADDS: re-triggering a run with the same input
        # files overwrites them, and counting those as new bytes would refuse a
        # perfectly ordinary re-run.
        growth = 0
        for norm, data in staged:
            growth += len(data) - (await files.file_size(investigation_id, norm) or 0)
        await files.ensure_room_for(investigation_id, growth)
        for norm, data in staged:
            await files.write(investigation_id, norm, data)
            activity.record(
                "file_written",
                f"Wrote {norm}",
                {"investigation_id": investigation_id, "path": norm},
            )
            logger.debug("workflow_routes: staged upload %s to item %s", norm, investigation_id)
            turn_engine.publish(
                investigation_id, FileChanged(path=norm, by=get_user_id(), kind="written")
            )
        # The chat overlay (manual §3): resolve the chat, start the run on it, then link
        # the run_id back. This runs synchronously before the run task drives any turn, so
        # the chat carries its run_id before it streams. #343: with a chat_id, take over
        # that existing chat; otherwise open a fresh workflow chat (the legacy path).
        if chat_id:
            target_chat_id, _conv = locator.require_chat(slug, item_id, chat_id)
        else:
            title = manifest.title or workflow_id or "Workflow"
            target_chat_id = conv_rm.create(
                Conversation(
                    item_id=investigation_id,
                    title=title,
                    created_ms=now_ms(),
                    # #306 PR3: stamp the item read-chat mirror on the workflow chat.
                    **item_conversation_mirror(spec, investigation_id),
                )
            ).resource_id
        try:
            run_id = await workflow_orchestrator.start(
                slug=slug,
                item_id=investigation_id,
                profile=profile,
                captured_user=get_user_id(),
                workflow_id=workflow_id,
                chat_id=target_chat_id,
            )
        except ActiveRunExists as exc:
            logger.warning(
                "workflow_routes: run rejected on item %s, active run exists: %s",
                investigation_id,
                exc,
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        chat = conv_rm.get(target_chat_id).data
        assert isinstance(chat, Conversation)
        chat.run_id = run_id
        conv_rm.update(target_chat_id, chat)
        activity.record(
            "workflow_started",
            "Started a workflow run",
            {"item_id": investigation_id, "run_id": run_id, "chat_id": target_chat_id},
        )
        logger.info(
            "workflow_routes: workflow run %s started on item %s (workflow=%r chat=%s)",
            run_id,
            investigation_id,
            workflow_id,
            target_chat_id,
        )
        return {"run_id": run_id, "item_id": investigation_id, "chat_id": target_chat_id}

    @app.get("/a/{slug}/items/{item_id}/runs")
    async def list_workflow_runs(slug: str, item_id: str) -> list[dict]:
        """#100: the item's run history (newest first), for the run-list view."""
        investigation_id = locator.require_access(slug, item_id, "read_chat")
        rm = spec.get_resource_manager(WorkflowRun)
        out: list[dict] = []
        for r in rm.list_resources((QB["item_id"] == investigation_id).build()):
            assert isinstance(r.data, WorkflowRun)
            out.append(
                {
                    "run_id": r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    **msgspec.to_builtins(r.data),
                }
            )
        out.sort(key=lambda d: d.get("started") or 0, reverse=True)
        return out

    @app.get("/a/{slug}/items/{item_id}/runs/preview")
    async def preview_workflow_run(
        slug: str, item_id: str, workflow_id: str = Query("")
    ) -> _PreflightPreviewOut:
        """#283 (manual §18): the launch dialog's pre-flight — what THIS workflow will do
        + whether its preconditions are met, WITHOUT starting a run. Reads the staged
        ``input.json`` (manual §14) and calls the author's optional ``preflight(wf, inputs)``
        over a read-only handle (no turn/sandbox drivers — pre-flight only inspects the
        workspace). A workflow with no ``preflight`` previews its phases alone (runnable).
        Registered before ``/runs/{run_id}`` so ``preview`` isn't read as a run id."""
        from ..workflow.discovery import load_preflight_callable

        locator.require_access(slug, item_id, "read_chat")  # #306 PR3: reads the run/workspace
        investigation_id, profile, manifest = await _workflow_manifest_or_404(
            slug, item_id, workflow_id
        )
        wf = WorkflowHandle(
            store=files,
            workspace_id=investigation_id,
            workflow_id=workflow_id,
            config=dict(manifest.config),
            upload_dir=workflow_executor.upload_dir(slug, profile),
            user=get_user_id(),
        )
        inputs = await resolve_inputs(wf, manifest)
        preflight = load_preflight_callable(slug, profile, workflow_id)
        summary = ""
        checks: list[_PreflightCheckOut] = []
        allowed = True
        if preflight is not None:
            report = await preflight(wf, inputs)
            summary = report.summary
            checks = [
                _PreflightCheckOut(
                    label=c.label, ok=c.ok, severity=c.severity.value, reason=c.reason
                )
                for c in report.checks
            ]
            allowed = _preflight_can_run(report)
        return _PreflightPreviewOut(
            workflow_id=workflow_id,
            # The dialog falls back to the workflow id when title is empty (FE side), so
            # send the raw title — no server-side or-chain (keeps the branch coverage clean).
            title=manifest.title,
            description=manifest.description,
            phases=[_PhaseOut(id=p.id, title=p.title) for p in manifest.phases],
            summary=summary,
            checks=checks,
            can_run=allowed,
            has_preflight=preflight is not None,
        )

    @app.get("/a/{slug}/items/{item_id}/runs/{run_id}")
    async def get_workflow_run(slug: str, item_id: str, run_id: str) -> dict:
        """#100 (manual §14): poll a run — status + result + per-phase progress."""
        locator.require_access(slug, item_id, "read_chat")
        rm = spec.get_resource_manager(WorkflowRun)
        try:
            data = rm.get(run_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id!r}") from exc
        assert isinstance(data, WorkflowRun)
        return {"run_id": run_id, **msgspec.to_builtins(data)}

    @app.get("/a/{slug}/items/{item_id}/runs/{run_id}/stream")
    async def stream_workflow_run(slug: str, item_id: str, run_id: str) -> StreamingResponse:
        """#100 / P8 (manual §3, §14): the run's live SSE — its WORKFLOW CHAT's stream
        (agent events + phase/step events overlaid). Falls back to the item's broadcast
        stream when the run / its chat can't be resolved (defensive)."""
        investigation_id = locator.require_access(slug, item_id, "read_chat")
        key = investigation_id
        try:
            run = spec.get_resource_manager(WorkflowRun).get(run_id).data
            if isinstance(run, WorkflowRun) and run.chat_id:
                key = run.chat_id
        except ResourceIDNotFoundError:
            pass
        return StreamingResponse(turn_engine.subscribe_sse(key), media_type="text/event-stream")

    @app.post(
        "/a/{slug}/items/{item_id}/runs/{run_id}/cancel",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_workflow_run(slug: str, item_id: str, run_id: str) -> Response:
        """#100 (manual §10): Stop a run — it goes terminal (cancelled) and the item
        opens to interactive use. Idempotent (a no-op when nothing is running)."""
        investigation_id = locator.require_access(slug, item_id, "converse")
        logger.info(
            "workflow_routes: cancel requested for run %s on item %s", run_id, investigation_id
        )
        await workflow_orchestrator.cancel(run_id, investigation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/a/{slug}/items/{item_id}/runs/{run_id}/decisions",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def decide_workflow_run(
        slug: str, item_id: str, run_id: str, body: _DecisionBody
    ) -> dict:
        """#100 (manual §10): answer a `human_gate` — records the decision artifact
        and resumes the run (completed steps skip; the gate reads the decision)."""
        locator.require_access(slug, item_id, "converse")  # #306 PR3: resumes/drives the run
        investigation_id, profile, _manifest = await _workflow_manifest_or_404(slug, item_id)
        try:
            await workflow_orchestrator.decide(
                slug=slug,
                item_id=investigation_id,
                profile=profile,
                run_id=run_id,
                choice=body.choice,
                input=body.input,
                decided_by=get_user_id(),
            )
        except NotAwaitingDecision as exc:
            logger.warning(
                "workflow_routes: decision for run %s not awaiting a gate: %s", run_id, exc
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info(
            "workflow_routes: decision %r recorded for run %s on item %s",
            body.choice,
            run_id,
            investigation_id,
        )
        return {"run_id": run_id, "resumed": True}

    @app.post(
        "/a/{slug}/items/{item_id}/runs/{run_id}/steer",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def steer_workflow_run(
        slug: str, item_id: str, run_id: str, body: _SteerBody
    ) -> _SteerAck:
        """#288 (manual §10): steer a run in words. Stops it first if it is still going,
        then runs the read-only steerer in the background — it streams into the run's
        chat and, when it has a plan, suspends the run `awaiting_human` with
        `pending_steer` set for the human to confirm (the FE refetches the run)."""
        locator.require_access(slug, item_id, "converse")  # #306 PR3: stops + steers the run
        investigation_id, profile, _manifest = await _workflow_manifest_or_404(slug, item_id)
        try:
            await workflow_orchestrator.steer(
                slug=slug,
                item_id=investigation_id,
                profile=profile,
                run_id=run_id,
                instruction=body.instruction,
            )
        except ResourceIDNotFoundError as exc:
            logger.warning("workflow_routes: steer for unknown run %s: %s", run_id, exc)
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id!r}") from exc
        logger.info(
            "workflow_routes: steer requested for run %s on item %s", run_id, investigation_id
        )
        return _SteerAck(run_id=run_id)

    @app.post(
        "/a/{slug}/items/{item_id}/runs/{run_id}/steer/confirm",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def confirm_steer_workflow_run(
        slug: str, item_id: str, run_id: str, body: _SteerConfirmBody
    ) -> _SteerConfirmOut:
        """#288 (manual §10): resolve a pending steer plan — approve to apply the edits +
        invalidate the steps and resume the same run (the valid prefix skips, §9), or
        reject to discard it (the run returns to its gate or to a stopped state)."""
        locator.require_access(slug, item_id, "converse")  # #306 PR3: resolves the steer plan
        investigation_id, profile, _manifest = await _workflow_manifest_or_404(slug, item_id)
        try:
            await workflow_orchestrator.confirm_steer(
                slug=slug,
                item_id=investigation_id,
                profile=profile,
                run_id=run_id,
                approve=body.approve,
                decided_by=get_user_id(),
            )
        except NotAwaitingSteer as exc:
            logger.warning(
                "workflow_routes: steer confirm for run %s not awaiting: %s", run_id, exc
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info(
            "workflow_routes: steer confirmed for run %s (approve=%s)", run_id, body.approve
        )
        return _SteerConfirmOut(run_id=run_id, applied=body.approve)

    @app.get("/a/{slug}/items/{item_id}/event-triggers/lag")
    async def event_trigger_lag(slug: str, item_id: str) -> list[dict]:
        """#429 P11 (D2d): the records each of this app's event triggers has NOT fired for on
        this item — the watermark's discoverable lag (a run missed when a pod died mid-dispatch,
        or a trigger added after the write). Read-only, so it doubles as the backfill dry-run."""
        investigation_id = locator.require_access(slug, item_id, "read_meta")
        triggers = [t for t in event_dispatcher.event_triggers() if t.slug == slug]
        lag = await find_trigger_lag(
            investigation_id,
            triggers=triggers,
            entities_of=await _item_entities_of(investigation_id),
            watermark=event_dispatcher.watermark,
        )
        return [msgspec.to_builtins(tl) for tl in lag]

    @app.post("/a/{slug}/items/{item_id}/event-triggers/backfill")
    async def backfill_event_triggers(slug: str, item_id: str) -> dict:
        """#429 P11 (D2d): re-dispatch every record its trigger's watermark is behind on,
        catching the missed runs up. Idempotent — the dispatcher's once-per-version gate means
        a version a live dispatch already handled is a no-op, so it's safe to re-run."""
        investigation_id = locator.require_access(slug, item_id, "converse")
        triggers = [t for t in event_dispatcher.event_triggers() if t.slug == slug]
        report = await backfill_trigger_lag(
            investigation_id,
            triggers=triggers,
            entities_of=await _item_entities_of(investigation_id),
            watermark=event_dispatcher.watermark,
            dispatch=event_dispatcher.dispatch,
        )
        logger.info(
            "workflow_routes: event-trigger backfill run for item %s of app %s",
            investigation_id,
            slug,
        )
        return msgspec.to_builtins(report)
