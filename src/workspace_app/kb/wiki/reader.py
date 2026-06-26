"""answer_from_wiki (#50 P4) — answer a question from a collection's LLM wiki.

The reader is the SAME agent runner given a sandbox-free wiki context: its
``filestore`` is the per-page ``WikiFileStore`` and ``investigation_id`` is the
collection id, so the existing file tools (list_files / read_file) plus ``search_wiki``
(grep) navigate the wiki pages. It grounds answers in the underlying source
documents via ``read_source`` (read-only) and cites them.

**Citations point back to the raw ``SourceDoc`` (option 2).** With
``wiki_cite_sources`` on, ``read_source`` registers each source it reads into
``kb_passages`` and returns it numbered ([n]); the answer's [n] markers then
resolve to the underlying document via the unchanged ``parse_citations`` — same
shape, same FE reference card, same click-through as chunk-RAG citations. The
wiki is synthesised, so provenance is document-level (snippet = the source text
the reader grounded against), coarser than a chunk span but auditable.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ...agent.context import AgentToolContext
from ...files import WorkspaceFiles
from ...resources import AgentConfig
from ...resources.conversation import Citation
from ..citations import parse_citations
from .sources import IWikiSources
from .store import WikiFileStore

if TYPE_CHECKING:
    from ...api.events import AgentEvent
    from ...api.runner import AgentRunner

_READER_PROMPT = (Path(__file__).parent.parent / "prompts" / "wiki_reader.md").read_text(
    encoding="utf-8"
)

# Navigating the wiki (index → [[links]] → pages) and grounding in sources is
# multi-step — more than a single chat reply's turn budget. Operator-tuned via
# settings.kb.wiki.reader_max_turns; this is the fallback default.
_DEFAULT_READER_MAX_TURNS = 24

# The reader's tools: navigate the wiki (list_files / read_file / search_wiki),
# reach the raw sources to ground + cite (list_sources / read_source). NO writes.
_WIKI_READER_TOOLS = [
    "list_files",
    "read_file",
    "search_wiki",
    "list_sources",
    "read_source",
]


def default_wiki_reader_config() -> AgentConfig:
    """The bundled wiki-reader AgentConfig — read-only navigation toolset."""
    return AgentConfig(
        name="Wiki Reader",
        system_prompt=_READER_PROMPT,
        allowed_tools=list(_WIKI_READER_TOOLS),
    )


async def answer_from_wiki(
    runner: AgentRunner,
    *,
    wiki_store: WikiFileStore,
    wiki_sources: IWikiSources,
    collection_id: str,
    question: str,
    agent_config: AgentConfig,
    max_turns: int = _DEFAULT_READER_MAX_TURNS,
    on_event: Callable[[AgentEvent], None] | None = None,
) -> tuple[str, list[Citation]]:
    """Run one wiki-reader turn to completion and return its answer + the
    citations (resolved against the sources it grounded on). ``on_event`` (when
    given) fires for every reader event so a caller can relay the navigation
    into a parent stream. ``max_turns`` (operator-configured via
    settings.kb.wiki.reader_max_turns) must cover navigating + grounding."""
    from ...api.events import MessageDelta, RunError

    ctx = AgentToolContext(
        investigation_id=collection_id,  # WikiFileStore is keyed by collection id
        filestore=wiki_store,
        files=WorkspaceFiles(wiki_store),
        sandbox=None,
        agent_config=agent_config,
        wiki_sources=wiki_sources,
        wiki_cite_sources=True,
        max_turns=max_turns,
    )
    parts: list[str] = []
    run_error: str | None = None
    async for ev in runner.run(question, ctx):
        if on_event is not None:
            on_event(ev)
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            parts.append(ev.text)
        elif isinstance(ev, RunError):
            run_error = ev.message
    if run_error is not None:
        return f"Wiki reader failed: {run_error}", []
    answer = "".join(parts)
    return answer, parse_citations(answer, ctx.kb_passages)
