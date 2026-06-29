"""RCA workspace chat routes (#54).

The item-level chat (``/messages``, ``/stream``, cancel, undo) and the multi-chat
endpoints (``/chats`` CRUD + per-chat send/stream/cancel/undo), plus @-mention,
the manual chat→knowledge promote, and the ``.chat.json`` export. The turn-driving
core (`send_into`) and the human-mention recorder (`record_mention`) are injected —
they hold the per-turn context the workspace shares with the workflow driver, so
they live next to ``create_app``; these routes only address chats and delegate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from specstar import SpecStar

from ..kb.ingest import Ingestor
from ..resources import Conversation
from ..workflow.orchestrator import WorkflowOrchestrator
from .activity import ActivityLog
from .chat_info import chat_info_from_resource, item_run_status
from .locator import ItemLocator
from .promote import promote_chat_to_kb
from .rca_messages import undo_cut_index
from .schemas import (
    _ChatInfo,
    _CreateChatBody,
    _MentionBody,
    _MessageBody,
    _RenameChatBody,
    _UndoOut,
)
from .timeutil import now_ms
from .turns import ChatTurnEngine

# `send_into(investigation_id, rid, conv, engine_key, body)` — append the user message,
# build the RCA turn ctx, enqueue the turn. `record_mention(investigation_id, title,
# user_ids, note, *, actor, author)` — append a mention entry + notify each user.
SendInto = Callable[[str, str, Conversation, str, _MessageBody], Awaitable[None]]
RecordMention = Callable[..., None]


def register_chat_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    turn_engine: ChatTurnEngine,
    activity: ActivityLog,
    get_user_id: Callable[[], str],
    workflow_orchestrator: WorkflowOrchestrator,
    ingestor: Ingestor,
    insights_collection_id: str,
    kb_chat_pipeline: object | None,
    send_into: SendInto,
    record_mention: RecordMention,
) -> None:
    """Mount the RCA workspace + multi-chat routes onto ``app``."""
    conv_rm = spec.get_resource_manager(Conversation)

    @app.post(
        "/a/{slug}/items/{item_id}/messages",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def send_message(slug: str, item_id: str, body: _MessageBody) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        # Item-level (no chat_id) → the implicit default chat, keyed on item_id so the
        # workflow drive path + file-change broadcasts share its stream (manual §3).
        rid, conv = locator.conversation_for(investigation_id)
        await send_into(investigation_id, rid, conv, investigation_id, body)
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @app.get("/a/{slug}/items/{item_id}/stream")
    async def stream_investigation(slug: str, item_id: str) -> StreamingResponse:
        """#43: the shared per-investigation event stream. Every viewer subscribes
        here and sees all turns live (whoever sent them) + human messages +
        file-changed notices. Live-only — past messages load from the
        conversation resource."""
        investigation_id = locator.require_item(slug, item_id)
        # subscribe_sse() registers the subscriber NOW (eagerly), so a turn that
        # starts between connect and first body-pull isn't missed.
        return StreamingResponse(
            turn_engine.subscribe_sse(investigation_id), media_type="text/event-stream"
        )

    @app.delete(
        "/a/{slug}/items/{item_id}/messages/current",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_message(slug: str, item_id: str) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        # #43 Stop: anyone may interrupt the in-flight turn; the queue keeps
        # draining (queued messages from others are not dropped).
        await turn_engine.cancel_current(investigation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/a/{slug}/items/{item_id}/messages")
    async def undo_turns(
        slug: str,
        item_id: str,
        turns: int = Query(..., ge=1, description="How many whole turns to undo (≥1)."),
    ) -> _UndoOut:
        """Undo the last `turns` whole turns (issue #38). A turn is the
        user's prompt plus everything the agent produced for it; undoing
        removes them as a unit (no orphan tool/assistant left behind) so
        the next turn's history no longer sees them. The workspace FILES
        are NOT reverted — undo edits the conversation only."""
        investigation_id = locator.require_item(slug, item_id)
        rid, conv = locator.conversation_for(investigation_id)
        cut = undo_cut_index(conv.messages, turns)
        removed = len(conv.messages) - cut
        conv.messages = conv.messages[:cut]
        conv_rm.update(rid, conv)
        activity.record(
            "turns_undone",
            f"Undid {turns} turn(s)",
            {"investigation_id": investigation_id, "removed": removed},
        )
        return _UndoOut(message_count=len(conv.messages), removed=removed)

    # ── Multi-chat (topic-hub P7, manual §3) ─────────────────────────────
    # An item holds many chats. These chat-scoped endpoints address one chat by id;
    # the item-level endpoints above keep hitting the implicit default chat.

    @app.get("/a/{slug}/items/{item_id}/chats")
    async def list_chats(slug: str, item_id: str) -> list[_ChatInfo]:
        """List the item's chats (free + workflow), **most-recent-activity first**
        (#132 — no "main chat" privilege). Each workflow chat carries its driving
        run's status; every chat carries a name hint + last-activity stamp. Read-only:
        the implicit default chat materialises on first use, not here."""
        from .chats import _item_chats_query, find_default_conversation

        investigation_id = locator.require_item(slug, item_id)
        default = find_default_conversation(conv_rm, investigation_id)
        default_id = default[0] if default else None
        run_status = item_run_status(spec, investigation_id)
        infos = [
            chat_info_from_resource(r, default_id, run_status)
            for r in conv_rm.list_resources(_item_chats_query(investigation_id))
        ]
        # Most-recent activity first; chat_id breaks ties for a stable order.
        infos.sort(key=lambda c: (-(c.last_activity_ms or 0), c.chat_id))
        return infos

    @app.post("/a/{slug}/items/{item_id}/chats", status_code=status.HTTP_201_CREATED)
    async def create_chat(slug: str, item_id: str, body: _CreateChatBody) -> _ChatInfo:
        """Open a new FREE chat in the item (manual §3); returns its chat_id. A
        workflow chat is opened by the run endpoint (P8), not here."""
        from .chats import find_default_conversation

        investigation_id = locator.require_item(slug, item_id)
        rev = conv_rm.create(
            Conversation(item_id=investigation_id, title=body.title, created_ms=now_ms())
        )
        default = find_default_conversation(conv_rm, investigation_id)
        return chat_info_from_resource(
            conv_rm.get(rev.resource_id), default[0] if default else None, {}
        )

    @app.patch("/a/{slug}/items/{item_id}/chats/{chat_id}")
    async def rename_chat(
        slug: str, item_id: str, chat_id: str, body: _RenameChatBody
    ) -> _ChatInfo:
        """Rename a chat (#132) — set its display title from the manage modal."""
        from .chats import find_default_conversation

        investigation_id = locator.require_item(slug, item_id)
        rid, conv = locator.require_chat(slug, item_id, chat_id)
        conv.title = body.title
        conv_rm.update(rid, conv)
        default = find_default_conversation(conv_rm, investigation_id)
        return chat_info_from_resource(
            conv_rm.get(rid),
            default[0] if default else None,
            item_run_status(spec, investigation_id),
        )

    @app.delete("/a/{slug}/items/{item_id}/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_chat(slug: str, item_id: str, chat_id: str) -> Response:
        """Delete a chat (#132). A workflow chat's driving run is **cancelled first**
        (delete also cancels the run); then any in-flight turn / SSE is torn down and
        the conversation removed. Idempotent cancel — a no-op for a terminal run."""
        investigation_id = locator.require_item(slug, item_id)
        rid, conv = locator.require_chat(slug, item_id, chat_id)
        if conv.run_id:
            await workflow_orchestrator.cancel(conv.run_id, investigation_id)
        turn_engine.forget(locator.engine_key(investigation_id, rid))
        conv_rm.delete(rid)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/messages",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def send_chat_message(
        slug: str, item_id: str, chat_id: str, body: _MessageBody
    ) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        rid, conv = locator.require_chat(slug, item_id, chat_id)
        await send_into(
            investigation_id, rid, conv, locator.engine_key(investigation_id, rid), body
        )
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @app.get("/a/{slug}/items/{item_id}/chats/{chat_id}/stream")
    async def stream_chat(slug: str, item_id: str, chat_id: str) -> StreamingResponse:
        """The chat's own live event stream (manual §3) — per-chat, unlike the
        item-level stream which carries the default chat + item-wide events."""
        investigation_id = locator.require_item(slug, item_id)
        locator.require_chat(slug, item_id, chat_id)
        return StreamingResponse(
            turn_engine.subscribe_sse(locator.engine_key(investigation_id, chat_id)),
            media_type="text/event-stream",
        )

    @app.delete(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/messages/current",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_chat_message(slug: str, item_id: str, chat_id: str) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        locator.require_chat(slug, item_id, chat_id)
        await turn_engine.cancel_current(locator.engine_key(investigation_id, chat_id))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/a/{slug}/items/{item_id}/chats/{chat_id}/messages")
    async def undo_chat_turns(
        slug: str,
        item_id: str,
        chat_id: str,
        turns: int = Query(..., ge=1, description="How many whole turns to undo (≥1)."),
    ) -> _UndoOut:
        """Undo the last `turns` whole turns of ONE chat (manual §3 + issue #38),
        the chat-scoped twin of `undo_turns`. A turn is the user's prompt plus
        everything the agent produced for it; undoing removes them as a unit. The
        workspace FILES are NOT reverted — undo edits the conversation only."""
        locator.require_item(slug, item_id)
        rid, conv = locator.require_chat(slug, item_id, chat_id)
        cut = undo_cut_index(conv.messages, turns)
        removed = len(conv.messages) - cut
        conv.messages = conv.messages[:cut]
        conv_rm.update(rid, conv)
        return _UndoOut(message_count=len(conv.messages), removed=removed)

    @app.post(
        "/a/{slug}/items/{item_id}/mentions",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def mention_users(slug: str, item_id: str, body: _MentionBody) -> Response:
        """@-mention people in the chat — a pure "come look" summon (does NOT
        run the agent): records a mention entry + notifies each user."""
        investigation_id = locator.require_item(slug, item_id)
        title = locator.title_of(investigation_id)
        if title is None:
            raise HTTPException(status_code=404, detail=f"unknown item: {investigation_id!r}")
        me = get_user_id()
        record_mention(investigation_id, title, body.user_ids, body.note, actor=me, author=me)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/a/{slug}/items/{item_id}/promote-to-kb")
    async def promote_to_kb(slug: str, item_id: str) -> dict[str, list[str]]:
        """Manual trigger for chat → knowledge insight extraction. Runs
        synchronously (FE shows a spinner) and returns the SourceDoc ids
        written. `[]` when the chat had no extractable insights, the LLM
        failed, or no chat pipeline is wired (offline / no KB LLM)."""
        investigation_id = locator.require_item(slug, item_id)
        if kb_chat_pipeline is None:
            return {"insight_ids": []}
        title = locator.title_of(investigation_id)
        if title is None:
            raise HTTPException(status_code=404, detail=f"unknown item: {investigation_id!r}")
        _rid, conv = locator.conversation_for(investigation_id)
        ids = await promote_chat_to_kb(
            ingestor=ingestor,
            insights_collection_id=insights_collection_id,
            actor=get_user_id(),
            investigation_id=investigation_id,
            investigation_title=title,
            messages=conv.messages,
        )
        return {"insight_ids": ids}

    @app.get("/a/{slug}/items/{item_id}/export-chat")
    async def export_chat(slug: str, item_id: str) -> Response:
        """Download the conversation in the `.chat.json` round-trip
        format — the KB upload path runs the same insight extraction
        the promote button does on these files (debug / out-of-band
        re-ingestion). The filename guarantees the suffix contract."""
        investigation_id = locator.require_item(slug, item_id)
        from ..kb.chat_export import CHAT_EXPORT_SUFFIX, build_chat_export

        title = locator.title_of(investigation_id)
        if title is None:
            raise HTTPException(status_code=404, detail=f"unknown item: {investigation_id!r}")
        _rid, conv = locator.conversation_for(investigation_id)
        payload = build_chat_export(
            title=title,
            messages=[
                {"role": m.role, "content": m.content, "tool_name": m.tool_name or ""}
                for m in conv.messages
            ],
        )
        filename = f"{investigation_id}{CHAT_EXPORT_SUFFIX}"
        return Response(
            content=payload,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
