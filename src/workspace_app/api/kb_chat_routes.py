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

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

import msgspec
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..agent.context import AgentToolContext
from ..kb.citations import parse_citations
from ..kb.cited import record_citations
from ..kb.context_cards import build_vocab, card_context_block, cards_for_collections
from ..kb.context_cards import match as match_cards
from ..kb.retriever import Enhancements, Retriever
from ..resources import AgentConfig
from ..resources.kb import Citation, KbChat, KbMessage
from .events import AgentEvent, MessageDelta, RunError, ToolEnd, ToolLog, ToolStart
from .notifications import notify
from .runner import AgentRunner
from .turns import ChatTurnEngine, TurnMessage, history_items


def kb_progress(ev: AgentEvent) -> str | None:
    """Render a KB sub-agent event as a one-line progress note for the parent
    (RCA) stream, so the user sees the KB agent's searches and reasoning live
    while `ask_knowledge_base` runs. ``None`` ⇒ nothing worth surfacing."""
    if isinstance(ev, ToolStart):
        query = ev.args.get("query")
        return f"🔎 {ev.name}: {query}\n" if query else f"🔎 {ev.name}\n"
    if isinstance(ev, ToolLog):
        # The kb_search tool's live output — e.g. the retriever's enhancement-LLM
        # thinking (multi-query / HyDE / rerank) — relayed under the parent's
        # ask_knowledge_base card (issue #10).
        return ev.text
    if isinstance(ev, MessageDelta) and ev.reasoning:
        return ev.text
    return None


async def answer_question(
    runner: AgentRunner,
    retriever: Retriever,
    collection_ids: list[str],
    question: str,
    *,
    agent_config: AgentConfig,
    spec: SpecStar | None = None,
    enhancements: Enhancements | None = None,
    reasoning_effort: str | None = None,
    wiki: bool = False,
    on_event: Callable[[AgentEvent], None] | None = None,
    on_citations: Callable[[list[Citation]], None] | None = None,
) -> str:
    """Run one KB-agent turn to completion (no streaming) and return its answer
    with a compact sources footer. This is how the RCA agent's
    `ask_knowledge_base` tool consults the KB — a synthesized, cited reply
    rather than raw passages.

    `agent_config` is the resolved KB AgentConfig (built by the catalog from
    `agents.kb_chat` in config.yaml — Q4 named-preset path). `on_event` (when
    given) is fired for every KB event as it happens, so a caller can surface
    the sub-agent's intermediate work (e.g. relay it into the parent stream).
    `on_citations` (when given) receives the resolved citations so the caller
    can log them (this path doesn't persist a KbMessage). The return value is
    unchanged.

    `wiki` opts the lookup into the LLM-wiki path (the caller passes a
    wiki-aware runner) — the RCA composer's "Search the wiki" toggle forwarded
    over the bridge."""
    ctx = AgentToolContext(
        retriever=retriever,
        collection_ids=collection_ids,
        agent_config=agent_config,
        # specstar handle so a kb_chat agent granted `lookup_glossary` can read
        # context cards on the bridge path too (RCA → ask_knowledge_base → KB
        # sub-agent). None when the caller can't supply one (degrades to the
        # tool's "needs a collection-scoped context" note, not a crash).
        spec=spec,
        # Caller's knowledge-search depth (e.g. the RCA composer's pick,
        # forwarded over the bridge) — kb_search's cascade consumes it.
        kb_enhancements=enhancements,
        # Caller's reasoning effort (#65) — the composer's effort pick rides
        # the bridge so the KB sub-agent thinks at the depth the user chose,
        # not its config default. None ⇒ the model/config default.
        reasoning_effort=reasoning_effort,
        # Route through the wiki/both path when the caller (a wiki-aware runner)
        # opted in. Harmless when the runner isn't wiki-aware.
        wiki_query=wiki,
    )
    parts: list[str] = []
    # (tool_name, error_text) pairs captured from ToolEnd events whose
    # output looks like the agents-SDK default error wrapper. When ALL
    # of the sub-agent's tool calls failed this way, the LLM tends to
    # synthesize a polite "I can't access the KB" recovery sentence that
    # masks the real failure. We surface the errors verbatim instead so
    # the operator's log and the RCA agent's tool-result message both
    # show the root cause.
    tool_errors: list[tuple[str, str]] = []
    pending_tools: dict[str, str] = {}  # call_id → tool name
    run_error: str | None = None
    async for ev in runner.run(question, ctx):
        if on_event is not None:
            on_event(ev)
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            parts.append(ev.text)
        elif isinstance(ev, ToolStart):
            pending_tools[ev.call_id] = ev.name
        elif isinstance(ev, ToolEnd) and _looks_like_tool_error(ev.output):
            tool_name = pending_tools.pop(ev.call_id, "tool")
            tool_errors.append((tool_name, ev.output))
            _LOGGER.error("KB sub-agent tool %r returned error: %s", tool_name, ev.output)
        elif isinstance(ev, RunError):
            # The runner exhausted its retry budget. Don't return
            # whatever partial MessageDelta text leaked before bailing —
            # that's mid-stream LLM output the operator shouldn't have
            # to interpret as an answer.
            run_error = ev.message
            _LOGGER.error("KB sub-agent runner emitted RunError: %s", ev.message)
    if run_error is not None:
        return f"KB sub-agent failed: {run_error}"
    if tool_errors:
        # The LLM's synthesised "I can't access" wording downstream is
        # not what we want to give the RCA caller — return the actual
        # errors. The RCA agent's ask_knowledge_base tool message then
        # surfaces the root cause to both the operator (log + chat) and
        # the LLM (so it can decide what to do, not hallucinate).
        return "KB lookup failed:\n" + "\n".join(f"- {name}: {err}" for name, err in tool_errors)
    answer = "".join(parts)
    cites = parse_citations(answer, ctx.kb_passages)
    if on_citations is not None:
        on_citations(cites)
    if cites:
        footer = "; ".join(f"[{c.marker}] {c.filename}" for c in cites)
        answer = f"{answer}\n\nSources: {footer}"
    return answer


