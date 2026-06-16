"""run_wiki_maintainer (#50 P2) — run one wiki-maintenance turn over a
collection's wiki.

Karpathy-faithful + reuse: the maintainer is the SAME agent runner, given
a sandbox-free context whose ``filestore`` is the per-page ``WikiFileStore``
and whose ``investigation_id`` is the collection id — so the existing file
tools (read/write/edit/ls) operate on the wiki pages, plus the wiki tools
(search_wiki / read_new_source / list_sources / read_source). No sandbox →
writes land straight in the durable FileStore; nothing depends on the
human-initiated workspace/sandbox lifecycle.

The maintainer is triggered per ingest (coalesced) in P3; this is the
runnable unit it calls.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ...agent.context import AgentToolContext
from ...files import WorkspaceFiles
from ...resources import AgentConfig
from .sources import IWikiSources
from .store import WikiFileStore

if TYPE_CHECKING:
    from ...api.events import AgentEvent
    from ...api.runner import AgentRunner

_WIKI_SCHEMA = (Path(__file__).parent / "wiki_schema.md").read_text(encoding="utf-8")
_MAINTAINER_PROMPT = (Path(__file__).parent.parent / "prompts" / "wiki_maintainer.md").read_text(
    encoding="utf-8"
)
_UNFOLDER_PROMPT = (Path(__file__).parent.parent / "prompts" / "wiki_unfolder.md").read_text(
    encoding="utf-8"
)

# Default user-turn instruction for a fold pass (a source was added). The
# unfold pass passes its own remove-oriented instruction.
_FOLD_INSTRUCTION = "A new source document was added to the collection. Integrate it into the wiki."

# A maintenance pass reads the schema + source, searches the wiki, then writes
# several pages (summary / entity / concept / index / log) — well past a chat
# reply's ~10 turns. Too low and the SDK yields MaxTurnsExceeded mid-read and
# the run ends having written nothing. The operator tunes it via
# settings.kb.wiki.maintainer_max_turns; this is the fallback default.
_DEFAULT_MAINTAINER_MAX_TURNS = 40

# The maintainer's tools: the existing file tools (over the WikiFileStore)
# + the wiki-specific tools. No exec/sandbox.
_WIKI_MAINTAINER_TOOLS = [
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "delete_file",
    "search_wiki",
    "read_new_source",
    "list_sources",
    "read_source",
]


def default_wiki_maintainer_config() -> AgentConfig:
    """The bundled wiki-maintainer AgentConfig — its system prompt is the
    maintainer instructions; allowed_tools is the wiki toolset."""
    return AgentConfig(
        name="Wiki Maintainer",
        system_prompt=_MAINTAINER_PROMPT,
        allowed_tools=list(_WIKI_MAINTAINER_TOOLS),
    )


def default_wiki_unfolder_config() -> AgentConfig:
    """The bundled wiki-UNFOLDER AgentConfig (#43): same toolset as the
    maintainer, but a remove-oriented system prompt — it scrubs a deleted
    source's content + citations from the wiki instead of folding one in."""
    return AgentConfig(
        name="Wiki Unfolder",
        system_prompt=_UNFOLDER_PROMPT,
        allowed_tools=list(_WIKI_MAINTAINER_TOOLS),
    )


async def run_wiki_maintainer(
    runner: AgentRunner,
    *,
    wiki_store: WikiFileStore,
    wiki_sources: IWikiSources,
    collection_id: str,
    new_source: str,
    agent_config: AgentConfig,
    max_turns: int = _DEFAULT_MAINTAINER_MAX_TURNS,
    on_event: Callable[[AgentEvent], None] | None = None,
    instruction: str = _FOLD_INSTRUCTION,
) -> None:
    """Run one wiki-agent pass over ``new_source``: seed ``/WIKI.md`` if absent,
    build the sandbox-free wiki context, and drive the agent. Returns when the
    agent is done (the work IS the page edits).

    ``instruction`` is the user-turn message — the default folds the source in;
    the un-fold pass (#43) passes a remove-oriented instruction with the same
    machinery (the ``agent_config``'s system prompt sets fold vs. remove).

    ``max_turns`` is the step budget for the pass (operator-configured via
    settings.kb.wiki.maintainer_max_turns); it must be generous — a pass
    reads + searches before it writes several pages."""
    # Seed the conventions file once — the agent reads (and may later edit)
    # it; don't clobber an existing one.
    if not await wiki_store.exists(collection_id, "/WIKI.md"):
        await wiki_store.write(collection_id, "/WIKI.md", _WIKI_SCHEMA.encode("utf-8"))

    ctx = AgentToolContext(
        investigation_id=collection_id,  # WikiFileStore is keyed by collection id
        filestore=wiki_store,
        files=WorkspaceFiles(wiki_store),
        sandbox=None,
        agent_config=agent_config,
        wiki_sources=wiki_sources,
        wiki_new_source=new_source,
        max_turns=max_turns,
    )
    async for ev in runner.run(instruction, ctx):
        if on_event is not None:
            on_event(ev)
