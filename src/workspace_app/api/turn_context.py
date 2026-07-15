"""Turn-context builder (#54) — one place that knows what an RCA turn needs.

Both the interactive workspace/chat send path (`_send_into`) and the workflow
agent-node driver (`_wf_drive_turn`) build the same `AgentToolContext` for an RCA
turn: the same sandbox/filestore/files/sync wiring, the same lazily-woken handle,
the same read-file caps, history window, packages, `read_skill` app/profile, and
the same mention bridge. They differed only in a handful of per-turn extras — and
the two hand-rolled constructions had already drifted apart (the workflow turn
silently omitted `speaker`/`users`/`collection_tiers`/`acting_user`).

This module collapses the shared ~21-field core into ``_common`` and exposes two
named turn shapes:

- ``build_chat_turn`` — the full interactive context (adds the composer's
  reasoning effort + KB enhancements, the item's collection scope/tiers, the
  acting user + resolved speaker + directory, and the infer-modules fan-out).
- ``build_workflow_turn`` — the lean background-node context (the shared core
  only; every interactive extra stays at its ``AgentToolContext`` default, so a
  workflow node sees exactly what it saw before).

A new ctx field is now added once, in ``_common`` or one named method — not copied
into two call sites that can drift.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..agent.context import AgentToolContext
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..sync import SandboxSync
from .turns import history_items

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..entity.events import EntityOrigin, EntityWriteSink
    from ..files import WorkspaceFiles
    from ..filestore.protocol import FileStore
    from ..kb.retriever import Enhancements
    from ..kb.vlm import IVlm, VlmDescriber
    from ..kb.wiki.coordinator import WikiMaintenanceCoordinator
    from ..resources import AgentConfig, Message
    from ..resources.kb import Citation
    from ..tooling.registry import PackageInfo
    from ..users import User, UserDirectory
    from .locator import ItemLocator
    from .registry import InvestigationRegistry

# The sub-agent bridge callable shape (purpose, payload, sink, origin_id, ...).
RunSubagent = Callable[..., Awaitable[tuple[str, "list[Citation]"]]]

logger = logging.getLogger(__name__)


class TurnContextBuilder:
    """Assemble the per-turn ``AgentToolContext`` for an RCA turn, holding the
    app-lifetime service bundle once so the two turn surfaces don't each capture
    it. See the module docstring for why the two shapes exist."""

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        filestore: FileStore,
        files: WorkspaceFiles,
        sync: SandboxSync,
        registry: InvestigationRegistry,
        locator: ItemLocator,
        agent_mention: Callable[[str, list[str], str], None],
        describer: VlmDescriber | None,
        deck_vlm: IVlm | None,
        users: UserDirectory,
        spec: SpecStar,
        packages: list[PackageInfo] | None,
        prebuilt_dir: Path | None,
        read_file_max_lines: int,
        read_file_max_chars: int,
        exec_output_max_chars: int,
        infer_modules_parallelism: int,
        history_max_messages: int,
        history_max_context_tokens: int,
        wiki_coordinator: WikiMaintenanceCoordinator | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._filestore = filestore
        self._files = files
        self._sync = sync
        self._registry = registry
        self._locator = locator
        self._agent_mention = agent_mention
        self._describer = describer
        self._deck_vlm = deck_vlm
        self._users = users
        self._spec = spec
        self._packages = packages
        self._prebuilt_dir = prebuilt_dir
        self._read_file_max_lines = read_file_max_lines
        self._read_file_max_chars = read_file_max_chars
        self._exec_output_max_chars = exec_output_max_chars
        self._infer_modules_parallelism = infer_modules_parallelism
        self._history_max_messages = history_max_messages
        self._history_max_context_tokens = history_max_context_tokens
        self._wiki_coordinator = wiki_coordinator
        # #429 P10: the event-dispatch sink stamped onto every agent turn's ctx so an
        # agent's entity write fires on_event workflows. Set after construction by the
        # composition root (the EventTriggerDispatcher is built later than this builder,
        # so it can't be a constructor arg — mirrors orchestrator.entity_write_sink).
        # None ⇒ no dispatch wired (tests / deployments with no triggers pay nothing).
        self.entity_write_sink: EntityWriteSink | None = None

    def _common(
        self,
        item_id: str,
        session: Any,
        *,
        agent_config: AgentConfig | None,
        run_subagent: RunSubagent,
        history_messages: list[Message],
    ) -> dict[str, Any]:
        """The fields identical across every RCA turn shape (interactive + workflow)."""
        return dict(
            investigation_id=item_id,
            sandbox=self._sandbox,
            filestore=self._filestore,
            files=self._files,
            sync=self._sync,
            sandbox_spec=SandboxSpec(),
            handle=session.handle,
            # Route lazy-create through the registry so session.handle is set
            # (so idle-kill/close_all can find it) and the restore-after-create
            # hook fires. #492 P11: forward the turn's restore-progress sink so a
            # cold-wake restore streams "還原中 N/M" into this turn's stream.
            ensure_sandbox_via=lambda on_progress: self._registry.ensure_handle(
                session, on_progress=on_progress
            ),
            agent_config=agent_config,
            run_subagent=run_subagent,
            mention=self._agent_mention,
            describer=self._describer,
            deck_vlm=self._deck_vlm,
            read_file_max_lines=self._read_file_max_lines,
            read_file_max_chars=self._read_file_max_chars,
            exec_output_max_chars=self._exec_output_max_chars,
            history=history_items(
                history_messages,
                max_messages=self._history_max_messages,
                max_tokens=self._history_max_context_tokens,
                users=self._users,
            ),
            packages=self._packages or [],
            prebuilt_dir=self._prebuilt_dir,
            app_slug=self._locator.slug_of(item_id),
            template_profile=self._locator.profile_of(item_id),
            # #380: the item's tri-state skill override, so read_skill's toggle gate
            # fires live (a skill turned off is unreadable) and the workspace-skill
            # block can drop the disabled ones.
            skill_prefs=self._locator.skill_prefs_of(item_id),
            # #429 P10: the entity tools publish a post-commit write event through this,
            # so an AI-authored entity change fires on_event workflows like any other
            # write. Identical across both turn shapes — the ambient ORIGIN differs (see
            # build_workflow_turn), the sink does not.
            entity_write_sink=self.entity_write_sink,
        )

    async def build_chat_turn(
        self,
        item_id: str,
        *,
        agent_config: AgentConfig | None,
        run_subagent: RunSubagent,
        history_messages: list[Message],
        reasoning_effort: str | None,
        kb_enhancements: Enhancements | None,
        collection_ids: list[str],
        collection_tiers: list[list[str]],
        acting_user: str,
        speaker: User | None,
        apply_skills: list[str] | None = None,
    ) -> AgentToolContext:
        """The full interactive RCA/workspace-chat turn context (`_send_into`)."""
        session = await self._registry.session(item_id)
        logger.debug("turn-context: build chat turn for %s", item_id)
        return AgentToolContext(
            **self._common(
                item_id,
                session,
                agent_config=agent_config,
                run_subagent=run_subagent,
                history_messages=history_messages,
            ),
            # The turn's depth override also rides the ctx so any direct kb tool
            # on the RCA agent applies the same cascade.
            kb_enhancements=kb_enhancements,
            # Per-message reasoning effort from the UI selector.
            reasoning_effort=reasoning_effort,
            # #66: bound the infer_modules tool's per-step classification fan-out.
            infer_modules_parallelism=self._infer_modules_parallelism,
            # Topic Hub §5/§7: spec + the Hub's collection set let the retriever-free
            # `lookup_glossary` / `resolve_collection` tools query context cards.
            spec=self._spec,
            collection_ids=collection_ids,
            # #280: rank-ordered priority tiers the RCA agent walks via
            # ask_knowledge_base(rank). Empty ⇒ no tier fallback.
            collection_tiers=collection_tiers,
            # #111: card create/update agent tools stamp this user on the write.
            acting_user=acting_user,
            # #242: the resolved speaker for the per-turn "who am I replying to" note.
            speaker=speaker,
            # #275: the directory the `lookup_user` tool resolves a handle through.
            users=self._users,
            # #380: skills applied this turn — read_skill exempts them from the
            # disable gate (their bodies are already preloaded into the prompt).
            applied_skills=apply_skills or [],
            # #397: the request_wiki_update tool submits a user's wiki correction
            # through this. Bound to the coordinator when one is wired; None ⇒ the
            # tool reports it's unavailable (it also no-ops for non-wiki scopes).
            submit_wiki_correction=(
                self._wiki_coordinator.submit_correction if self._wiki_coordinator else None
            ),
        )

    async def build_workflow_turn(
        self,
        item_id: str,
        *,
        agent_config: AgentConfig | None,
        run_subagent: RunSubagent,
        history_messages: list[Message],
        entity_write_origin: EntityOrigin | None = None,
    ) -> AgentToolContext:
        """The lean workflow agent-node turn context (`_wf_drive_turn`): the shared
        core only — every interactive extra stays at its ``AgentToolContext`` default,
        byte-for-byte what a workflow node saw before.

        ``entity_write_origin`` (#429 P10) is the running workflow's
        ``EntityOrigin(trigger, depth)`` when it was spawned by a trigger — passed in by
        ``WorkflowExecutor.wire_handle`` from the run's handle — so an agent editing an
        entity mid-run stamps the SAME origin a workflow-handle write would, keeping the
        dispatcher's self-trigger + depth-cap guards effective on the agent path. None
        for a human/schedule run (a first-level write)."""
        session = await self._registry.session(item_id)
        logger.debug("turn-context: build workflow turn for %s", item_id)
        return AgentToolContext(
            **self._common(
                item_id,
                session,
                agent_config=agent_config,
                run_subagent=run_subagent,
                history_messages=history_messages,
            ),
            entity_write_origin=entity_write_origin,
        )