_LOGGER = logging.getLogger(__name__)
# The agents-SDK's `default_tool_error_function` wraps tool exceptions
# into a string starting with this prefix; matching it lets us surface
# real tool failures instead of letting the LLM "recover" them with a
# polite hallucination.
_SDK_TOOL_ERROR_PREFIX = "An error occurred while running the tool"


def _looks_like_tool_error(output: str) -> bool:
    return output.startswith(_SDK_TOOL_ERROR_PREFIX)


class _ChatBody(BaseModel):
    title: str = "New chat"
    collection_ids: list[str] = []


class EnhancementsInput(BaseModel):
    """Per-message enhancement override (Issue #33 follow-up). Any
    field set to a concrete value overrides the operator's retriever
    default for that knob; `None` (or omitted) inherits. The operator's
    `max` clamps every value before the retriever runs."""

    expand: int | None = None
    hyde: int | None = None
    rerank: bool | None = None
    # Issue #50 P6: opt this query into the LLM-wiki path (depth picker's
    # "Search the wiki" advanced toggle). NOT a retriever knob — it routes the
    # turn (chunk / wiki / both), so it's read separately from the three
    # retriever enhancements above (see to_caller_enhancements, which ignores it).
    wiki: bool | None = None


def to_caller_enhancements(body_enh: EnhancementsInput | None):
    """Body override → the retriever's `Enhancements` (None = inherit
    the operator default). Shared by the KB chat turn and the RCA turn
    (whose ask_knowledge_base bridge forwards it to the KB sub-agent).

    Only the three retriever knobs (expand/hyde/rerank) map here; the `wiki`
    routing flag is handled at the turn level, not by the retriever."""
    if body_enh is None:
        return None
    return Enhancements(expand=body_enh.expand, hyde=body_enh.hyde, rerank=body_enh.rerank)


