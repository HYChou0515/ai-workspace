"""RCA turn driver (#54) — the interactive workspace/chat send path.

Extracted from ``create_app``'s ``_send_into`` closure: append the user message
to a conversation, build the RCA turn context from ITS history, and enqueue the
turn on the chat engine. Shared by the item-level and chat-scoped message
endpoints (wired into ``register_chat_routes`` as ``send_into``).

The closure became ``ChatSendService.send`` with its create_app-local helpers
turned into constructor-injected deps: the sub-agent bridge (``_run_subagent``),
the item locator (``_resolve_agent_config`` / ``_app_context_files``), the turn
context builder, and the file/user/activity/engine services. The two nested
per-turn closures (``_run_subagent_with_depth`` and ``persist``) stay nested —
they close over this turn's body/enhancements/collection scope and the delicate
citation-bubbling logic.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from ..agent.context import KbSearchBudget
from ..kb.collections import (
    collection_ids_from_json,
    collection_tiers_from_json,
    read_hub_collections,
    resolve_named_collection_ids,
)
from ..resources import Conversation, Message
from ..sandbox.protocol import OutputSink
from .events import UserMessage
from .kb_chat_routes import resolve_max_searches, to_caller_enhancements
from .rca_messages import bubble_kb_citations, to_rca_message
from .timeutil import now_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from specstar import SpecStar

    from ..files import WorkspaceFiles
    from ..filestore.protocol import FileStore
    from ..kb.retriever import Enhancements
    from ..resources.kb import Citation
    from ..users import UserDirectory
    from .activity import ActivityLog
    from .locator import ItemLocator
    from .schemas import _MessageBody
    from .subagent_bridge import SubagentBridge
    from .turn_context import TurnContextBuilder
    from .turns import ChatTurnEngine, TurnMessage


class ChatSendService:
    """Drive an RCA turn for a workspace/chat message: persist the user message,
    build the turn context from the conversation's history, and enqueue it on the
    chat engine. The injected deps replace the create_app closures it captured."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        locator: ItemLocator,
        turn_ctx: TurnContextBuilder,
        subagent_bridge: SubagentBridge,
        filestore: FileStore,
        files: WorkspaceFiles,
        users: UserDirectory,
        activity: ActivityLog,
        turn_engine: ChatTurnEngine,
        get_user_id: Callable[[], str],
        infer_modules_collection: str,
        infer_modules_enhancements: Enhancements | None,
        infer_modules_reasoning_effort: str | None,
        kb_max_searches_per_turn: int | None = None,
        kb_max_searches_ceiling: int = 10,
        flush_item: Callable[[str], Awaitable[None]],
        send_await_timeout: float = 25.0,
    ) -> None:
        self._spec = spec
        self._locator = locator
        self._turn_ctx = turn_ctx
        self._subagent_bridge = subagent_bridge
        self._filestore = filestore
        self._files = files
        self._users = users
        self._activity = activity
        self._turn_engine = turn_engine
        self._get_user_id = get_user_id
        self._infer_modules_collection = infer_modules_collection
        self._infer_modules_enhancements = infer_modules_enhancements
        self._infer_modules_reasoning_effort = infer_modules_reasoning_effort
        self._kb_max_searches_per_turn = kb_max_searches_per_turn
        self._kb_max_searches_ceiling = kb_max_searches_ceiling
        # #492: flush this item's live sandbox to durable at turn-end (guarantee
        # (2)'s Y=1 turn) — a no-op when the item is cold.
        self._flush_item = flush_item
        # #493 symptom 1 (504): how long the POST awaits its own turn before
        # DETACHING it to the background. Snappy turns finish within this and the
        # POST returns after the reply is persisted (the historical behaviour every
        # test + the instant-reply UX rely on); a long agent turn detaches and the
        # POST returns 202 well before an ingress `proxy-read-timeout` (default 60s)
        # would 504 it — the turn keeps running on the engine's worker and the
        # client watches the live SSE stream, refetching the thread on `done`.
        self._send_await_timeout = send_await_timeout
        self._conv_rm = spec.get_resource_manager(Conversation)

    async def send(
        self,
        investigation_id: str,
        rid: str,
        conv: Conversation,
        engine_key: str,
        body: _MessageBody,
    ) -> None:
        """Append the user message to conversation ``rid``, build the RCA turn ctx
        from ITS history, and enqueue the turn on ``engine_key`` (item_id for the
        default chat, the chat_id otherwise — manual §3). Shared by the item-level
        and chat-scoped message endpoints."""
        # #43: stamp the sender so a shared workspace's chat shows who said what,
        # and broadcast the message to live viewers (below, before the turn runs).
        author = self._get_user_id()
        created = now_ms()
        conv.messages.append(
            Message(role="user", content=body.content, author=author, created_at=created)
        )
        self._conv_rm.update(rid, conv)

        # Topic Hub §5/§7 + #280: the item's collection set (collections.json),
        # read ONCE — the flat union scopes the turn's deterministic glossary /
        # resolve_collection; the rank-ordered tiers drive ask_knowledge_base's
        # priority fallback. Both empty for Apps without the file.
        hub_data = await read_hub_collections(self._filestore, investigation_id)
        hub_collection_ids = collection_ids_from_json(hub_data)
        hub_collection_tiers = collection_tiers_from_json(hub_data)
        # Composer knowledge-search depth: applies to this turn's KB
        # lookups. The bridge wrapper forwards it to the kb_chat
        # sub-agent only — infer_modules' focused classification probe
        # keeps the operator defaults.
        caller_enh = to_caller_enhancements(body.enhancements)
        # The composer's "Search the wiki" toggle — a routing flag (not a depth
        # knob), so it's read off the body separately and only applies to the
        # kb_chat sub-agent (infer_modules stays chunk-only).
        caller_wiki = bool(body.enhancements and body.enhancements.wiki)
        # #66: resolve infer_modules' configured collection NAME → ids ONCE for
        # this whole turn (not per step). "" ⇒ None ⇒ the bridge searches all
        # collections (backward-compatible). A configured-but-missing name → []
        # ⇒ kb_search finds nothing and the classifier falls back to taxonomy.
        infer_coll_ids = resolve_named_collection_ids(self._spec, self._infer_modules_collection)
        # #334 Q6: ONE kb_search budget for the WHOLE turn, shared by every
        # ask_knowledge_base call below — the composer's per-message pick (clamped
        # to [0, ceiling]) or, absent one, the operator default. infer_modules is
        # NOT scoped by it (it keeps the operator default, a focused classifier).
        kb_budget = KbSearchBudget(
            max_calls=resolve_max_searches(
                body.max_kb_searches,
                default=self._kb_max_searches_per_turn,
                ceiling=self._kb_max_searches_ceiling,
            )
        )

        async def _run_subagent_with_depth(
            purpose: str,
            payload: str,
            emit: OutputSink | None = None,
            origin_id: str | None = None,
            collection_ids: list[str] | None = None,
        ) -> tuple[str, list[Citation]]:
            # kb_chat uses the COMPOSER's live depth + effort (#65); infer_modules
            # uses its OWN configured depth + effort + a single configured
            # collection (#66, a focused classifier).
            #
            # #280: for kb_chat, the caller (ask_knowledge_base, after resolving
            # its `rank` → a priority tier) passes the tier's `collection_ids`;
            # `None` ⇒ no tier scoping ⇒ search the whole KB (today's behaviour).
            # #334 Q6: only kb_chat (ask_knowledge_base) draws from the turn's
            # shared budget; infer_modules keeps the operator default (bud=None ⇒
            # the bridge seeds a fresh budget from its own max_searches).
            if purpose == "kb_chat":
                enh = caller_enh
                reff = body.reasoning_effort
                wiki = caller_wiki
                colls = collection_ids
                bud = kb_budget
            elif purpose == "infer_modules":
                enh, reff, wiki = (
                    self._infer_modules_enhancements,
                    self._infer_modules_reasoning_effort,
                    False,
                )
                colls = infer_coll_ids
                bud = None
            else:  # pragma: no cover
                enh, reff, wiki, colls, bud = None, None, False, None, None
            return await self._subagent_bridge.run(
                purpose,
                payload,
                emit,
                origin_id,
                enhancements=enh,
                reasoning_effort=reff,
                wiki_query=wiki,
                collection_ids=colls,
                budget=bud,
            )

        # ONE bridge for every sub-agent the RCA tools may invoke
        # (ask_knowledge_base, infer_modules, future ones) drives the turn with the
        # investigation's attached agent + the composer's per-turn depth/effort/scope.
        ctx = await self._turn_ctx.build_chat_turn(
            investigation_id,
            agent_config=self._locator.resolve_agent_config(investigation_id),
            run_subagent=_run_subagent_with_depth,
            # Cross-turn memory: prior dialogue (excludes the user msg just added).
            history_messages=conv.messages[:-1],
            reasoning_effort=body.reasoning_effort,
            kb_enhancements=caller_enh,
            collection_ids=hub_collection_ids,
            collection_tiers=hub_collection_tiers,
            acting_user=author,
            speaker=self._users.get(author),
            # #380: skills applied THIS turn — so read_skill exempts them from the
            # disable gate (their bodies are already preloaded into the prompt).
            apply_skills=body.apply_skills or [],
        )

        def persist(produced: list[TurnMessage]) -> None:
            # Persist the agent's reply + tool outputs so re-entering the
            # workspace shows them, not just the user's own messages.
            if produced:
                conv2 = self._conv_rm.get(rid).data  # re-fetch THIS chat (not the default)
                assert isinstance(conv2, Conversation)
                # Citations live on `ctx.subagent_citations` — a dict
                # keyed by TOOL NAME (the surface that produced them).
                # Per name, lists are in CALL ORDER, so we keep one
                # cursor per name and pair the Nth bucket entry with
                # the Nth tool message bearing that name. Assistant
                # messages that quote `[N]` bubble against the shared
                # seen-so-far pool (most-recent call wins for marker
                # collisions), so a `[3]` after both an ask_kb call AND
                # an infer_modules call resolves to whichever of them
                # surfaced marker 3 most recently. Tool messages without
                # any stashed citations keep `citations=[]`.
                tool_idx: dict[str, int] = {}
                seen_subagent: list[list[Citation]] = []
                for tm in produced:
                    msg = to_rca_message(tm)
                    name = tm.tool_name
                    pool = ctx.subagent_citations.get(name) if name is not None else None
                    if pool is not None and name is not None:
                        idx = tool_idx.get(name, 0)
                        if idx < len(pool):
                            msg.citations = list(pool[idx])
                            seen_subagent.append(pool[idx])
                        tool_idx[name] = idx + 1
                    elif tm.role == "assistant" and seen_subagent:
                        msg.citations = bubble_kb_citations(tm.content, seen_subagent)
                    conv2.messages.append(msg)
                self._conv_rm.update(rid, conv2)
            self._activity.record(
                "agent_turn_complete",
                "Agent finished a turn",
                {"investigation_id": investigation_id},
            )

        # Topic Hub §6: prepend the App's context_files (e.g. MEMORY.md +
        # collections.json) as a labelled, authoritative block — re-derived fresh from
        # the live FileStore each turn and handed ONLY to the agent. The persisted user
        # message + the broadcast UserMessage stay clean (block never enters history),
        # so it is idempotent + replay-safe. "" for Apps that declare no context_files.
        from ..apps.context_files import build_context_block
        from ..apps.skills import build_applied_skills_block, build_workspace_skills_block

        block = await build_context_block(
            self._filestore, investigation_id, self._locator.context_files(investigation_id)
        )
        # #298: advertise the skills the user co-created in THIS workspace, read
        # live each turn (through the same file facade the agent writes with, so a
        # skill saved last turn shows up now). Injected like context_files —
        # never persisted into history.
        skills_block = await build_workspace_skills_block(
            self._files, investigation_id, self._locator.skill_prefs_of(investigation_id)
        )
        # #380: skills the user picked to APPLY this turn — hard-preload each body
        # so the model applies it without a read_skill round-trip. One-shot: built
        # from the per-message `apply_skills`, injected like the blocks above, never
        # persisted. Overrides a disabled toggle (resolve_skill_body ignores prefs).
        applied_block = (
            await build_applied_skills_block(
                self._files,
                investigation_id,
                self._locator.slug_of(investigation_id),
                self._locator.profile_of(investigation_id),
                body.apply_skills,
            )
            if body.apply_skills
            else ""
        )
        prefix = "\n\n".join(p for p in (block, skills_block, applied_block) if p)
        turn_content = f"{prefix}\n\n{body.content}" if prefix else body.content

        # #43: broadcast the human's message to every live viewer, then queue the
        # turn and await ITS completion. The queue serializes concurrent users on
        # the shared sandbox/files (a new message no longer cancels a running
        # turn — Stop does). Live turn events reach all viewers via GET .../stream
        # (item-level / default chat) or the chat-scoped stream (other chats).
        self._turn_engine.publish(
            engine_key,
            UserMessage(author=author, content=body.content, created_at=created),
        )
        # #492: flush the item's live sandbox to durable when THIS turn ends, so
        # durable lags by at most one turn (guarantee (2)). Runs on the engine's
        # worker, off this POST's back; a flush failure never fails the turn.
        fut = self._turn_engine.enqueue(
            engine_key,
            turn_content,
            ctx,
            on_complete=persist,
            on_turn_end=lambda: self._flush_item(investigation_id),
        )
        # #493 symptom 1 (504): await THIS turn's completion, but only up to a
        # deadline — then DETACH it so a long turn can't hang the POST until the
        # ingress `proxy-read-timeout` fires a 504. `shield` keeps the turn's
        # completion future alive across our timeout (the worker still resolves it
        # via `fut.set_result`), so a detach is not a cancel. Fast turns resolve
        # `fut` well within the deadline → the POST returns after the reply is
        # persisted, exactly as before; slow turns run on in the background and the
        # client follows the live SSE stream.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(fut), timeout=self._send_await_timeout)
