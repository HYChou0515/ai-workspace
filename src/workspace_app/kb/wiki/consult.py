"""#537 — consulting the wiki as ONE tool call.

`ask_wiki` hands the caller a written answer; everything it took to get there —
reading the index, opening the pages it points at, following `[[wikilink]]`s,
pulling the source documents behind them — happens in a wiki reader's own
throwaway context and never reaches the caller's window. That isolation is the
whole reason the KB agent isn't simply handed the wiki's file tools (#270): a
handful of whole wiki pages would fill a small local model's context and evict
the conversation it is supposed to be having.

This module builds the closure the tool calls. It is the only place that knows a
turn may scope SEVERAL wikis: it runs one reader per wiki-backed collection and
folds their answers into one, renumbering each draft's `[n]` onto a single
shared source list so every marker in the merged text still resolves to the
document it was written against.

A turn scoping no wiki gets `None` back, not an empty consultant — the wiring
layer then leaves `ctx.run_wiki_reader` unset and the tool says so plainly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources import AgentConfig, Collection
from ...resources.kb import RetrievedPassage
from ...sandbox.protocol import OutputSink
from ..citations import shift_markers
from .guidance import with_collection_guidance
from .reader import _DEFAULT_READER_MAX_TURNS, default_wiki_reader_config, read_wiki
from .sources import SpecstarWikiSources
from .store import WikiFileStore

if TYPE_CHECKING:
    from ...api.runner import AgentRunner

# `(question, progress sink) -> (answer, the sources it grounded on)`. The sink
# is the caller's live tool-log channel: a consultation can run for dozens of
# reader turns, and a chat that shows nothing for that long reads as hung.
WikiConsultant = Callable[[str, "OutputSink | None"], Awaitable[tuple[str, list[RetrievedPassage]]]]


def wiki_backed(spec: SpecStar, collection_ids: list[str]) -> list[str]:
    """Those of `collection_ids` that actually keep a wiki, in the caller's order.
    Unknown / deleted ids are skipped rather than raising — a stale id in a chat's
    scope must not take the turn down."""
    rm = spec.get_resource_manager(Collection)
    out: list[str] = []
    for cid in collection_ids:
        try:
            coll = rm.get(cid).data
        except ResourceIDNotFoundError:
            continue
        if isinstance(coll, Collection) and coll.use_wiki:
            out.append(cid)
    return out


def make_wiki_consultant(
    runner: AgentRunner,
    spec: SpecStar,
    collection_ids: list[str],
    *,
    reader_config: AgentConfig | None = None,
    reader_max_turns: int = _DEFAULT_READER_MAX_TURNS,
    # The deploy's output ceilings, passed down so the reader navigates under
    # the same budgets as the turn that consulted it.
    tool_output_max_chars: int = 200_000,
    exec_output_max_chars: int = 30_000,
) -> WikiConsultant | None:
    """Build the `ask_wiki` closure for a turn scoped to `collection_ids`, or
    `None` when none of them keeps a wiki.

    `reader_config` overrides the bundled reader (operators repoint the wiki
    agents at their own model, #56); each collection's own read-side guidance
    (#90) is appended on top, scoped to THAT collection's reader — a merged
    answer spanning collections has no single owner, so nothing collection-
    specific is applied to the fold.
    """
    wiki_ids = wiki_backed(spec, collection_ids)
    if not wiki_ids:
        return None

    rm = spec.get_resource_manager(Collection)
    store = WikiFileStore(spec)
    base_config = reader_config or default_wiki_reader_config()
    multi = len(wiki_ids) > 1

    def _config_for(cid: str) -> AgentConfig:
        try:
            coll = rm.get(cid).data
        except ResourceIDNotFoundError:  # pragma: no cover — vanished mid-turn
            return base_config
        assert isinstance(coll, Collection)
        return with_collection_guidance(base_config, coll.wiki_reader_guidance)

    def _name(cid: str) -> str:
        try:
            coll = rm.get(cid).data
        except ResourceIDNotFoundError:  # pragma: no cover — vanished mid-turn
            return cid
        assert isinstance(coll, Collection)
        return coll.name or cid

    async def consult(
        question: str, sink: OutputSink | None = None
    ) -> tuple[str, list[RetrievedPassage]]:
        blocks: list[str] = []
        passages: list[RetrievedPassage] = []
        for cid in wiki_ids:
            if sink is not None:
                sink(f"Reading the {_name(cid)} wiki…\n".encode())
            answer, found = await read_wiki(
                runner,
                wiki_store=store,
                wiki_sources=SpecstarWikiSources(spec, cid),
                collection_id=cid,
                question=question,
                agent_config=_config_for(cid),
                max_turns=reader_max_turns,
                tool_output_max_chars=tool_output_max_chars,
                exec_output_max_chars=exec_output_max_chars,
            )
            if not answer.strip():
                continue  # this wiki had nothing to say; the others may
            # Shift onto the shared list BEFORE extending it: the offset is what
            # the list held before this draft's sources were appended.
            block = shift_markers(answer, len(passages))
            # Name the wiki only when several were consulted — otherwise the
            # attribution is noise on a single-source answer.
            blocks.append(f"From the {_name(cid)} wiki:\n{block}" if multi else block)
            passages.extend(found)
        if not blocks:
            return ("The wiki has nothing on this.", [])
        return "\n\n".join(blocks), passages

    return consult
