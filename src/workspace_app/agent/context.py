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

    from ..kb.retriever import Enhancements, Retriever
    from ..kb.vlm import VlmDescriber
    from ..kb.wiki.sources import IWikiSources
    from ..resources import AgentConfig
    from ..resources.conversation import Citation
    from ..resources.kb import RetrievedPassage
    from ..tooling.registry import PackageInfo


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
    ensure_sandbox_via: Callable[[], Awaitable[SandboxHandle]] | None = None
    # The investigation's attached AgentConfig (model + prompt) for this
    # turn; when set, LitellmAgentRunner uses it instead of its default.
    agent_config: AgentConfig | None = None
    # Optional sink the exec tool streams a command's stdout to while it runs,
    # so the runner can emit live tool-log events. Set per-run by the runner.
    on_exec_output: OutputSink | None = None
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
    # Prior-turn dialogue as SDK input items ({role, content}) for cross-turn
    # memory (#17). Set per-turn by the API layer from the persisted thread; the
    # runner prepends it to this turn's message. Empty for a fresh thread.
    history: list[dict[str, str]] = field(default_factory=list)
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

    # #112: the VLM describer the `read_image` tool uses to read a workspace
    # image. Set by the API layer from `get_kb_vlm`/`VlmDescriber` (shared with
    # KB ingestion). None when the deployment configured no VLM (`kb.vlm_llm`
    # unset) — `read_image` then reports it's unavailable instead of failing.
    describer: VlmDescriber | None = None

    # KB agent (kb_search tool).
    retriever: Retriever | None = None
    collection_ids: list[str] = field(default_factory=list)
    kb_passages: list[RetrievedPassage] = field(default_factory=list)
    # Topic Hub tools (`resolve_collection`, `lookup_glossary`) query specstar
    # resources (Collection / ContextCard) directly. Set by the Topic Hub turn
    # builder; None for RCA/KB-flavour contexts.
    spec: SpecStar | None = None
    # Per-turn enhancement override the *caller* (KB chat composer,
    # ask_knowledge_base bridge, …) wants the kb_search tool to apply
    # on top of the operator's retriever defaults. LLM-set tool args
    # win over this; this wins over the operator default. `None` = no
    # caller override.
    kb_enhancements: Enhancements | None = None
    # Per-query opt-in to the LLM-wiki retrieval path (#50 P6, the depth
    # picker's "Search the wiki" advanced toggle). The WikiAwareRunner reads
    # this together with each collection's use_rag/use_wiki to route the turn:
    # off ⇒ pure chunk-RAG (unchanged); on + a use_wiki collection ⇒ wiki
    # reader, and both ⇒ chunk + wiki answers merged.
    wiki_query: bool = False
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
    run_subagent: (
        Callable[
            [str, str, OutputSink | None, str | None],
            Awaitable[tuple[str, list[Citation]]],
        ]
        | None
    ) = None
    # Per-call citation lists from this turn's sub-agent invocations,
    # keyed by purpose. Per purpose, lists are in CALL ORDER — the
    # persist step pairs the Nth list with the Nth tool message of
    # that purpose. (RunContextWrapper-typed tool params don't expose
    # the call id; the SDK runs tools sequentially within a turn, so
    # per-purpose order pairing is unambiguous.)
    subagent_citations: dict[str, list[list[Citation]]] = field(default_factory=dict)
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

    async def ensure_sandbox(self) -> SandboxHandle:
        assert self.sandbox is not None  # file/exec tools imply an RCA context
        if self.handle is None:
            if self.ensure_sandbox_via is not None:
                self.handle = await self.ensure_sandbox_via()
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
