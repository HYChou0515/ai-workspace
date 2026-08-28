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
from ..apps.manifest import load_app_manifest
from ..apps.subagents import SubagentDef, load_subagents
from ..context_budget import ContextLimit, catalog_limit, estimate_tokens
from ..entity.brief import entity_schema_brief
from ..entity.catalog import discover_catalog
from ..sandbox.protocol import Sandbox, SandboxSpec
from ..sync import SandboxSync
from ..tokens import CallLane
from ..tooling.external import ExternalTools, confine_to_mounted, resolve_external_tools
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
# The generic delegation seam (parent_ctx, defn, prompt, sink) -> the sub-agent's
# report. It takes the parent context rather than closing over one, so the same
# callable serves every turn the composition root built it for.
RunAgent = Callable[..., Awaitable[str]]

logger = logging.getLogger(__name__)


async def resolve_item_tools(sandbox: Sandbox, locator: ItemLocator, item_id: str) -> ExternalTools:
    """#674: what this item's App declares as third-party tools, resolved.

    A module function rather than a builder method because TWO callers need the
    same answer and they are not both turns: a turn (which then confines it to
    what the live sandbox actually mounts), and the registry, for the wakes that
    have no turn at all. Written once, because "which bundles does this item
    get" answered in two places is a rule that eventually disagrees with
    itself — and the way it disagrees is a tool that exists in some sandboxes
    and not others depending on who opened them."""
    slug = locator.slug_of(item_id)
    declared = load_app_manifest(slug).agent.external_tools if slug else {}
    external = await resolve_external_tools(sandbox, declared)
    _record_what_this_item_got(item_id, external)
    return external


