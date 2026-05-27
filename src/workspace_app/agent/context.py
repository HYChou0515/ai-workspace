from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..files import WorkspaceFiles
from ..filestore.protocol import FileStore
from ..sandbox.protocol import OutputSink, Sandbox, SandboxHandle, SandboxSpec
from ..sync import SandboxSync

if TYPE_CHECKING:
    from ..kb.retriever import Retriever
    from ..resources import AgentConfig
    from ..resources.kb import RetrievedPassage
    from .provision import ToolDef


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
    # Prior-turn dialogue as SDK input items ({role, content}) for cross-turn
    # memory (#17). Set per-turn by the API layer from the persisted thread; the
    # runner prepends it to this turn's message. Empty for a fresh thread.
    history: list[dict[str, str]] = field(default_factory=list)
    # Deploy-level registry of provisionable tools (#21). When the sandbox is
    # created, the ones named in agent_config.allowed_tools are installed into
    # it (setup); the runner also exposes them as function tools. Set by the API
    # layer from create_app config.
    tool_defs: list[ToolDef] = field(default_factory=list)

    # KB agent (kb_search tool).
    retriever: Retriever | None = None
    collection_ids: list[str] = field(default_factory=list)
    kb_passages: list[RetrievedPassage] = field(default_factory=list)
    # RCA → KB bridge: when set (by the API layer), the RCA agent's
    # `ask_knowledge_base` tool runs the KB agent via this callable and gets
    # back a synthesized, cited answer. Wraps the KB agent (grill Q "Option 1").
    # Args: (question, sink, origin_id). The sink is the RCA run's
    # `on_exec_output` — the bridge relays the KB sub-agent's live progress to it
    # so searches/reasoning show as tool-log lines under the ask_knowledge_base
    # call. `origin_id` is this investigation's id, so the KB citations it
    # produces are logged against it.
    ask_kb: Callable[[str, OutputSink | None, str | None], Awaitable[str]] | None = None
    # RCA agent's `mention_user` tool reaches this to summon a human to the
    # investigation. Args: (investigation_id, user_ids, note). Wired by the API.
    mention: Callable[[str, list[str], str], None] | None = None

    async def ensure_sandbox(self) -> SandboxHandle:
        assert self.sandbox is not None  # file/exec tools imply an RCA context
        if self.handle is None:
            if self.ensure_sandbox_via is not None:
                self.handle = await self.ensure_sandbox_via()
            else:
                self.handle = await self.sandbox.create(self.sandbox_spec)
            # Eagerly install the allowed provisioned tools into the fresh
            # sandbox (after any snapshot restore the ensure-hook did), so the
            # agent can call them. Runs once per sandbox (handle was None).
            if self.tool_defs and self.agent_config is not None:
                from .provision import provision_tools

                allowed = set(self.agent_config.allowed_tools or [])
                todo = [d for d in self.tool_defs if d.name in allowed]
                if todo:
                    await provision_tools(self.sandbox, self.handle, todo)
        return self.handle
