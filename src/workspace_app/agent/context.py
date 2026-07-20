from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..files import WorkspaceFiles
from ..filestore.protocol import FileStore
from ..sandbox.protocol import OutputSink, Sandbox, SandboxHandle, SandboxSpec
from ..sync import SandboxSync

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..entity.events import EntityOrigin, EntityWriteSink
    from ..kb.retriever import Enhancements, Retriever
    from ..kb.vlm import IVlm, VlmDescriber
    from ..kb.wiki.sources import IWikiSources
    from ..resources import AgentConfig
    from ..resources.conversation import Citation
    from ..resources.kb import RetrievedPassage
    from ..tooling.registry import PackageInfo
    from ..users.protocol import User, UserDirectory


@dataclass
class KbSearchBudget:
    """Per-turn budget for how many times `kb_search` may actually run (#195, #334).

    `max_calls` is the cap: `None` ⇒ unlimited (still counted, so callers can
    report how many ran); `0` ⇒ no search this turn (answer from context only,
    #334 Q4). `used` increments on every completed search (incl. empty/erroring
    ones, so a model that keeps matching nothing can't loop forever).

    The object is mutable and shareable BY REFERENCE: an app turn that spawns
    several `ask_knowledge_base` sub-agents threads ONE budget through them all
    (#334 Q6), so the whole turn — not each sub-agent — gets `max_calls` searches.
    A KB-chat turn just holds its own.
    """

    max_calls: int | None = None
    used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.max_calls is not None and self.used >= self.max_calls

    @property
    def remaining(self) -> int | None:
        return None if self.max_calls is None else max(0, self.max_calls - self.used)


@dataclass
class WikiSearchBudget:
    """Per-turn budget for how many times `search_wiki` may grep the wiki (#506).

    The exact shape as `KbSearchBudget` — `max_calls` caps it (`None` ⇒ unlimited
    but counted; `0` ⇒ no wiki search), `used` increments on every completed grep.
    Given its own type (not reused) so the two knobs are tuned + threaded
    independently: the card drafter's `ask_knowledge_base` spec caps wiki and
    chunk search separately, and a function can't pass the wrong budget by mistake.
    Default unlimited, so the wiki maintainer/reader (which never set it) are
    unaffected.
    """

    max_calls: int | None = None
    used: int = 0

    @property
    def exhausted(self) -> bool:
        return self.max_calls is not None and self.used >= self.max_calls

    @property
    def remaining(self) -> int | None:
        return None if self.max_calls is None else max(0, self.max_calls - self.used)


