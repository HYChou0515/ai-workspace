"""Routes for the outside helper (docs/plan-outside-helper.md §3.2, §3.3).

Two callers:

- **the card**, in the chat — hands a lookup to the person's helper, reads how
  it is going, cancels it. Handing over and cancelling ask ``converse``, as
  answering the card by hand does; reading how it goes asks ``read_chat``.
- **the helper**, a Python program on the person's PC — checks in, takes the
  next job, reports a failure. It authenticates as the person (the deploy's
  login, D6), so ``get_user_id`` is the person and it only ever sees their jobs.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from ..resources.conversation import Conversation
from .locator import ItemLocator
from .outside_helper_release import read_release
from .outside_jobs import CLAIM_TIMEOUT_MS, MIN_PROTOCOL, OutsideJob, OutsideJobStore, job_id
from .outside_lookup_routes import AnswerForm, Deliver, OutsideAnswerOut, _open_card


class HandOverIn(BaseModel):
    tool_call_id: str
    #: The query as the person edited it on the card (D4); ignored for a URL.
    query: str = ""
    #: The card asking on its own, with no 「查」 (D12): allowed only for a URL
    #: an earlier lookup in this chat brought back, and never restarts a job.
    auto: bool = False


class JobOut(BaseModel):
    id: str
    kind: str
    args: str
    state: str
    error: str = ""
    #: What the helper waits on (`captcha`), shown on the card while claimed.
    note: str = ""
    #: Opened with no nod — a link an earlier lookup brought back (D12).
    auto: bool = False
    #: Where the page was saved, once done.
    saved: str = ""


class HeartbeatIn(BaseModel):
    version: str = ""
    protocol: int = 0
    chrome: str = ""
    playwright: str = ""
    python: str = ""
    update: str = ""


def _version(text: str) -> tuple[int, ...]:
    """`1.10.0` as numbers (newer than `1.9.0`); unreadable reads as oldest."""
    try:
        return tuple(int(part) for part in text.strip().split("."))
    except ValueError:
        return (0,)


class HelperStatusOut(BaseModel):
    online: bool


class ResultIn(BaseModel):
    """One fetched page: the page as Markdown (the card's own paste converter,
    D11), where it was, and the person's local date for the file name."""

    markdown: str
    url: str = ""
    title: str = ""
    date: str = ""


class FailIn(BaseModel):
    error: str = ""


class NoteIn(BaseModel):
    note: str = ""


#: Longest failure reason kept: it is shown on the card, not a log.
_ERROR_CHARS = 500


def _asker(conv: Conversation, call_id: str) -> str | None:
    """Who sent the message that started the turn drawing card `call_id` — the
    last person's message before that card."""
    at = next(
        (i for i, m in enumerate(conv.messages) if m.role == "tool" and m.tool_call_id == call_id),
        len(conv.messages),
    )
    return next(
        (m.author for m in reversed(conv.messages[:at]) if m.role == "user"),
        None,
    )


def _out(jid: str, job: OutsideJob, *, now: int | None = None) -> JobOut:
    """The job as the card reads it. Given `now`, a claim no helper renewed
    within CLAIM_TIMEOUT_MS reads as `queued` — that is what it is (the next
    claim takes it); read as `claimed`, a card whose helper died mid-job showed
    it at work forever."""
    lapsed = now is not None and job.state == "claimed" and now - job.claimed_ms > CLAIM_TIMEOUT_MS
    return JobOut(
        id=jid,
        kind=job.kind,
        args=job.args,
        state="queued" if lapsed else job.state,
        error=job.error,
        note=job.note,
        auto=job.auto,
        saved=job.saved,
    )


