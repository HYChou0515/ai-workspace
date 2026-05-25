"""KB chat routes — multi-thread chat against one or more collections.

Each thread is a KbChat (specstar resource). A message turn drives the KB agent
through the shared AgentRunner with a KB-flavoured context (retriever +
collection_ids, no sandbox), streams the agent's events over SSE, and persists
the assistant answer with its [n] citations resolved against the passages the
turn's kb_search calls accumulated.

User, assistant (with [n] citations), and tool-call messages all persist, so
reopening a thread shows the answer, its sources, and what the agent searched.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import msgspec
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..agent.context import AgentToolContext
from ..kb.agent import default_kb_agent_config
from ..kb.citations import parse_citations
from ..kb.cited import record_citations
from ..kb.retriever import Retriever
from ..resources.kb import Citation, KbChat, KbMessage
from .events import AgentEvent, MessageDelta, ToolStart
from .notifications import notify
from .runner import AgentRunner
from .turns import ChatTurnEngine, TurnMessage


def kb_progress(ev: AgentEvent) -> str | None:
    """Render a KB sub-agent event as a one-line progress note for the parent
    (RCA) stream, so the user sees the KB agent's searches and reasoning live
    while `ask_knowledge_base` runs. ``None`` ⇒ nothing worth surfacing."""
    if isinstance(ev, ToolStart):
        query = ev.args.get("query")
        return f"🔎 {ev.name}: {query}\n" if query else f"🔎 {ev.name}\n"
    if isinstance(ev, MessageDelta) and ev.reasoning:
        return ev.text
    return None


async def answer_question(
    runner: AgentRunner,
    retriever: Retriever,
    collection_ids: list[str],
    question: str,
    on_event: Callable[[AgentEvent], None] | None = None,
    on_citations: Callable[[list[Citation]], None] | None = None,
) -> str:
    """Run one KB-agent turn to completion (no streaming) and return its answer
    with a compact sources footer. This is how the RCA agent's
    `ask_knowledge_base` tool consults the KB — a synthesized, cited reply
    rather than raw passages.

    `on_event` (when given) is fired for every KB event as it happens, so a
    caller can surface the sub-agent's intermediate work (e.g. relay it into the
    parent stream). `on_citations` (when given) receives the resolved citations
    so the caller can log them (this path doesn't persist a KbMessage). The
    return value is unchanged."""
    ctx = AgentToolContext(
        retriever=retriever,
        collection_ids=collection_ids,
        agent_config=default_kb_agent_config(),
    )
    parts: list[str] = []
    async for ev in runner.run(question, ctx):
        if on_event is not None:
            on_event(ev)
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            parts.append(ev.text)
    answer = "".join(parts)
    cites = parse_citations(answer, ctx.kb_passages)
    if on_citations is not None:
        on_citations(cites)
    if cites:
        footer = "; ".join(f"[{c.marker}] {c.filename}" for c in cites)
        answer = f"{answer}\n\nSources: {footer}"
    return answer


class _ChatBody(BaseModel):
    title: str = "New chat"
    collection_ids: list[str] = []


class _MsgBody(BaseModel):
    content: str


class _ShareBody(BaseModel):
    user_ids: list[str]


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def register_kb_chat_routes(
    app: FastAPI,
    spec: SpecStar,
    engine: ChatTurnEngine,
    retriever: Retriever,
    get_user_id: Callable[[], str],
) -> None:
    chat_rm = spec.get_resource_manager(KbChat)

    def _load_rev(chat_id: str) -> tuple[KbChat, str]:
        """Return (chat, owner_id). 404 if missing. Owner = created_by meta."""
        try:
            rev = chat_rm.get(chat_id)
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        data = rev.data
        assert isinstance(data, KbChat)  # the KbChat manager yields KbChat
        return data, rev.info.created_by

    def _load(chat_id: str) -> KbChat:
        return _load_rev(chat_id)[0]

    def _require_owner(chat_id: str) -> tuple[KbChat, str]:
        chat, owner = _load_rev(chat_id)
        if owner != get_user_id():
            raise HTTPException(status_code=403, detail="only the owner can do that")
        return chat, owner

    @app.get("/kb/agent")
    async def kb_agent_config() -> dict:
        """The KB agent's display name + quick-prompt suggestions (the chat UI
        renders these as chips — they live with the config, not the FE)."""
        cfg = default_kb_agent_config()
        return {"name": cfg.name, "suggestions": cfg.suggestions}

    @app.post("/kb/chats")
    async def create_chat(body: _ChatBody) -> dict:
        rev = chat_rm.create(KbChat(title=body.title, collection_ids=body.collection_ids))
        return {
            "resource_id": rev.resource_id,
            "title": body.title,
            "collection_ids": body.collection_ids,
        }

    @app.get("/kb/chats")
    async def list_chats() -> list[dict]:
        """Only the current user's chats: ones they own + ones shared with them."""
        me = get_user_id()
        out: list[dict] = []
        for r in chat_rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            data = r.data
            assert isinstance(data, KbChat)
            owner = r.info.created_by  # ty: ignore[unresolved-attribute]
            if owner != me and me not in data.shared_with:
                continue
            out.append(
                {
                    "resource_id": r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    "title": data.title,
                    "collection_ids": data.collection_ids,
                    "message_count": len(data.messages),
                    "owner": owner,
                    "shared_with": data.shared_with,
                }
            )
        return out

    @app.get("/kb/chats/{chat_id}")
    async def get_chat(chat_id: str) -> dict:
        data, owner = _load_rev(chat_id)
        me = get_user_id()
        if owner != me and me not in data.shared_with:
            raise HTTPException(status_code=403, detail="not shared with you")
        return {
            "resource_id": chat_id,
            "title": data.title,
            "collection_ids": data.collection_ids,
            "messages": [_message_dict(m) for m in data.messages],
            "owner": owner,
            "shared_with": data.shared_with,
        }

    @app.delete("/kb/chats/{chat_id}", status_code=204)
    async def delete_chat(chat_id: str) -> Response:
        _require_owner(chat_id)
        chat_rm.permanently_delete(chat_id)
        engine.forget(chat_id)
        return Response(status_code=204)

    @app.post("/kb/chats/{chat_id}/share", status_code=204)
    async def share_chat(chat_id: str, body: _ShareBody) -> Response:
        """Owner shares the thread read-only with users → they're added to
        `shared_with` and each newly-added user gets a `share` notification."""
        chat, _ = _require_owner(chat_id)
        new = [u for u in body.user_ids if u not in chat.shared_with and u != get_user_id()]
        if new:
            chat_rm.update(
                chat_id, msgspec.structs.replace(chat, shared_with=[*chat.shared_with, *new])
            )
            for uid in new:
                notify(
                    spec,
                    recipient=uid,
                    kind="share",
                    title=f'Shared a chat: "{chat.title}"',
                    link=f"/kb/chats/{chat_id}",
                    actor=get_user_id(),
                )
        return Response(status_code=204)

    @app.delete("/kb/chats/{chat_id}/share/{user_id}", status_code=204)
    async def unshare_chat(chat_id: str, user_id: str) -> Response:
        chat, _ = _require_owner(chat_id)
        if user_id in chat.shared_with:
            chat_rm.update(
                chat_id,
                msgspec.structs.replace(
                    chat, shared_with=[u for u in chat.shared_with if u != user_id]
                ),
            )
        return Response(status_code=204)

    @app.post("/kb/chats/{chat_id}/messages")
    async def send_message(chat_id: str, body: _MsgBody) -> StreamingResponse:
        chat, owner = _load_rev(chat_id)
        if owner != get_user_id():
            # Shares are read-only — only the owner drives the thread.
            raise HTTPException(status_code=403, detail="this chat is read-only for you")
        chat.messages.append(KbMessage(role="user", content=body.content, created_at=_now_ms()))
        chat_rm.update(chat_id, chat)

        ctx = AgentToolContext(
            retriever=retriever,
            collection_ids=chat.collection_ids,
            agent_config=default_kb_agent_config(),
        )

        def persist(produced: list[TurnMessage]) -> None:
            if not produced:
                return
            fresh = _load(chat_id)
            for m in produced:
                km = KbMessage(
                    role=m.role,
                    content=m.content,
                    reasoning=m.reasoning,
                    tool_call_id=m.tool_call_id,
                    tool_name=m.tool_name,
                    tool_args=m.tool_args,
                    created_at=m.created_at,
                )
                # Resolve [n] on answers against the passages this turn searched,
                # and log each as a CitationEvent (powers the cited counts).
                if m.role == "assistant":
                    km.citations = parse_citations(m.content, ctx.kb_passages)
                    record_citations(
                        spec,
                        km.citations,
                        origin_kind="kb_chat",
                        origin_id=chat_id,
                        cited_by=get_user_id(),
                    )
                fresh.messages.append(km)
            chat_rm.update(chat_id, fresh)

        return await engine.stream(chat_id, body.content, ctx, on_complete=persist)

    @app.delete("/kb/chats/{chat_id}/messages/current", status_code=204)
    async def cancel_message(chat_id: str) -> Response:
        """Interrupt the chat's in-flight turn (its stream gets RunCancelled,
        then closes). 204 even when nothing is running — same as RCA."""
        await engine.cancel(chat_id)
        return Response(status_code=204)


def _message_dict(m: KbMessage) -> dict:
    return {
        "role": m.role,
        "content": m.content,
        "reasoning": m.reasoning,
        "tool_name": m.tool_name,
        "tool_args": m.tool_args,
        "tool_call_id": m.tool_call_id,
        "created_at": m.created_at,
        "citations": [
            {
                "marker": c.marker,
                "collection_id": c.collection_id,
                "document_id": c.document_id,
                "filename": c.filename,
                "start": c.start,
                "end": c.end,
                "source_chunk_ids": c.source_chunk_ids,
                "snippet": c.snippet,
            }
            for c in m.citations
        ],
    }