@dataclass
class AgentToolContext:
    """Per-run context passed into agent tools.

    Two flavours share this context so they can share `LitellmAgentRunner`:

    - The **RCA workspace** agent sets `investigation_id` + `sandbox` +
      `files` and gets the file/exec tools. The sandbox is woken lazily on the
      first `exec`; the `files` facade routes file ops to the live sandbox (the
      source of truth) when it's warm and to the FileStore snapshot when cold,
      so the agent's file tools and its shell always see one view. `restore` on
      wake brings in any cold writes; a throttled mirror persists warm writes
      back to the snapshot.

    - The **KB** agent sets `retriever` + `collection_ids` and gets only the
      `kb_search` tool — no sandbox, no file store. Each `kb_search` appends to
      `kb_passages` (a per-turn registry) so the answer's `[n]` markers map back
      to the passage that produced them.

    `ensure_sandbox_via` lets the caller (typically the API layer's
    InvestigationRegistry) own handle creation — so the registry's
    restore-after-create hook fires and idle-kill can later find and reap the
    handle. When unset, ctx falls back to a direct `sandbox.create(...)`; useful
    in tests that don't wire a registry.
    """

    # RCA workspace agent (file/exec tools).
    investigation_id: str | None = None
    sandbox: Sandbox | None = None
    filestore: FileStore | None = None
    # The file-access chokepoint the file tools go through. When unset, the
    # tools wrap `filestore` in a fresh facade (transitional — P2 makes this the
    # liveness-routing instance the API layer injects).
    files: WorkspaceFiles | None = None
    sync: SandboxSync | None = None
    sandbox_spec: SandboxSpec = field(default_factory=SandboxSpec)
    handle: SandboxHandle | None = None
    # #492 P11: the wake hook receives the turn's restore-progress sink so a cold
    # wake's snapshot restore can stream (done, total) back to the turn. Callers
    # that don't care (tests) accept it and ignore it.
    ensure_sandbox_via: (
        Callable[[Callable[[int, int], None] | None], Awaitable[SandboxHandle]] | None
    ) = None
    # The investigation's attached AgentConfig (model + prompt) for this
    # turn; when set, LitellmAgentRunner uses it instead of its default.
    agent_config: AgentConfig | None = None
    # Optional sink the exec tool streams a command's stdout to while it runs,
    # so the runner can emit live tool-log events. Set per-run by the runner.
    on_exec_output: OutputSink | None = None
    # #492 P11: optional sink for cold-wake restore progress — called (done, total)
    # per restored file so the runner can emit RestoreProgress events ("還原中 N/M")
    # instead of leaving a blank running card. Set per-run by the runner (like
    # on_exec_output); None ⇒ no progress surfaced (host-managed / non-turn wakes).
    on_restore_progress: Callable[[int, int], None] | None = None
    # read_file caps (deploy config; the API layer sets these from Settings).
    # A read past either cap is truncated with a notice; the agent pages with
    # offset/limit. Defaults sized for a large-context model.
    read_file_max_lines: int = 2000
    read_file_max_chars: int = 200_000
    # Exec/tool stdout+stderr cap (issue #44). A command (e.g. `grep` over a
    # big file) whose output exceeds this is truncated head+tail with a
    # notice, so one tool call can't flood the model's context. Smaller than
    # the read_file cap because tool outputs accumulate across turns.
    exec_output_max_chars: int = 30_000
    # The ABSOLUTE ceiling on any one tool result, enforced for every tool by
    # `agent/output_cap.py` rather than by each tool remembering to cap itself.
    # It is a backstop: the per-tool caps above are deliberately tighter, and a
    # tool that can page (read_file, list_files) should page instead of relying
    # on this. Sized at the widest legitimate single answer (a full read_file).
    tool_output_max_chars: int = 200_000
    # Prior-turn dialogue as SDK input items ({role, content}) for cross-turn
    # memory (#17). Set per-turn by the API layer from the persisted thread; the
    # runner prepends it to this turn's message. Empty for a fresh thread.
    history: list[dict[str, str]] = field(default_factory=list)
    # Attached images (data: URLs) to inline into THIS turn's user message so a
    # vision-capable main model sees the pixels directly — no `read_image`
    # round-trip through the separate VLM. Set per-turn by the API layer ONLY
    # when the resolved agent is a VLM (`agent_config.vision`); the runner passes
    # them to `_build_input`, which makes the user message multimodal. Empty for
    # text-only models (and turns with no attachment) ⇒ the text-only path is
    # unchanged. The image also persists as a workspace file, so a later turn can
    # still `read_image` it — the persisted history stays text.
    turn_image_urls: list[str] = field(default_factory=list)
    # Deploy-level registry of provisionable tool packages (#21, #25). When
    # the sandbox is created, the packages that appear in
    # agent_config.allowed_tools (raw pkg name, or `pkg:cmd` colon syntax) are
    # installed into it from prebuilt_dir; the runner also exposes their
    # commands as function tools. Set by the API layer from create_app config.
    packages: list[PackageInfo] = field(default_factory=list)
    # Host path to the prebuilt-packages root. ``provision_tools`` reads
    # ``prebuilt_dir / pkg.name`` and tars it into the sandbox. Required when
    # ``packages`` is non-empty.
    prebuilt_dir: Path | None = None

    # Per-run step-limit override (max LLM turns). None ⇒ the runner's
    # configured default. The wiki maintainer/reader need MANY more turns than
    # a chat reply: a maintenance pass reads the schema + source, searches,
    # then writes several pages — well past the default ~10, after which the
    # SDK yields MaxTurnsExceeded and the run ends having written nothing.
    max_turns: int | None = None

    # Per-message reasoning effort from the UI selector ("low"/"medium"/"high");
    # None → the model's default. Threaded to the model's ModelSettings.
    reasoning_effort: str | None = None

    # #66: how many infer_modules per-step classifications run concurrently.
    # The tool fans out one sub-agent call per unique step; this bounds the
    # fan-out (operator-configured via agents.infer_modules[].parallelism).
    # NB: real end-to-end concurrency also needs the LLM endpoint to allow it
    # (e.g. Ollama OLLAMA_NUM_PARALLEL>1), else requests serialize server-side.
    infer_modules_parallelism: int = 16

    # The item's App slug + profile name (#29 / §A, #89). When both are set the
    # runner exposes `read_skill` if the profile ships skills, and read_skill
    # reads from `apps/<app_slug>/profiles/<template_profile>/.skill/...`. None
    # for KB-flavour contexts (no App/profile).
    app_slug: str | None = None
    template_profile: str | None = None

    # #380: the item's per-turn tri-state skill override (`attached_skill_prefs`),
    # set by the API layer. `read_skill` refuses a skill pinned OFF here (a skill
    # the user toggled off is also absent from the advertised index). Empty ⇒ every
    # skill follows its profile/App default. A skill applied THIS turn is loaded
    # regardless — apply overrides the off toggle (see `applied_skills`).
    skill_prefs: dict[str, bool] = field(default_factory=dict)

    # #380: skills the user chose to APPLY this turn (per-message). Their bodies are
    # already preloaded into the turn; read_skill also exempts them from the toggle
    # gate so apply consistently overrides a disabled skill. Empty on most turns.
    applied_skills: list[str] = field(default_factory=list)

    # #112: the VLM describer the `read_image` tool uses to read a workspace
    # image. Set by the API layer from `get_kb_vlm`/`VlmDescriber` (shared with
    # KB ingestion). None when the deployment configured no VLM (`kb.vlm_llm`
    # unset) — `read_image` then reports it's unavailable instead of failing.
    describer: VlmDescriber | None = None

    # #284: the multimodal model the `make_deck` tool drives — it both *sees*
    # rendered slides and *writes* the pptxgenjs fix. Set by the API layer from
    # `get_designed_pptx_vlm` (falls back to the read_image VLM). None when no
    # vision model is configured — `make_deck` then reports it's unavailable.
    deck_vlm: IVlm | None = None

    # KB agent (kb_search tool).
    retriever: Retriever | None = None
    collection_ids: list[str] = field(default_factory=list)
    # #280: the item's collection set grouped into priority tiers, ordered by
    # rank (rank 0 = highest-priority tier). Read from `collections.json`'s
    # optional per-entry `tier` (sparse ints collapse to ranks). Empty when the
    # item configures no tiers ⇒ `ask_knowledge_base` searches the whole KB
    # (today's behaviour). `collection_ids` above stays the flat union (the
    # glossary / resolve_collection scope); this drives the agent-rankable
    # `ask_knowledge_base` fallback only.
    collection_tiers: list[list[str]] = field(default_factory=list)
    # #308: the doc-ids whose per-doc read override BLOCKS this turn's speaker from
    # their CONTENT — the retriever excludes their chunks so a doc tightened away
    # from the speaker never leaks into an AI answer. Computed ONCE at the API
    # boundary (KB-chat send + the ask_knowledge_base bridge), where the speaker,
    # their groups, and the superusers are all known; empty for an unwired /
    # no-override context, so a turn nobody tightened per-doc pays nothing.
    exclude_doc_ids: frozenset[str] = field(default_factory=frozenset)
    # Permission-disclosure: collections in this turn's scope the speaker may
    # see-exist (read_meta) but NOT read (read_content). The disclosure probe in
    # kb_search runs over these; a competitive match is disclosed (existence only)
    # instead of silently dropped. Computed at the API boundary beside the readable
    # `collection_ids` + `exclude_doc_ids`; empty ⇒ nothing to disclose (the probe
    # short-circuits, so a turn with no discoverable collections pays nothing).
    discoverable_collection_ids: list[str] = field(default_factory=list)
    # Permission-disclosure: the per-turn accumulator of DISCLOSED withheld
    # collection ids (union across this turn's kb_search calls / sub-agents),
    # mirroring `kb_passages`. Resolved to WithheldSource + persisted on the
    # assistant message at persist time (the FE refetch renders the lock chips).
    withheld_collection_ids: list[str] = field(default_factory=list)
    kb_passages: list[RetrievedPassage] = field(default_factory=list)
    # #484: resource-ids of the context cards already injected into this turn — by
    # the #106 user-message pre-scan (seeded at the API boundary) AND by earlier
    # `kb_search` calls. `kb_search` scans each result's passages for glossary
    # terms and appends the authoritative card definitions, skipping any card
    # already in this set, so a term the user asked about — or a passage retrieved
    # twice — is defined exactly once per turn instead of re-injected every search.
    injected_card_ids: set[str] = field(default_factory=set)
    # #195 / #334: this turn's kb_search budget. Default unlimited-but-counted;
    # the KB-chat turn and the ask_knowledge_base bridge seed `max_calls` from
    # `kb.max_searches_per_turn` (or the composer's per-message pick, #334). Once
    # exhausted, `kb_search` stops running the retriever and returns a sentinel
    # telling the model to answer from the passages it already has; every capped
    # result also reports the remaining budget so a small model spends frugally.
    # An app turn shares ONE instance across its ask_knowledge_base sub-agents.
    kb_search_budget: KbSearchBudget = field(default_factory=KbSearchBudget)
    # #506: this turn's search_wiki budget — the symmetric wiki twin of
    # kb_search_budget. Default unlimited-but-counted, so the wiki maintainer/
    # reader (which never set it) keep grepping freely; the ask_knowledge_base
    # spec seeds `max_calls` when the card drafter wants wiki search capped.
    wiki_search_budget: WikiSearchBudget = field(default_factory=WikiSearchBudget)
    # Topic Hub tools (`resolve_collection`, `lookup_glossary`) query specstar
    # resources (Collection / ContextCard) directly. Set by the Topic Hub turn
    # builder; None for RCA/KB-flavour contexts.
    spec: SpecStar | None = None
    # The acting user for agent-driven specstar writes (#111: create/update
    # context cards stamp `created_by`/`updated_by` as this user). Set per-turn
    # from the message author; empty for contexts with no card-write tools.
    acting_user: str = ""
    # #429 P10: the event-dispatch sink the agent's entity tools (create/update/
    # link_entity) publish a post-commit EntityWriteEvent to, so an AI-authored
    # entity change fires on_event workflows exactly like a UI or workflow write —
    # the single write path stays indistinguishable across all its callers. None ⇒
    # no emit (KB/wiki/test contexts that wire no triggers pay nothing).
    entity_write_sink: EntityWriteSink | None = None
    # #429 P10: the ambient trigger origin stamped on those writes. Set ONLY when
    # this turn runs INSIDE a triggered workflow run (build_workflow_turn threads
    # the run's EntityOrigin(trigger, depth)), so the dispatcher's self-trigger +
    # depth-cap recursion guards count an agent-mediated write like any other run
    # write. A plain user chat leaves it None (depth 0 — a first-level write that
    # SHOULD fire triggers).
    entity_write_origin: EntityOrigin | None = None
    # #242: the resolved current speaker (name / handle / section) for the
    # per-turn system note that tells the agent who it is replying to in a
    # shared, multi-collaborator workspace. Set per-turn by the API layer from
    # `users.get(author)`; None when unwired (single-user / replay).
    speaker: User | None = None
    # Per-turn enhancement override the *caller* (KB chat composer,
    # ask_knowledge_base bridge, …) wants the kb_search tool to apply
    # on top of the operator's retriever defaults. LLM-set tool args
    # win over this; this wins over the operator default. `None` = no
    # caller override.
    kb_enhancements: Enhancements | None = None
    # RCA → sub-agent bridge: when set (by the API layer), the RCA
    # agent's sub-agent-facing tools (`ask_knowledge_base`,
    # `infer_modules`, future) reach their sub-agent via this single
    # callable. Args: `(purpose, payload, sink, origin_id)` —
    # `purpose` names the entry in `AgentConfigCatalog.configs_for(...)`
    # (e.g. `"kb_chat"` or `"infer_modules"`); `payload` is the
    # already-formatted question string (tool impls own the
    # arg→string formatting); `sink` is the RCA turn's `on_exec_output`
    # so the sub-agent's live work relays as tool-log lines under the
    # calling tool; `origin_id` is this investigation's id so the KB
    # citations the sub-agent produces are logged against it. Returns
    # the answer text + the resolved citations — the tool impl stashes
    # the citations into `subagent_citations` so the turn engine can
    # attach them to the persisted tool message.
    #
    # #280: an optional 5th arg `collection_ids: list[str] | None` lets the
    # caller (ask_knowledge_base, after resolving its `rank` → a priority tier)
    # scope the kb_chat sub-agent to that tier's collections; `None` ⇒ the whole
    # KB. The type is `Callable[..., …]` because that override is keyword-default
    # (not expressible in a positional `Callable[[...], …]` signature).
    run_subagent: Callable[..., Awaitable[tuple[str, list[Citation]]]] | None = None
    # #537: the KB agent's SECOND knowledge source — consult the wiki. Given a
    # question, a wiki reader navigates the wiki index-first (index → the pages the
    # index points at → the source documents behind them) and returns its answer
    # plus the passages it grounded on. The navigation runs in a THROWAWAY context,
    # so whole wiki pages never land in the caller's window — the same
    # context-economy reason `ask_knowledge_base` delegates (#270), and the reason
    # the caller is NOT simply handed the wiki's file tools.
    #
    # Wired by the API layer for turns that scope a wiki-backed collection; `None`
    # ⇒ `ask_wiki` reports there is no wiki here rather than failing.
    run_wiki_reader: (
        Callable[[str, OutputSink | None], Awaitable[tuple[str, list[RetrievedPassage]]]] | None
    ) = None
    # Per-call citation lists from this turn's sub-agent invocations,
    # keyed by purpose. Per purpose, lists are in CALL ORDER — the
    # persist step pairs the Nth list with the Nth tool message of
    # that purpose. (RunContextWrapper-typed tool params don't expose
    # the call id; the SDK runs tools sequentially within a turn, so
    # per-purpose order pairing is unambiguous.)
    subagent_citations: dict[str, list[list[Citation]]] = field(default_factory=dict)
    # #537: this turn's knowledge-source allowance, rendered for the prompt. Set
    # where the budgets are resolved (that's the only place that still knows a
    # tool was dropped because its allowance was 0, rather than never granted).
    # The runner appends it to the system prompt; "" ⇒ nothing appended.
    search_allowance_note: str = ""
    # #62: a per-turn map from an exec tool's LLM-facing result (the cleaned
    # `_format_exec`, which IS the ToolEnd.output) to the FULL display result
    # (stderr kept even on success). The runner keys off ToolEnd.output to
    # attach the display version, so the FE/persisted card can show the error
    # the user saw stream live instead of a clean "exit_code=0". Only populated
    # when the two differ (exit 0 with non-empty stderr), so it stays tiny.
    # String-keyed, not call-id keyed: RunContextWrapper tool params don't
    # expose a call id (see subagent_citations above), and the cleaned output
    # is what the ToolEnd carries verbatim.
    tool_displays: dict[str, str] = field(default_factory=dict)
    # RCA agent's `mention_user` tool reaches this to summon a human to the
    # investigation. Args: (investigation_id, user_ids, note). Wired by the API.
    mention: Callable[[str, list[str], str], None] | None = None
    # #275: the company directory the `lookup_user` tool resolves a teammate's
    # handle → {name, id, section, email} through. The agent only reads the
    # `[Name (handle)]:` prefixes (#242), so this is how it turns a visible
    # handle into the canonical id it needs to act on them (e.g. mention_user).
    # Set per-turn where `speaker` is set (RCA); None when unwired (the tool
    # then reports it's unavailable instead of raising).
    users: UserDirectory | None = None

    # Wiki agents (#50). A maintainer/reader run sets `filestore` to a
    # WikiFileStore + `investigation_id` to the wiki workspace id, `sandbox`
    # None — the file tools then operate on the wiki pages. These add the
    # wiki-specific tools' data:
    #   - `wiki_sources`: read-only access to the collection's raw source
    #     docs (layer 1) for list_sources / read_source.
    #   - `wiki_new_source`: the source doc text that triggered this
    #     maintainer run (read_new_source).
    #   - `wiki_cite_sources`: True on a READER run — read_source then
    #     registers each read source into `kb_passages` and returns it
    #     numbered ([n]), so the answer's [n] markers resolve back to the
    #     underlying SourceDoc (option 2 citations). False on a maintainer
    #     run (read_source returns plain text for cross-referencing).
    wiki_sources: IWikiSources | None = None
    wiki_new_source: str | None = None
    wiki_cite_sources: bool = False

    # #397: the `request_wiki_update` tool submits a user's wiki correction
    # through this — bound to WikiMaintenanceCoordinator.submit_correction by the
    # API layer on turns that scope a wiki-enabled collection (chat + kb_chat),
    # None otherwise (the tool then reports it's unavailable). Args:
    # (collection_id, *, instruction, target_page, reference, requested_by) →
    # the corrections page path. Mirrors the run_subagent/mention callback pattern
    # so agent/ stays decoupled from kb/wiki/.
    submit_wiki_correction: Callable[..., Awaitable[str]] | None = None

    async def ensure_sandbox(self) -> SandboxHandle:
        assert self.sandbox is not None  # file/exec tools imply an RCA context
        if self.handle is None:
            if self.ensure_sandbox_via is not None:
                # #492 P11: hand the wake hook the restore-progress sink so a slow
                # cold-wake restore streams "還原中 N/M" to the turn.
                self.handle = await self.ensure_sandbox_via(self.on_restore_progress)
            else:
                self.handle = await self.sandbox.create(self.sandbox_spec)
            # Eagerly install the allowed packages into the fresh sandbox
            # (after any snapshot restore the ensure-hook did), so the agent
            # can call their commands. Runs once per sandbox (handle was None).
            #
            # `allowed_tools` uses the colon syntax (`"pkg"` or `"pkg:cmd"`);
            # for provisioning, a package goes in if ANY of its commands
            # appear in the allow list — we can't install half a venv.
            if self.packages and self.agent_config is not None:
                from .provision import provision_tools

                pkg_names_in_allowed = {
                    a.split(":", 1)[0] for a in (self.agent_config.allowed_tools or [])
                }
                todo = [p for p in self.packages if p.name in pkg_names_in_allowed]
                if todo and self.prebuilt_dir is not None:
                    await provision_tools(
                        self.sandbox, self.handle, todo, prebuilt_dir=self.prebuilt_dir
                    )
        return self.handle