def register_outside_job_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    store: OutsideJobStore,
    get_user_id: Callable[[], str],
    deliver: Deliver,
    release_dir: str | None = None,
    max_file_size: int = 2 * 1024 * 1024 * 1024,
) -> None:
    def current_helper(request: Request) -> None:
        """§3.7.3: a helper below the minimum protocol (ours, in code) or the
        minimum version (the operator's, in the published manifest) is told to
        update — 426 with where the current release is — rather than left to
        misread an API that moved on. Only a request that says it is a helper
        is judged; the card's own reads never are."""
        version = request.headers.get("X-Outside-Helper-Version")
        protocol = request.headers.get("X-Outside-Helper-Protocol")
        if version is None and protocol is None:
            return
        found = read_release(release_dir)
        too_old_protocol = _version(protocol or "0") < (MIN_PROTOCOL,)
        too_old_version = found is not None and _version(version or "0") < _version(
            found[0].min_version
        )
        if too_old_protocol or too_old_version:
            raise HTTPException(
                status_code=426,
                detail={
                    "message": "this outside helper is too old — update it",
                    "release": found[0].model_dump() if found else None,
                },
            )

    helper_only = [Depends(current_helper)]

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
        jid = job_id(investigation_id, rid, body.tool_call_id)
        existing = await store.get(jid)
        if body.auto:
            if kind != "open_url" or not await store.seen(investigation_id, rid, args):
                raise HTTPException(
                    status_code=409, detail="this lookup waits for the person's 「查」"
                )
            if _asker(conv, body.tool_call_id) != owner:
                # plan §3.2: opened for the person whose answer led to it — not
                # for whoever else has the chat open, whose helper would fetch
                # it as them
                raise HTTPException(
                    status_code=409, detail="this lookup opens by itself only for who asked"
                )
            if existing is not None:
                # running, ran, or stopped by the person: the card asking again
                # (it asks on every mount) starts nothing — 「查」 still can
                return _out(jid, existing, now=store.now())
        elif (
            existing is not None
            and existing.owner == owner
            and existing.state in ("queued", "claimed")
        ):
            # under way (a reloaded card offering 「查」 again): never restarted
            return _out(jid, existing, now=store.now())
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
            auto=body.auto,
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
        return _out(jid, job, now=store.now())

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

    @app.post(
        "/outside-helper/jobs/{jid}/result",
        response_model=OutsideAnswerOut,
        dependencies=helper_only,
    )
    async def result(request: Request, jid: str, body: ResultIn) -> OutsideAnswerOut:
        """The page the helper fetched answers the card — through the SAME code
        a person's paste goes through (`deliver`), as the person who handed it
        over: the helper is signed in as them, and only their claimed job is
        theirs to answer."""
        owner = get_user_id()
        job = await store.get(jid)
        if job is None or job.owner != owner or job.state != "claimed":
            raise HTTPException(status_code=404, detail="no claimed job of yours with this id")
        if len(body.markdown.encode()) > max_file_size:
            # the same limit a pasted page meets (`filestore.max_file_size`)
            detail = f"the page is larger than this workspace accepts ({max_file_size} bytes)"
            await store.fail(jid, owner, detail)
            raise HTTPException(status_code=413, detail=detail)
        form = AnswerForm(
            job.tool_call_id,
            "found",
            body.markdown,
            job.args if job.kind == "search" else "",
            body.url,
            "",
            "",
            body.date,
            [],
            by_helper=True,
        )
        try:
            out = await deliver(
                request,
                job.slug,
                job.item_id,
                lambda: locator.require_chat(job.slug, job.item_id, job.chat_id),
                locator.engine_key,
                form,
            )
        except HTTPException as exc:
            if exc.status_code == 409:  # answered meanwhile: theirs stands
                await store.end(jid, owner, "cancelled")
            elif 400 <= exc.status_code < 500:
                # Never sendable as it is (nothing to send, the chat gone, the
                # right to send revoked): end it, with why — left claimed, it
                # went back to the queue to be fetched and refused forever. A
                # 5xx may pass: the job stays claimed and is tried again.
                await store.fail(jid, owner, str(exc.detail)[:_ERROR_CHARS])
            raise
        await store.end(jid, owner, "done", saved=out.path or "")
        return out

    @app.post("/outside-helper/jobs/{jid}/fail", status_code=204, dependencies=helper_only)
    async def fail(jid: str, body: FailIn) -> Response:
        if not await store.fail(jid, get_user_id(), body.error.strip()[:_ERROR_CHARS]):
            raise HTTPException(status_code=404, detail="no claimed job of yours with this id")
        return Response(status_code=204)

    @app.post("/outside-helper/jobs/{jid}/note", status_code=204, dependencies=helper_only)
    async def note(jid: str, body: NoteIn) -> Response:
        if not await store.note(jid, get_user_id(), body.note.strip()[:_ERROR_CHARS]):
            raise HTTPException(status_code=404, detail="no claimed job of yours with this id")
        return Response(status_code=204)

    @app.post("/outside-helper/heartbeat", status_code=204, dependencies=helper_only)
    async def heartbeat(body: HeartbeatIn) -> Response:
        await store.heartbeat(get_user_id(), **body.model_dump())
        return Response(status_code=204)

    @app.get("/outside-helper/status", response_model=HelperStatusOut)
    async def status() -> HelperStatusOut:
        return HelperStatusOut(online=await store.online(get_user_id()))

    @app.post("/outside-helper/jobs/claim", response_model=JobOut | None, dependencies=helper_only)
    async def claim() -> JobOut | Response:
        got = await store.claim(get_user_id())
        if got is None:
            return Response(status_code=204)
        return _out(*got)


__all__ = ["register_outside_job_routes"]
