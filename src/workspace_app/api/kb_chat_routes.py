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

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..agent.context import AgentToolContext
from ..kb.agent import default_kb_agent_config
from ..kb.citations import parse_citations
from ..kb.retriever import Retriever
from ..resources.kb import KbChat, KbMessage
from .events import AgentEvent, MessageDelta, RunError, ToolEnd, ToolStart, to_sse
from .runner import AgentRunner


async def answer_question(
    runner: AgentRunner, retriever: Retriever, collection_ids: list[str], question: str
) -> str:
    """Run one KB-agent turn to completion (no streaming) and return its answer
    with a compact sources footer. This is how the RCA agent's
    `ask_knowledge_base` tool consults the KB — a synthesized, cited reply
    rather than raw passages."""
    ctx = AgentToolContext(
        retriever=retriever,
        collection_ids=collection_ids,
        agent_config=default_kb_agent_config(),
    )
    parts: list[str] = []
    async for ev in runner.run(question, ctx):
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            parts.append(ev.text)
    answer = "".join(parts)
    cites = parse_citations(answer, ctx.kb_passages)
    if cites:
        footer = "; ".join(f"[{c.marker}] {c.filename}" for c in cites)
        answer = f"{answer}\n\nSources: {footer}"
    return answer


class _ChatBody(BaseModel):
    title: str = "New chat"
    collection_ids: list[str] = []


class _MsgBody(BaseModel):
    content: str


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def register_kb_chat_routes(
    app: FastAPI, spec: SpecStar, runner: AgentRunner, retriever: Retriever
) -> None:
    chat_rm = spec.get_resource_manager(KbChat)

    def _load(chat_id: str) -> KbChat:
        try:
            data = chat_rm.get(chat_id).data
        except ResourceIDNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        assert isinstance(data, KbChat)  # the KbChat manager yields KbChat
        return data

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
        out: list[dict] = []
        for r in chat_rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            data = r.data
            assert isinstance(data, KbChat)
            out.append(
                {
                    "resource_id": r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    "title": data.title,
                    "collection_ids": data.collection_ids,
                    "message_count": len(data.messages),
                }
            )
        return out

    @app.get("/kb/chats/{chat_id}")
    async def get_chat(chat_id: str) -> dict:
        data = _load(chat_id)
        return {
            "resource_id": chat_id,
            "title": data.title,
            "collection_ids": data.collection_ids,
            "messages": [_message_dict(m) for m in data.messages],
        }

    @app.delete("/kb/chats/{chat_id}", status_code=204)
    async def delete_chat(chat_id: str) -> Response:
        _load(chat_id)  # 404 if missing
        chat_rm.permanently_delete(chat_id)
        return Response(status_code=204)

    @app.post("/kb/chats/{chat_id}/messages")
    async def send_message(chat_id: str, body: _MsgBody) -> StreamingResponse:
        chat = _load(chat_id)
        chat.messages.append(KbMessage(role="user", content=body.content, created_at=_now_ms()))
        chat_rm.update(chat_id, chat)

        ctx = AgentToolContext(
            retriever=retriever,
            collection_ids=chat.collection_ids,
            agent_config=default_kb_agent_config(),
        )
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        async def drive() -> None:
            try:
                async for ev in runner.run(body.content, ctx):
                    await queue.put(ev)
            except Exception as exc:  # noqa: BLE001 — surface as a terminal error event
                await queue.put(RunError(message=f"{type(exc).__name__}: {exc}"))
            finally:
                await queue.put(None)

        asyncio.create_task(drive())

        async def gen() -> AsyncIterator[str]:
            produced: list[KbMessage] = []
            pending_tools: dict[str, ToolStart] = {}

            def add_assistant(text: str, reasoning: bool) -> None:
                last = produced[-1] if produced else None
                # A tool message between answers starts a fresh assistant turn.
                if last is None or last.role != "assistant":
                    last = KbMessage(role="assistant", created_at=_now_ms())
                    produced.append(last)
                if reasoning:
                    last.reasoning = (last.reasoning or "") + text
                else:
                    last.content += text

            while True:
                item = await queue.get()
                if item is None:
                    if produced:
                        # resolve [n] on answers against the turn's searched passages
                        for m in produced:
                            if m.role == "assistant":
                                m.citations = parse_citations(m.content, ctx.kb_passages)
                        fresh = _load(chat_id)
                        fresh.messages.extend(produced)
                        chat_rm.update(chat_id, fresh)
                    return
                if isinstance(item, MessageDelta):
                    add_assistant(item.text, item.reasoning)
                elif isinstance(item, ToolStart):
                    pending_tools[item.call_id] = item
                elif isinstance(item, ToolEnd):
                    start = pending_tools.pop(item.call_id, None)
                    produced.append(
                        KbMessage(
                            role="tool",
                            content=item.output,
                            tool_call_id=item.call_id,
                            tool_name=start.name if start else None,
                            tool_args=dict(start.args) if start else None,
                            created_at=_now_ms(),
                        )
                    )
                yield to_sse(item)

        return StreamingResponse(gen(), media_type="text/event-stream")


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
