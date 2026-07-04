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
from typing import TYPE_CHECKING, Any, Literal, cast

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

if TYPE_CHECKING:
    # The concrete manager's `using(..., apply_access_scope=True)` (the storage-
    # layer read scope for a hand-written list) isn't on the `IResourceManager`
    # Protocol `get_resource_manager` returns — a specstar type-stub gap. We cast
    # to the concrete type only for that call.
    from specstar.resource_manager.core import ResourceManager

from ..agent.context import AgentToolContext, KbSearchBudget
from ..kb.chat_permission import effective_permission
from ..kb.citations import parse_citations
from ..kb.cited import record_citations
from ..kb.context_cards import build_vocab, card_context_block, cards_for_collections
from ..kb.context_cards import match as match_cards
from ..kb.doc_permission import denied_doc_ids
from ..kb.retriever import Enhancements, Retriever
from ..kb.wiki.coordinator import WikiMaintenanceCoordinator
from ..perm import Actor, Permission, authorize
from ..perm.model import Verb, user_subject
from ..resources import AgentConfig
from ..resources.groups import groups_of
from ..resources.kb import Citation, KbChat, KbMessage
from ..users.protocol import UserDirectory
from .chat_naming import first_user_snippet
from .events import AgentEvent, MessageDelta, RunError, ToolEnd, ToolLog, ToolStart
from .notifications import notify
from .permission_body import PermissionBody, PermissionOut, build_permission, granted_user_ids
from .runner import AgentRunner
from .timeutil import dt_ms
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
    max_searches: int | None = None,
    budget: KbSearchBudget | None = None,
    exclude_doc_ids: frozenset[str] = frozenset(),
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
        # #195/#334: cap the bridge's kb_search calls (same KB agent). A caller
        # that passes a shared `budget` (an app turn spanning several
        # ask_knowledge_base calls, #334 Q6) wins — all its sub-agents then draw
        # from the one budget; otherwise seed a fresh one from `max_searches`.
        kb_search_budget=budget if budget is not None else KbSearchBudget(max_calls=max_searches),
        # #308: the caller (the ask_knowledge_base bridge) resolves which docs the
        # ORIGINAL speaker's per-doc override blocks, so this sub-agent's retriever
        # can't surface a doc the speaker can't read — even though the KB ctx itself
        # carries no speaker identity.
        exclude_doc_ids=exclude_doc_ids,
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
    # #357: unnamed by default ("" not "New chat") so the list's
    # title-or-name_hint fallback surfaces the first user message instead of a
    # generic label. Manual rename sets a real title.
    title: str = ""
    collection_ids: list[str] = []


class KbChatSummary(BaseModel):
    """One KB chat in the list (#357). `name_hint` labels an unnamed chat by its
    first user message and `updated_ms` is the recency-sort key, so the FE renders
    a meaningful, sorted list without fetching each thread."""

    resource_id: str
    title: str
    collection_ids: list[str]
    message_count: int = 0
    owner: str | None = None
    shared_with: list[str] = []
    name_hint: str = ""
    updated_ms: int | None = None


class _RenameBody(BaseModel):
    # #357: manual rename. "" clears the name → the chat drops back to being
    # labelled by its first user message (name_hint).
    title: str


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


def resolve_max_searches(requested: int | None, *, default: int | None, ceiling: int) -> int | None:
    """#334: the composer's per-message kb_search-count pick → the budget cap.

    `None` (the composer sent nothing) inherits the operator `default` (which may
    itself be `None` = unlimited, the pre-#334 behaviour). A concrete pick is
    clamped to ``[0, ceiling]`` — 0 means "don't search this reply" (#334 Q4),
    and the operator's ceiling guards against a runaway request. Shared by the KB
    chat turn and the RCA turn's per-message budget."""
    if requested is None:
        return default
    return max(0, min(requested, ceiling))


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
    # Issue #334: per-message kb_search-count pick from the composer. None →
    # operator default (`kb.max_searches_per_turn`); a concrete value is clamped
    # to [0, kb.max_searches_ceiling] (0 = don't search this reply).
    max_kb_searches: int | None = None


