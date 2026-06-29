"""Item capability + export routes (#54).

The workflow HTTP capabilities a deterministic node's sandbox script calls with its
run-scoped credential (ingest a file, upsert a context card), plus the read-only
conversation export. Each authenticates an ``X-Workflow-Token`` (scoped to the item)
or falls back to the session user, then delegates to the ``WorkflowExecutor``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import msgspec
from fastapi import APIRouter, FastAPI, Header, HTTPException, Response
from specstar import SpecStar

from ..resources import Conversation
from ..workflow.capabilities import CollectionNotFound
from ..workflow.credential import CredentialBroker
from .locator import ItemLocator
from .schemas import _CardBody, _IngestBody
from .timeutil import now_ms
from .workflow_exec import WorkflowExecutor


def register_capability_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
    workflow_credentials: CredentialBroker,
    workflow_executor: WorkflowExecutor,
) -> None:
    """Mount the item capability + export routes onto ``app``."""
    conv_rm = spec.get_resource_manager(Conversation)

    @app.post("/a/{slug}/items/{item_id}/capabilities/ingest")
    async def capability_ingest(
        slug: str,
        item_id: str,
        body: _IngestBody,
        x_workflow_token: str | None = Header(default=None),
    ) -> dict:
        """#100 (manual §8): the ingest capability as an HTTP endpoint — a
        deterministic node's sandbox script reaches it with the run-scoped
        credential (manual §15). Idempotent (upsert by natural-key doc id).

        Auth: a valid ``X-Workflow-Token`` acts as its captured user and must be
        scoped to THIS item; an expired/forged token is 401. With no token the call
        falls back to the session user (the in-app / FE path)."""
        investigation_id = locator.require_item(slug, item_id)
        actor = get_user_id()
        if x_workflow_token is not None:
            claims = workflow_credentials.resolve(x_workflow_token)
            if claims is None or claims.item_id != investigation_id:
                raise HTTPException(status_code=401, detail="invalid or expired workflow token")
            actor = claims.user
        try:
            # The receipt for this direct/HTTP capability call lands in the _default
            # journal folder (#136) — the handle-driven node path threads the run's own
            # per-workflow journal_dir; this vestigial receipt has no run handle here.
            doc_id = await workflow_executor.ingest(
                investigation_id, actor, body.collection, body.path
            )
        except CollectionNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown collection: {body.collection!r}"
            ) from exc
        return {"doc_id": doc_id}

    @app.post("/a/{slug}/items/{item_id}/capabilities/context-card")
    async def capability_context_card(
        slug: str,
        item_id: str,
        body: _CardBody,
        x_workflow_token: str | None = Header(default=None),
    ) -> dict:
        """topic-hub P9 / #111 (manual §8): the upsert-context-card capability as an HTTP
        endpoint — a deterministic node's sandbox script reaches it with the run-scoped
        credential (manual §15). Idempotent: create-or-update by key, so a re-run updates
        the card instead of duplicating it. Same auth as ingest: a valid
        ``X-Workflow-Token`` acts as its captured user, scoped to THIS item; no token →
        the session user."""
        investigation_id = locator.require_item(slug, item_id)
        actor = get_user_id()
        if x_workflow_token is not None:
            claims = workflow_credentials.resolve(x_workflow_token)
            if claims is None or claims.item_id != investigation_id:
                raise HTTPException(status_code=401, detail="invalid or expired workflow token")
            actor = claims.user
        try:
            card_id = await workflow_executor.upsert_card(
                actor, body.collection, body.keys, body.title, body.body
            )
        except CollectionNotFound as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown collection: {body.collection!r}"
            ) from exc
        return {"card_id": card_id}

    @app.get("/a/{slug}/items/{item_id}/export")
    async def export_investigation(slug: str, item_id: str) -> Response:
        """Download the investigation's full conversation as JSON — every message
        with its reasoning, tool calls (name/args/output), citations, metrics and
        timestamps, plus the case metadata. Read-only (won't create a
        conversation) and curl-friendly, so it doubles as a debug dump."""
        investigation_id = locator.require_item(slug, item_id)
        from specstar import QB

        from ..apps.resolve import find_work_item

        meta: dict[str, object] = {"id": investigation_id}
        found = find_work_item(spec, investigation_id)
        # require_item already validated the id, so found is always present here;
        # the None default is defensive against a delete-between-calls race.
        if found is not None:  # pragma: no branch
            meta = {"id": investigation_id, **msgspec.to_builtins(found[1])}

        messages: list = []
        for r in conv_rm.list_resources((QB["item_id"] == investigation_id).build()):
            assert isinstance(r.data, Conversation)
            messages = msgspec.to_builtins(r.data.messages)
            break

        payload = {"investigation": meta, "exported_at": now_ms(), "messages": messages}
        filename = f"investigation-{investigation_id}.json"
        return Response(
            content=json.dumps(payload, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
