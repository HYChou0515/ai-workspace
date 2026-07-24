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
from ..context_budget import estimate_tokens
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
        tool_output_max_chars: int,
        exec_output_max_chars: int,
        infer_modules_parallelism: int,
        history_max_messages: int,
        history_max_context_tokens: int,
        context_limit: int | None = None,
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
        self._tool_output_max_chars = tool_output_max_chars
        self._exec_output_max_chars = exec_output_max_chars
        self._infer_modules_parallelism = infer_modules_parallelism
        self._history_max_messages = history_max_messages
        self._history_max_context_tokens = history_max_context_tokens
        # #624: the operator's declared ceiling for this deploy's endpoint. None ⇒
        # resolve per turn (catalog lookup), and `unknown` ⇒ do not trim at all.
        self._context_limit = context_limit
        self._wiki_coordinator = wiki_coordinator
        # #429 P10: the event-dispatch sink stamped onto every agent turn's ctx so an
        # agent's entity write fires on_event workflows. Set after construction by the
        # composition root (the EventTriggerDispatcher is built later than this builder,
        # so it can't be a constructor arg — mirrors orchestrator.entity_write_sink).
        # None ⇒ no dispatch wired (tests / deployments with no triggers pay nothing).
        self.entity_write_sink: EntityWriteSink | None = None
        # #624: reads what the RUNNER has learned about each endpoint's real
        # ceiling (from the limits its rejections stated). Set after construction
        # by the composition root — the runner is injected, and a scripted runner
        # (tests, replay) has nothing to learn from. None ⇒ the ladder simply
        # skips that rung.
        self.learned_limit_fn: Callable[[str, str | None], int | None] | None = None

    def _overhead_for(self, agent_config: AgentConfig | None, item_id: str) -> int:
        """Tokens spent before any history: the system prompt + tool schemas."""
        if agent_config is None:
            return 0
        return estimate_tokens(agent_config.system_prompt or "") + self._tools_tokens(
            agent_config,
            app_slug=self._locator.slug_of(item_id),
            profile=self._locator.profile_of(item_id),
        )

    def _learned_limit(self, agent_config: AgentConfig) -> int | None:
        """What the endpoint stated in a past rejection, via the runner's
        learner. None when nothing has been learned (or no runner is wired —
        tests, replay), which simply leaves the ladder to its other rungs."""
        fn = self.learned_limit_fn
        if fn is None:
            return None
        try:
            return fn(agent_config.model, agent_config.llm_base_url or None)
        except Exception:  # noqa: BLE001 — a cache read must not break a turn
            return None

    def _budget_for(
        self,
        agent_config: AgentConfig | None,
        *,
        app_slug: str | None = None,
        profile: str | None = None,
    ) -> int | None:
        """Tokens left for replayed history on this turn, or ``None`` for "no
        ceiling known — do not trim" (#624).

        ``None`` and ``0`` are deliberately different answers: ``None`` means we
        do not know the ceiling and must not amputate on a guess, while ``0``
        means we DO know it and the prompt alone already fills it — there is
        genuinely no room for history. Collapsing them (both "falsy") makes the
        second case silently behave like the first, which is the opposite of
        what it needs.

        The ceiling is resolved per turn because it belongs to the *endpoint*
        this config points at: an operator override first, else the model
        registry. A self-hosted model behind an OpenAI-compatible endpoint is in
        no registry, so `unknown` is the expected answer there — and `unknown`
        must mean "send it all", never "fall back to some number", which is how
        24,000 came to govern a window nobody had measured.

        The overhead subtracted is real, not assumed: the system prompt (which
        since #480 carries every tool's documentation) plus the tool schemas that
        ride alongside it. The old budget could see neither, so an 18.5k-token
        prompt and a 24k history budget were aimed at a 40,960-token model.
        """
        from ..context_budget import (
            catalog_limit,
            estimate_tokens,
            history_budget,
            resolve_context_limit,
        )

        if agent_config is None:
            return None
        limit = resolve_context_limit(
            configured=self._context_limit,
            # #624: what the endpoint told us in a past rejection. Wired to the
            # runner's learner — the adversarial review caught this as a dangling
            # `learned=None  # P3 feeds this` comment that nothing ever fed.
            learned=self._learned_limit(agent_config),
            catalog=catalog_limit(agent_config.model),
        )
        overhead = estimate_tokens(agent_config.system_prompt or "")
        overhead += self._tools_tokens(agent_config, app_slug=app_slug, profile=profile)
        budget = history_budget(limit, overhead_tokens=overhead)
        if budget is None:
            return None
        # `_fit_token_budget` always keeps the newest message (dropping the turn's
        # own context is worse than a slight overflow), so a floor of 1 expresses
        # "no room for history" without colliding with 0 = "budget disabled".
        return max(1, budget)

    def _tools_tokens(
        self, agent_config: AgentConfig, *, app_slug: str | None, profile: str | None
    ) -> int:
        """Estimated cost of the tool schemas sent alongside the prompt. Built
        per turn (~12 ms) rather than guessed — a guess here is the same class of
        defect as the constant it replaces. Any failure degrades to 0 rather than
        breaking the turn."""
        import json

        from ..agent import build_tools
        from ..context_budget import estimate_tokens

        try:
            # NOT `allowed_tools or None` — `[]` is an explicit "no tools", and
            # that alias turns it into "use the workspace defaults". `_agent_for`
            # carries a ten-line comment about the misconfig it caused; sizing
            # must measure the SAME tool set the runner will actually send.
            tools = build_tools(
                agent_config.allowed_tools,
                app_slug=app_slug,
                profile=profile,
            )
            payload = json.dumps(
                [
                    {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.params_json_schema,
                    }
                    for t in tools
                ]
            )
        except Exception:  # noqa: BLE001 — sizing must never break a turn
            return 0
        return estimate_tokens(payload)

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
        # #624: capture whether history had to be cut, so the send path can say so
        # in the thread. The cut used to be unspeakable — nothing recorded it.
        cut: list[int] = []
        said: list[str] = []
        history = history_items(
            history_messages,
            max_messages=self._history_max_messages,
            # The token ceiling is DERIVED from what this endpoint can actually
            # take, minus what the prompt + tool schemas already spend — not a
            # constant. An unknown ceiling yields 0 ⇒ no trim (we send it all and
            # learn the real limit from the response), unless an operator has set
            # the legacy manual cap.
            max_tokens=(
                derived
                if (
                    derived := self._budget_for(
                        agent_config,
                        app_slug=self._locator.slug_of(item_id),
                        profile=self._locator.profile_of(item_id),
                    )
                )
                is not None
                else self._history_max_context_tokens
            ),
            users=self._users,
            on_trim=cut.append,
            on_reduce=said.append,
        )
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
            tool_output_max_chars=self._tool_output_max_chars,
            exec_output_max_chars=self._exec_output_max_chars,
            history=history,
            history_trimmed=cut[0] if cut else 0,
            history_reduced_note=said[0] if said else "",
            context_overhead_tokens=self._overhead_for(agent_config, item_id),
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
        conversation_id: str | None = None,
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
            # #613: which chat thread this turn belongs to — the update_todos
            # tool's row key. Chat turns only; build_workflow_turn never sets it
            # (workflow runs have their own progress UI), so on workflow turns
            # the tool reports itself unavailable.
            conversation_id=conversation_id,
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