def _record_what_this_item_got(item_id: str, external: ExternalTools) -> None:
    """#674 P8 / #724: the trail behind "that tool was behaving oddly".

    A third-party bundle can change under us between one turn and the next —
    the URL points at the author's latest, which is the whole point — so the
    only account of what a given turn actually ran is written at the moment it
    is resolved. Nothing else records it: the sha never appears in a path the
    agent sees, and the manifest is read on a machine the app cannot reach.

    A log line rather than a resource. This is forensics for a question asked
    about the recent past, and a stored row would have to be scoped, listed,
    permissioned and reaped to serve it — see #723 for what a model added for
    one field costs."""
    if not external.provenance:
        return
    logger.info(
        "item %s third-party tools: %s",
        item_id,
        "; ".join(
            f"{name} {p.version}"
            + (f" by {p.author}" if p.author else "")
            + f" sha={external.shas[name][:12]}"
            + (" LAST-KNOWN-GOOD" if p.stale else "")
            for name, p in sorted(external.provenance.items())
        ),
    )


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
        run_agent: RunAgent | None = None,
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
        # How a turn delegates to one of its sub-agents. A property of the
        # deployment (which runner drives the sub-turn), not of the call — so it
        # is wired once here rather than passed per turn shape. None ⇒ no
        # delegation this deploy, and the tool is never built.
        self._run_agent = run_agent
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
        # #624 §9.12: the catalog rung, memoised per model and filled OFF the
        # event loop (see `deferred_lookup`). `_catalog_fn` is a plain attribute
        # rather than a direct call so a test can stand in for the slow, untimed
        # registry lookup — the hazard itself is what needs driving.
        self._catalog_cache: dict[str, int | None] = {}
        self._catalog_fn: Callable[[str], int | None] = catalog_limit

    def _overhead_for(
        self, agent_config: AgentConfig | None, item_id: str, *, has_subagents: bool = False
    ) -> int:
        """Tokens spent before any history: the system prompt + tool schemas."""
        if agent_config is None:
            return 0
        return estimate_tokens(agent_config.system_prompt or "") + self._tools_tokens(
            agent_config,
            app_slug=self._locator.slug_of(item_id),
            profile=self._locator.profile_of(item_id),
            has_subagents=has_subagents,
        )

    def _context_window(self, agent_config: AgentConfig | None) -> ContextLimit:
        """This endpoint's context window, with the source that answered (#624).
        ``.tokens is None`` when nothing could answer.

        Resolved once per turn and used twice: to size the history budget, and —
        for endpoints that open a window on request rather than from the model —
        to tell the endpoint how much to open. Both must read the SAME number, or
        we budget against one window and are served another, which is exactly the
        shape of the silent truncation this resolves against.
        """
        from ..context_budget import deferred_lookup, resolve_context_limit

        if agent_config is None:
            return ContextLimit(tokens=None, source="unknown")
        return resolve_context_limit(
            configured=self._context_limit,
            # #624: what the endpoint told us in a past rejection. Wired to the
            # runner's learner — the adversarial review caught this as a dangling
            # `learned=None  # P3 feeds this` comment that nothing ever fed.
            learned=self._learned_limit(agent_config),
            # #624 §9.12: NOT a table lookup, whatever its name says — litellm
            # resolves an `ollama/*` name by asking the daemon, untimed (measured
            # 129,781 ms against an address that does not answer). This runs on
            # every turn, inside `async def build_chat_turn`, so it is deferred
            # off the loop like the probe.
            catalog=deferred_lookup(
                self._catalog_cache,
                agent_config.model,
                lambda: self._catalog_fn(agent_config.model),
            ),
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
        overhead_tokens: int | None = None,
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

        ``overhead_tokens`` lets a caller that already measured it pass it in.
        Building the tool schemas to size them costs ~28 ms on the event loop, and
        a turn needs the same figure twice — for this budget and for the ctx field
        the truncation check reads.
        """
        from ..context_budget import estimate_tokens, history_budget

        if agent_config is None:
            return None
        limit = self._context_window(agent_config)
        overhead = overhead_tokens
        if overhead is None:
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
        self,
        agent_config: AgentConfig,
        *,
        app_slug: str | None,
        profile: str | None,
        has_subagents: bool = False,
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
                has_subagents=has_subagents,
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

    async def _subagent_defs(
        self, item_id: str, agent_config: AgentConfig | None
    ) -> tuple[SubagentDef, ...]:
        """Who this turn may delegate to: the App profile's `.agent/` plus the
        item's own workspace, clamped to what THIS turn is itself allowed to do.

        The clamp is the resolved `allowed_tools` rather than the App ceiling
        whenever there is one: a definition file is user-authored, and a
        sub-agent that could reach past its parent would turn a per-item tool
        toggle into a suggestion. Never breaks a turn — a workspace that cannot
        be listed simply has no sub-agents."""
        from ..agent.tools import _profile_tool_ceiling

        app_slug = self._locator.slug_of(item_id)
        profile = self._locator.profile_of(item_id)
        if app_slug is None or profile is None:
            return ()
        ceiling: Any = (
            agent_config.allowed_tools
            if agent_config is not None and agent_config.allowed_tools is not None
            else _profile_tool_ceiling(app_slug, profile)
        )
        try:
            defs = await load_subagents(self._files, item_id, app_slug, profile, ceiling=ceiling)
        except Exception:  # noqa: BLE001 — delegation is a capability, never a turn-breaker
            logger.warning("turn-context: sub-agent defs skipped for %s", item_id, exc_info=True)
            return ()
        return tuple(defs)

    async def _external_tools(self, item_id: str, session: Any) -> ExternalTools:
        """#674: resolve this app's third-party tools, once, at the top of a turn.

        Before the sandbox exists, because the answer decides which tools the
        model is offered — and the sha it returns is what the sandbox is later
        created with, so the two can never describe different bundles.

        When one ALREADY exists, though, its bundles were fixed when it was
        created and the resolve cannot change them. So the mounted set becomes
        the ceiling: a tool registered since, or released since, is reported as
        unavailable with a reason rather than handed over as a launcher that
        isn't there."""
        external = await resolve_item_tools(self._sandbox, self._locator, item_id)
        return confine_to_mounted(external, live=session.handle is not None, mounted=session.tools)

    def _common(
        self,
        item_id: str,
        session: Any,
        *,
        agent_config: AgentConfig | None,
        run_subagent: RunSubagent,
        history_messages: list[Message],
        external: ExternalTools,
        subagent_defs: tuple[SubagentDef, ...] = (),
        request_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """The fields identical across every RCA turn shape (interactive + workflow)."""
        # #624: capture whether history had to be cut, so the send path can say so
        # in the thread. The cut used to be unspeakable — nothing recorded it.
        cut: list[int] = []
        said: list[str] = []
        # #624: measured ONCE — sizing the tool schemas builds them (~28 ms, on
        # the event loop) and this turn needs the same figure twice: to derive
        # the history budget, and as the ctx field the silent-truncation check
        # compares against.
        overhead = self._overhead_for(agent_config, item_id, has_subagents=bool(subagent_defs))
        # #624: the same window the history budget below is derived from, carried
        # on the ctx so the runner can tell the endpoint to OPEN that much rather
        # than serve its own default and truncate the difference away in silence.
        window = self._context_window(agent_config)
        history = history_items(
            history_messages,
            max_messages=self._history_max_messages,
            # The token ceiling is DERIVED from what this endpoint can actually
            # take, minus what the prompt + tool schemas already spend — not a
            # constant. An unknown ceiling yields None ⇒ no trim (we send it all
            # and learn the real limit from the response), unless an operator has
            # set the legacy manual cap. None is deliberately not 0: 0 is a known
            # ceiling with no room left, which must trim to nothing, not to all.
            max_tokens=(
                derived
                if (derived := self._budget_for(agent_config, overhead_tokens=overhead)) is not None
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
            # #674: the third-party bundles this turn resolved travel with
            # `create`, so the sandbox mounts the very shas whose schemas the
            # model was handed a moment ago.
            sandbox_spec=SandboxSpec(tools=external.shas),
            # The item's user env, read fresh per turn — which is what makes an
            # edit between turns take effect. NOT folded into `sandbox_spec`:
            # that is create-time infra env, and the launcher's own exports run
            # after it, so a user value placed there would be silently
            # overwritten for exactly the names that collide.
            #
            # #714: whatever the request behind this turn contributed goes in
            # FIRST, so the item's own panel wins a name collision. The two are
            # different kinds of thing — the request's values belong to the one
            # person who pressed send and are never stored, the item's are a
            # shared copy every participant can read — and the decision was that
            # the stored one overrides, unannounced, so a value can be pinned
            # for testing. `request_env` is empty for every turn with no request
            # behind it (a workflow step, the goal driver, a scheduled job),
            # which is the whole of what those turns inherit: nothing.
            user_env={**(request_env or {}), **self._locator.env_vars_of(item_id)},
            handle=session.handle,
            # Route lazy-create through the registry so session.handle is set
            # (so idle-kill/close_all can find it) and the restore-after-create
            # hook fires. #492 P11: forward the turn's restore-progress sink so a
            # cold-wake restore streams "還原中 N/M" into this turn's stream.
            # #674: and the bundles, which arrive from `sandbox_spec` above — the
            # registry composes the item's spec and has no turn to ask.
            ensure_sandbox_via=lambda on_progress, tools: self._registry.ensure_handle(
                session, tools=tools, on_progress=on_progress
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
            context_overhead_tokens=overhead,
            context_window=window.tokens,
            # First-party packages come from the startup scan; third-party ones
            # from this turn's resolve. The model cannot tell them apart, which
            # is the point — they are the same kind of thing.
            packages=[*(self._packages or []), *external.packages],
            unavailable_tools=external.refused,
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
            # Who this turn may delegate to, and how. Both or neither: the tool is
            # only built when there are definitions, and definitions with no seam
            # would advertise a capability the turn cannot perform.
            subagent_defs=subagent_defs,
            run_agent=self._run_agent,
        )

    async def _entity_schema_note(self, item_id: str) -> str:
        """#pm: the item's record-type schema rendered for the turn's prompt, so
        the agent creates valid records (right field names, the closed status
        vocab, the date-range a timeline reads) instead of guessing — a small
        local model otherwise invents fields/statuses that lint or hide from the
        gantt. Derived live from `.entity/`, so it stays correct as the schema
        evolves; empty for an item with no entity types. Never breaks a turn."""
        try:
            catalog, _ = await discover_catalog(self._filestore, item_id)
            return entity_schema_brief(catalog)
        except Exception:  # noqa: BLE001 — schema guidance is best-effort, never fatal
            logger.debug("turn-context: entity schema brief skipped for %s", item_id, exc_info=True)
            return ""

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
        call_lane: CallLane = "background",
        apply_skills: list[str] | None = None,
        conversation_id: str | None = None,
        request_env: dict[str, str] | None = None,
    ) -> AgentToolContext:
        """The full RCA/workspace-chat turn context (`_send_into`).

        ``call_lane`` comes from the CALLER, not from this builder: the same send
        path serves a person hitting send and the goal driver continuing a chat by
        itself, and only the caller knows which it is. It defaults to the tighter
        lane so a new caller that forgets cannot spend a person's quota.

        ``request_env`` (#714) likewise comes from the caller, and for the same
        reason: only the send path holds the request these values were read from.
        It defaults to none, so the goal driver re-entering this path with nobody
        watching gets a turn carrying the item's env and nothing else."""
        session = await self._registry.session(item_id)
        logger.debug("turn-context: build chat turn for %s", item_id)
        external = await self._external_tools(item_id, session)
        return AgentToolContext(
            **self._common(
                item_id,
                session,
                agent_config=agent_config,
                run_subagent=run_subagent,
                subagent_defs=await self._subagent_defs(item_id, agent_config),
                history_messages=history_messages,
                external=external,
                request_env=request_env,
            ),
            # #pm: live record-type schema so the agent creates valid issues /
            # milestones up front (field names, status vocab, timeline date-range).
            entity_schema_note=await self._entity_schema_note(item_id),
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
            # Is a person waiting on this turn? Decides which rate limit the
            # gateway applies to its LLM calls.
            call_lane=call_lane,
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
        external = await self._external_tools(item_id, session)
        return AgentToolContext(
            **self._common(
                item_id,
                session,
                agent_config=agent_config,
                run_subagent=run_subagent,
                subagent_defs=await self._subagent_defs(item_id, agent_config),
                history_messages=history_messages,
                external=external,
            ),
            entity_write_origin=entity_write_origin,
        )
