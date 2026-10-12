"""Routes for the outside helper (docs/plan-outside-helper.md §3.2, §3.3).

Two callers:

- **the card**, in the chat — hands a lookup to the person's helper, reads how
  it is going, cancels it. Asks ``converse``, as answering the card by hand does.
- **the helper**, a Python program on the person's PC — checks in, takes the
  next job, reports a failure. It authenticates as the person (the deploy's
  login, D6), so ``get_user_id`` is the person and it only ever sees their jobs.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel

from ..resources.conversation import Conversation
from .locator import ItemLocator
from .outside_jobs import OutsideJob, OutsideJobStore, job_id
from .outside_lookup_routes import _open_card


class HandOverIn(BaseModel):
    tool_call_id: str
    #: The query as the person edited it on the card (D4); ignored for a URL.
    query: str = ""


class JobOut(BaseModel):
    id: str
    kind: str
    args: str
    state: str
    error: str = ""


class HeartbeatIn(BaseModel):
    version: str = ""
    protocol: int = 0


class HelperStatusOut(BaseModel):
    online: bool


class FailIn(BaseModel):
    error: str = ""


#: Longest failure reason kept: it is shown on the card, not a log.
_ERROR_CHARS = 500


def _out(jid: str, job: OutsideJob) -> JobOut:
    return JobOut(id=jid, kind=job.kind, args=job.args, state=job.state, error=job.error)


def register_outside_job_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    store: OutsideJobStore,
    get_user_id: Callable[[], str],
) -> None:
    async def hand_over(
        slug: str,
        item_id: str,
        conversation: Callable[[], tuple[str, Conversation]],
        body: HandOverIn,
    ) -> JobOut:
        investigation_id = locator.require_access(slug, item_id, "converse")
        rid, conv = conversation()
        card = _open_card(conv, body.tool_call_id)
        owner = get_user_id()
        if not await store.online(owner):
            raise HTTPException(status_code=409, detail="your outside helper is not connected")
        if "query" in card:
            kind, args = "search", (body.query.strip() or card["query"])
        else:
            kind, args = "open_url", card["url"]
        job = OutsideJob(
            owner=owner,
            item_id=investigation_id,
            slug=slug,
            chat_id=rid,
            tool_call_id=body.tool_call_id,
            kind=kind,
            args=args,
            state="queued",
            created_ms=store.now(),
        )
        jid = await store.hand_over(job)
        return _out(jid, job)

    @app.post("/a/{slug}/items/{item_id}/outside-jobs", status_code=201, response_model=JobOut)
    async def hand_over_default_chat(slug: str, item_id: str, body: HandOverIn) -> JobOut:
        return await hand_over(slug, item_id, lambda: locator.conversation_for(item_id), body)

    @app.post(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/outside-jobs",
        status_code=201,
        response_model=JobOut,
    )
    async def hand_over_chat(slug: str, item_id: str, chat_id: str, body: HandOverIn) -> JobOut:
        return await hand_over(
            slug, item_id, lambda: locator.require_chat(slug, item_id, chat_id), body
        )

    def _card_job_id(slug: str, item_id: str, chat_rid: str, call: str, verb: str) -> str:
        investigation_id = locator.require_access(slug, item_id, verb)  # ty: ignore[invalid-argument-type]
        return job_id(investigation_id, chat_rid, call)

    async def read_job(jid: str) -> JobOut:
        job = await store.get(jid)
        if job is None:
            raise HTTPException(status_code=404, detail="this card has no helper job")
        return _out(jid, job)

    async def cancel_job(jid: str) -> Response:
        if not await store.cancel(jid):
            raise HTTPException(status_code=404, detail="no running helper job for this card")
        return Response(status_code=204)

    def _default_rid(item_id: str) -> str:
        return locator.conversation_for(item_id)[0]

    def _chat_rid(slug: str, item_id: str, chat_id: str) -> str:
        return locator.require_chat(slug, item_id, chat_id)[0]

    @app.get("/a/{slug}/items/{item_id}/outside-jobs/{tool_call_id}", response_model=JobOut)
    async def read_default_chat(slug: str, item_id: str, tool_call_id: str) -> JobOut:
        jid = _card_job_id(slug, item_id, _default_rid(item_id), tool_call_id, "read_chat")
        return await read_job(jid)

    @app.get(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/outside-jobs/{tool_call_id}",
        response_model=JobOut,
    )
    async def read_chat(slug: str, item_id: str, chat_id: str, tool_call_id: str) -> JobOut:
        rid = _chat_rid(slug, item_id, chat_id)
        return await read_job(_card_job_id(slug, item_id, rid, tool_call_id, "read_chat"))

    @app.delete("/a/{slug}/items/{item_id}/outside-jobs/{tool_call_id}", status_code=204)
    async def cancel_default_chat(slug: str, item_id: str, tool_call_id: str) -> Response:
        jid = _card_job_id(slug, item_id, _default_rid(item_id), tool_call_id, "converse")
        return await cancel_job(jid)

    @app.delete(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/outside-jobs/{tool_call_id}", status_code=204
    )
    async def cancel_chat(slug: str, item_id: str, chat_id: str, tool_call_id: str) -> Response:
        rid = _chat_rid(slug, item_id, chat_id)
        return await cancel_job(_card_job_id(slug, item_id, rid, tool_call_id, "converse"))

    # ── the helper ──────────────────────────────────────────────────────────

    @app.post("/outside-helper/jobs/{jid}/fail", status_code=204)
    async def fail(jid: str, body: FailIn) -> Response:
        if not await store.fail(jid, get_user_id(), body.error.strip()[:_ERROR_CHARS]):
            raise HTTPException(status_code=404, detail="no claimed job of yours with this id")
        return Response(status_code=204)

    @app.post("/outside-helper/heartbeat", status_code=204)
    async def heartbeat(body: HeartbeatIn) -> Response:
        await store.heartbeat(get_user_id(), version=body.version, protocol=body.protocol)
        return Response(status_code=204)

    @app.get("/outside-helper/status", response_model=HelperStatusOut)
    async def status() -> HelperStatusOut:
        return HelperStatusOut(online=await store.online(get_user_id()))

    @app.post("/outside-helper/jobs/claim", response_model=JobOut | None)
    async def claim() -> JobOut | Response:
        got = await store.claim(get_user_id())
        if got is None:
            return Response(status_code=204)
        return _out(*got)


__all__ = ["register_outside_job_routes"]