class _ShareBody(BaseModel):
    user_ids: list[str]


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def register_kb_chat_routes(
    app: FastAPI | APIRouter,
    spec: SpecStar,
    engine: ChatTurnEngine,
    retriever: Retriever,
    get_user_id: Callable[[], str],
    users: UserDirectory,
    *,
    kb_agent_configs: list[AgentConfig],
    history_max_messages: int = 40,
    history_max_context_tokens: int = 24_000,
    # #195: per-turn cap on kb_search calls (None ⇒ unlimited). The operator
    # default applied when the composer sends no per-message pick (#334).
    max_searches_per_turn: int | None = None,
    # #334: upper bound a per-message pick (`_MsgBody.max_kb_searches`) may
    # request — the composer's value is clamped to [0, this].
    max_searches_ceiling: int = 10,
    # #397: lets the KB chat's request_wiki_update tool submit a wiki correction.
    wiki_coordinator: WikiMaintenanceCoordinator | None = None,
    # #304: superusers bypass the per-verb chat ACL (must match make_spec's set).
    superusers: frozenset[str] = frozenset(),
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

    def _authorize_chat(chat_id: str, verb: Verb) -> tuple[KbChat, str]:
        """#304 — gate a hand-written chat route. Loads the chat (`_load_rev` 404s
        if missing), then sequences the two checks the auto-CRUD layer composes:
        `read_meta` first — a caller who can't see the chat gets a uniform 404 (no
        existence leak) — then `verb` → 403. Authorizes against the EFFECTIVE
        permission (a pre-#304 row with no `permission` is read from its legacy
        `shared_with`, so `authorize`'s "None ≡ public" never wrongly opens a
        private chat)."""
        chat, owner = _load_rev(chat_id)
        perm = effective_permission(chat.permission, chat.shared_with)
        actor = Actor.human(get_user_id())
        if not authorize(actor, "read_meta", perm, created_by=owner, superusers=superusers):
            raise HTTPException(status_code=404, detail="chat not found")
        if not authorize(actor, verb, perm, created_by=owner, superusers=superusers):
            raise HTTPException(status_code=403, detail=f"not authorized to {verb}")
        return chat, owner

    def _shared_user_ids(chat: KbChat, owner: str) -> list[str]:
        """The concrete users a chat is shared with, for the summary/`shared_with`
        field the FE renders. Derived from the effective permission's read_chat
        grants (so it survives the shared_with→permission migration), minus the
        owner and the `all`/`group:` non-user subjects."""
        perm = effective_permission(chat.permission, chat.shared_with)
        return sorted(
            {u[len("user:") :] for u in perm.read_chat if u.startswith("user:")} - {owner}
        )

    def _summary(r: Any) -> KbChatSummary:
        """Build a list/rename summary row from a fetched chat revision (#357):
        name_hint labels an unnamed chat, updated_ms is the recency-sort key."""
        data = r.data
        assert isinstance(data, KbChat)
        return KbChatSummary(
            resource_id=r.info.resource_id,
            title=data.title,
            collection_ids=data.collection_ids,
            message_count=len(data.messages),
            owner=r.info.created_by,
            shared_with=_shared_user_ids(data, r.info.created_by),
            name_hint=first_user_snippet(data.messages),
            updated_ms=dt_ms(r.info.updated_time),
        )

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
    async def create_chat(body: _ChatBody) -> KbChatSummary:
        # #304: a new chat is PRIVATE (owner-only) by default — unlike a collection,
        # an unshared chat is not open to everyone. Sharing is an explicit later act.
        rev = chat_rm.create(
            KbChat(
                title=body.title,
                collection_ids=body.collection_ids,
                permission=Permission(visibility="private"),
            )
        )
        return KbChatSummary(
            resource_id=rev.resource_id,
            title=body.title,
            collection_ids=body.collection_ids,
            owner=get_user_id(),
        )

    @app.get("/kb/chats")
    async def list_chats() -> list[KbChatSummary]:
        """Only the chats the current user may see — owned, public, restricted-and-
        granted, or (a pre-#304 row) still in the legacy `shared_with`. #304 pushes
        this down to the storage layer: one `access_scope`-applied list, no manual
        owner/shared queries. Each row carries `name_hint` (first user message) so an
        unnamed chat is still tellable apart, and `updated_ms` as the recency-sort
        key (#357)."""
        me = get_user_id()
        scoped = cast("ResourceManager[KbChat]", chat_rm)
        with scoped.using(user=me, apply_access_scope=True) as op:
            rows = op.list_resources()
        return [_summary(r) for r in rows]

    @app.get("/kb/chats/{chat_id}")
    async def get_chat(chat_id: str) -> dict:
        # #304: viewing the full thread (its messages) needs `read_chat` — 404 if
        # the caller can't even see the chat (read_meta), 403 if they can see it but
        # not read the conversation.
        data, owner = _authorize_chat(chat_id, "read_chat")
        return {
            "resource_id": chat_id,
            "title": data.title,
            "collection_ids": data.collection_ids,
            "messages": [_message_dict(m) for m in data.messages],
            "owner": owner,
            "shared_with": _shared_user_ids(data, owner),
            # #357: same fallback label the list uses, so the chat-view header can
            # show a meaningful title for an unnamed thread instead of "Chat".
            "name_hint": first_user_snippet(data.messages),
        }

    @app.patch("/kb/chats/{chat_id}")
    async def rename_chat(chat_id: str, body: _RenameBody) -> KbChatSummary:
        """Rename the thread (#357) — set its display title. "" clears the name so
        the chat drops back to its name_hint label. #304: gated on `write_meta`
        (404 if you can't see it, 403 if you can't write it); the update runs AS THE
        OWNER so the auto-CRUD write handler (which re-checks write_meta) passes for
        a write_meta-granted collaborator who isn't the owner."""
        chat, owner = _authorize_chat(chat_id, "write_meta")
        with chat_rm.using(user=owner) as op:
            op.update(chat_id, msgspec.structs.replace(chat, title=body.title))
        return _summary(chat_rm.get(chat_id))

    @app.delete("/kb/chats/{chat_id}", status_code=204)
    async def delete_chat(chat_id: str) -> Response:
        # #304: deleting a chat is owner-only (a read/write collaborator can't
        # destroy it). read_meta first → 404 hides a chat the caller can't see; then
        # the owner/superuser check → 403.
        _, owner = _authorize_chat(chat_id, "read_meta")
        if owner != get_user_id() and get_user_id() not in superusers:
            raise HTTPException(status_code=403, detail="only the owner can delete this chat")
        chat_rm.permanently_delete(chat_id)
        await engine.forget(chat_id)
        return Response(status_code=204)

    def _write_permission(chat_id: str, chat: KbChat, owner: str, new_perm: Permission) -> None:
        """Persist a chat's new `permission` AS THE OWNER (so the auto-CRUD write
        handler — which re-checks write_meta + change_permission — passes for a
        change_permission-only delegate) and clear the legacy `shared_with` (this
        write migrates a pre-#304 row: its access_scope now runs off `permission`,
        and the `shared_with` fallback clause goes inert)."""
        with chat_rm.using(user=owner) as op:
            op.update(chat_id, msgspec.structs.replace(chat, permission=new_perm, shared_with=[]))

    @app.post("/kb/chats/{chat_id}/share", status_code=204)
    async def share_chat(chat_id: str, body: _ShareBody) -> Response:
        """Share the thread read-only with users → each gets a `read_meta` +
        `read_chat` grant (a viewer, not a sender) and a `share` notification. #304:
        gated on `change_permission` (owner or a change_permission-grantee), and the
        grants live on the chat's `Permission` now, not the legacy `shared_with`."""
        chat, owner = _authorize_chat(chat_id, "change_permission")
        perm = effective_permission(chat.permission, chat.shared_with)
        new = [u for u in body.user_ids if u != owner and user_subject(u) not in perm.read_chat]
        if new:
            subs = [user_subject(u) for u in new]
            # A private chat ignores its grant lists, so sharing must bump it to
            # `restricted` for the new viewers to actually gain access; a public
            # chat is already world-readable, so leave its visibility as-is.
            visibility = "restricted" if perm.visibility == "private" else perm.visibility
            updated = msgspec.structs.replace(
                perm,
                visibility=visibility,
                read_meta=[*perm.read_meta, *(s for s in subs if s not in perm.read_meta)],
                read_chat=[*perm.read_chat, *(s for s in subs if s not in perm.read_chat)],
            )
            _write_permission(chat_id, chat, owner, updated)
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
        """Revoke a user's access entirely — drop them from every grant list
        (read_meta / read_chat / converse). #304: gated on `change_permission`."""
        chat, owner = _authorize_chat(chat_id, "change_permission")
        perm = effective_permission(chat.permission, chat.shared_with)
        subj = user_subject(user_id)
        if any(subj in perm.grants(v) for v in ("read_meta", "read_chat", "converse")):
            updated = msgspec.structs.replace(
                perm,
                read_meta=[s for s in perm.read_meta if s != subj],
                read_chat=[s for s in perm.read_chat if s != subj],
                converse=[s for s in perm.converse if s != subj],
            )
            _write_permission(chat_id, chat, owner, updated)
        return Response(status_code=204)

    @app.put("/kb/chats/{chat_id}/permission")
    async def set_chat_permission(chat_id: str, body: PermissionBody) -> PermissionOut:
        """#304 — set a chat's access control (the FE share UI's backend, #310).
        Only the owner / a superuser / a `change_permission` grantee may call it
        (404 if you can't see it, 403 if you can't change it). Persists the full
        desired state AS THE OWNER (PUT = replace); newly-granted users get a
        `share` notification."""
        chat, owner = _authorize_chat(chat_id, "change_permission")
        before = effective_permission(chat.permission, chat.shared_with)
        new_perm = build_permission(body)
        _write_permission(chat_id, chat, owner, new_perm)
        me = get_user_id()
        notified = sorted(granted_user_ids(new_perm) - granted_user_ids(before) - {me})
        for uid in notified:
            notify(
                spec,
                recipient=uid,
                kind="share",
                title=f'Shared a chat: "{chat.title}"',
                link=f"/kb/chats/{chat_id}",
                actor=me,
            )
        return PermissionOut(resource_id=chat_id, visibility=new_perm.visibility, notified=notified)

    @app.post("/kb/chats/{chat_id}/messages")
    async def send_message(chat_id: str, body: _MsgBody) -> StreamingResponse:
        # #304: sending a message needs `converse` — 404 if you can't see the chat,
        # 403 if you can see/read it but aren't allowed to drive it (a read-only
        # share). The owner always holds converse; a collaborator needs the grant.
        chat, owner = _authorize_chat(chat_id, "converse")
        chat.messages.append(
            KbMessage(role="user", content=body.content, author=get_user_id(), created_at=_now_ms())
        )
        # Persist AS THE OWNER: the write mechanically an `update`, so the auto-CRUD
        # write handler re-checks write_meta — which a converse-only collaborator
        # need not hold (converse was already verified at the route). The message
        # itself records the real sender via `author`.
        with chat_rm.using(user=owner) as op:
            op.update(chat_id, chat)

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
            # #308: exclude docs whose per-doc override blocks THIS speaker's
            # read_content, so the retriever never surfaces a doc tightened away
            # from them (empty when no doc in scope is overridden).
            exclude_doc_ids=denied_doc_ids(
                spec,
                Actor.human(get_user_id(), groups=groups_of(spec, get_user_id())),
                chat.collection_ids,
                "read_content",
                superusers=superusers,
            ),
            agent_config=agent_config,
            # specstar handle so the agent's `lookup_glossary` tool (when granted)
            # can read this collection's context cards — deterministic glossary
            # path beside kb_search (unknown term → glossary, question → search).
            spec=spec,
            # Cross-turn memory: prior dialogue (excludes the user msg just added),
            # each message attributed to its author (#242).
            history=history_items(
                chat.messages[:-1],
                max_messages=history_max_messages,
                max_tokens=history_max_context_tokens,
                users=users,
            ),
            # #242: who the agent is replying to (here, always the owner — shares
            # are read-only). Feeds the per-turn "you are replying to …" note.
            speaker=users.get(get_user_id()),
            # #111/#397: the acting user tools stamp on writes (context cards,
            # wiki corrections).
            acting_user=get_user_id(),
            # #397: the request_wiki_update tool submits a wiki correction through
            # this (None ⇒ the tool reports it's unavailable).
            submit_wiki_correction=(
                wiki_coordinator.submit_correction if wiki_coordinator else None
            ),
            # Per-message reasoning effort from the UI selector.
            reasoning_effort=body.reasoning_effort,
            kb_enhancements=caller_enh,
            # Per-query opt-in to the wiki path (depth picker). The
            # WikiAwareRunner gates it on the collections' use_wiki/use_rag.
            wiki_query=bool(body.enhancements and body.enhancements.wiki),
            # #195/#334: cap how many times this reply may run kb_search — the
            # composer's per-message pick (clamped to [0, ceiling]) or, absent
            # one, the operator default.
            kb_search_budget=KbSearchBudget(
                max_calls=resolve_max_searches(
                    body.max_kb_searches,
                    default=max_searches_per_turn,
                    ceiling=max_searches_ceiling,
                )
            ),
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
                    stopped_reason=m.stopped_reason,  # #113: repetition-stop notice survives reload
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
            # Same owner-acting write as the user-message append above (#304): the
            # assistant reply persists through the write handler as the chat owner.
            with chat_rm.using(user=owner) as op:
                op.update(chat_id, fresh)

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
        "author": m.author,  # #242 — sender id (mirrors RCA Message)
        "reasoning": m.reasoning,
        "tool_name": m.tool_name,
        "tool_args": m.tool_args,
        "tool_call_id": m.tool_call_id,
        "created_at": m.created_at,
        "error_kind": m.error_kind,  # role=error (#37)
        "stopped_reason": m.stopped_reason,  # #113
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
                "provenance": c.provenance,
            }
            for c in m.citations
        ],
    }