class _MsgBody(BaseModel):
    content: str
    # Per-message reasoning effort from the UI selector; None → model default.
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Per-message enhancement override. `None` (or all-null fields)
    # inherits operator defaults; concrete values are clamped to the
    # operator's `max` (so a FE sending `expand: 99` is safe — the
    # retriever clamps before running). The Mode dropdown / Advanced
    # sliders on the FE side translate to this structured payload;
    # there is no separate `quick` bool any more.
    enhancements: EnhancementsInput | None = None
    # Issue #32: per-message picker — name of the kb_chat entry to use.
    # None / unknown name → first entry (the default). Unknown names
    # 404 the operator's request rather than silently falling back so
    # a typo at the FE picker is loud.
    agent_name: str | None = None


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
    *,
    kb_agent_configs: list[AgentConfig],
    history_max_messages: int = 40,
    history_max_context_tokens: int = 24_000,
) -> None:
    """Register the KB chat surface.

    `kb_agent_configs` (issue #32) is the catalog's `kb_chats()` list —
    every entry shows up in the FE picker; the first is the default.
    """
    if not kb_agent_configs:
        raise ValueError("kb_agent_configs must be non-empty")
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
    async def kb_agent_config_endpoint() -> list[dict]:
        """The KB chat picker (issue #32): every declared KB agent in
        `agents.kb_chat[]` is surfaced as a row of {name, model,
        suggestions}. FE renders a model dropdown over this; the chat UI
        also reads the suggestions chips from the chosen entry. First
        entry is the visible default."""
        return [
            {
                "name": cfg.name,
                "model": cfg.model,
                "description": cfg.description,
                # Suggestion is a msgspec.Struct — FastAPI's default response
                # encoder (Pydantic) doesn't know how to serialise it, so
                # render each entry as a plain ``{label, prompt}`` dict at
                # the boundary. See #91.
                "suggestions": [{"label": s.label, "prompt": s.prompt} for s in cfg.suggestions],
            }
            for cfg in kb_agent_configs
        ]

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
        """Only the current user's chats: ones they own + ones shared with them.
        Two indexed queries (owner = created_by meta; shared_with contains me),
        merged + deduped — not a full scan."""
        me = get_user_id()
        owned = chat_rm.list_resources((QB.created_by() == me).build())
        shared = chat_rm.list_resources((QB["shared_with"].contains(me)).build())
        # owned ∩ shared is empty by construction (the share endpoint forbids
        # the owner being in their own shared_with), so concatenation is enough.
        out: list[dict] = []
        for r in [*owned, *shared]:
            data = r.data
            assert isinstance(data, KbChat)
            out.append(
                {
                    "resource_id": r.info.resource_id,  # ty: ignore[unresolved-attribute]
                    "title": data.title,
                    "collection_ids": data.collection_ids,
                    "message_count": len(data.messages),
                    "owner": r.info.created_by,  # ty: ignore[unresolved-attribute]
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

        # Issue #32: resolve the picker selection. Default = first
        # entry; explicit unknown name → 422 (not a silent fallback).
        if body.agent_name is None:
            agent_config = kb_agent_configs[0]
        else:
            match = next((c for c in kb_agent_configs if c.name == body.agent_name), None)
            if match is None:
                avail = [c.name for c in kb_agent_configs]
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown agent_name={body.agent_name!r}; available: {avail}",
                )
            agent_config = match

        # Resolve the caller-level enhancement override. FE depth
        # picker translates to this structured payload; absence =
        # inherit operator default.
        caller_enh = to_caller_enhancements(body.enhancements)

        ctx = AgentToolContext(
            retriever=retriever,
            collection_ids=chat.collection_ids,
            agent_config=agent_config,
            # specstar handle so the agent's `lookup_glossary` tool (when granted)
            # can read this collection's context cards — deterministic glossary
            # path beside kb_search (unknown term → glossary, question → search).
            spec=spec,
            # Cross-turn memory: prior dialogue (excludes the user msg just added).
            history=history_items(
                chat.messages[:-1],
                max_messages=history_max_messages,
                max_tokens=history_max_context_tokens,
            ),
            # Per-message reasoning effort from the UI selector.
            reasoning_effort=body.reasoning_effort,
            kb_enhancements=caller_enh,
            # Per-query opt-in to the wiki path (depth picker). The
            # WikiAwareRunner gates it on the collections' use_wiki/use_rag.
            wiki_query=bool(body.enhancements and body.enhancements.wiki),
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
                    metrics=m.metrics,
                    error_kind=m.error_kind,  # role=error (#37)
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

        # #106: deterministic context-card pre-scan. Inject any cards whose keys
        # appear in the message so a covered term is answered straight away,
        # without a kb_search round-trip. The persisted user message (above)
        # stays clean — only the content handed to the agent is augmented.
        agent_content = body.content
        if chat.collection_ids:
            cards = cards_for_collections(spec, chat.collection_ids)
            block = card_context_block(match_cards(body.content, build_vocab(cards)))
            if block:
                agent_content = f"{block}\n\n{body.content}"

        return await engine.stream(chat_id, agent_content, ctx, on_complete=persist)

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
        "error_kind": m.error_kind,  # role=error (#37)
        "metrics": (
            {
                "prompt_tokens": m.metrics.prompt_tokens,
                "completion_tokens": m.metrics.completion_tokens,
                "elapsed_ms": m.metrics.elapsed_ms,
            }
            if m.metrics is not None
            else None
        ),
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
