"""WikiAwareRunner (#50 P5) — route a KB chat turn across chunk-RAG and the
LLM wiki, merging when both apply.

This is an ``AgentRunner`` the KB chat engine drives. It is a **pure
pass-through to the base runner for the default chunk-RAG path** (the wiki flag
off, or no ``use_wiki`` collection) — so existing KB chat behaviour is byte
unchanged and can't regress. Only when a query opts into the wiki
(``ctx.wiki_query``) AND a collection has ``use_wiki`` does it orchestrate:

  - **wiki-only** (no ``use_rag`` collection, one wiki): stream the wiki reader
    directly.
  - **both / multi**: run each source's answer to completion (chunk draft +
    one wiki draft per ``use_wiki`` collection), renumber their ``[n]`` markers
    into one shared source list, then stream a merge agent that integrates the
    drafts — citations preserved (plan §2.5: two agents each answer, then merge).

Every sub-answer and the merge run through the SAME base runner — no separate
LLM plumbing. The combined passages are written onto the turn's ``ctx`` so the
route's ``parse_citations`` resolves the merged ``[n]`` against them.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ...agent.context import AgentToolContext
from ...files import WorkspaceFiles
from ...resources import AgentConfig, Collection
from ...resources.kb import RetrievedPassage
from .guidance import with_collection_guidance
from .reader import default_wiki_reader_config
from .sources import SpecstarWikiSources
from .store import WikiFileStore

if TYPE_CHECKING:
    from ...api.events import AgentEvent
    from ...api.runner import AgentRunner

_MARKER = re.compile(r"[\[［【]\s*(\d+)\s*[\]］】]")
_MERGE_PROMPT = (Path(__file__).parent.parent / "prompts" / "wiki_merge.md").read_text(
    encoding="utf-8"
)


def default_wiki_merge_config() -> AgentConfig:
    """The bundled merge AgentConfig — no tools; it only integrates the drafts."""
    return AgentConfig(name="Wiki Merge", system_prompt=_MERGE_PROMPT, allowed_tools=[])


def _shift_markers(text: str, offset: int) -> str:
    """Shift every ``[n]`` in `text` by `offset` (so a draft's local citation
    numbers line up with their slice of the shared source list)."""
    if offset == 0:
        return text
    return _MARKER.sub(lambda m: f"[{int(m.group(1)) + offset}]", text)


class WikiAwareRunner:
    """Wraps the base runner; routes KB chat turns across chunk / wiki / both."""

    def __init__(
        self,
        base: AgentRunner,
        spec: SpecStar,
        *,
        reader_config: AgentConfig | None = None,
        merge_config: AgentConfig | None = None,
        reader_max_turns: int = 24,
    ) -> None:
        self._base = base
        self._spec = spec
        self._coll_rm = spec.get_resource_manager(Collection)
        self._wiki_store = WikiFileStore(spec)
        self._reader_config = reader_config or default_wiki_reader_config()
        self._merge_config = merge_config or default_wiki_merge_config()
        self._reader_max_turns = reader_max_turns

    def _classify(self, collection_ids: list[str]) -> tuple[list[str], list[str]]:
        """(use_rag ids, use_wiki ids) among the chat's collections."""
        rag: list[str] = []
        wiki: list[str] = []
        for cid in collection_ids:
            try:
                coll = self._coll_rm.get(cid).data
            except Exception:  # noqa: BLE001 — unknown/deleted collection: skip
                continue
            if not isinstance(coll, Collection):
                continue
            if coll.use_rag:
                rag.append(cid)
            if coll.use_wiki:
                wiki.append(cid)
        return rag, wiki

    def _chunk_ctx(self, ctx: AgentToolContext, rag_ids: list[str]) -> AgentToolContext:
        """A chunk-RAG context like the turn's, scoped to the use_rag collections,
        with a fresh passage registry."""
        return AgentToolContext(
            retriever=ctx.retriever,
            collection_ids=rag_ids,
            agent_config=ctx.agent_config,
            history=ctx.history,
            reasoning_effort=ctx.reasoning_effort,
            kb_enhancements=ctx.kb_enhancements,
        )

    def _reader_config_for(self, cid: str) -> AgentConfig:
        """Append the collection's reader guidance (#90) onto the bundled reader
        config — so this wiki's answers follow the operator's read-side guidance.
        Scoped to the wiki reader ONLY; the chunk-RAG and cross-collection merge
        agents never see it. A vanished collection falls back to the bundled
        config."""
        try:
            coll = self._coll_rm.get(cid).data
        except ResourceIDNotFoundError:  # pragma: no cover — collection vanished mid-query
            return self._reader_config
        assert isinstance(coll, Collection)  # the Collection manager yields a Collection (ty)
        return with_collection_guidance(self._reader_config, coll.wiki_reader_guidance)

    def _wiki_ctx(self, ctx: AgentToolContext, cid: str) -> AgentToolContext:
        """A sandbox-free wiki-reader context over one collection's wiki."""
        return AgentToolContext(
            investigation_id=cid,
            filestore=self._wiki_store,
            files=WorkspaceFiles(self._wiki_store),
            sandbox=None,
            agent_config=self._reader_config_for(cid),
            history=ctx.history,
            reasoning_effort=ctx.reasoning_effort,
            wiki_sources=SpecstarWikiSources(self._spec, cid),
            wiki_cite_sources=True,
            max_turns=self._reader_max_turns,  # navigating + grounding is multi-step
        )

    async def _collect(self, prompt: str, sub: AgentToolContext) -> str:
        """Run a sub-agent to completion (no streaming), returning its answer
        text. Its passages accumulate on `sub.kb_passages`."""
        from ...api.events import MessageDelta, RunError

        parts: list[str] = []
        error: str | None = None
        async for ev in self._base.run(prompt, sub):
            if isinstance(ev, MessageDelta) and not ev.reasoning:
                parts.append(ev.text)
            elif isinstance(ev, RunError):
                error = ev.message
        if error is not None:
            return ""  # a failed source contributes no draft
        return "".join(parts)

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        from ...api.events import MessageDelta

        rag_ids, wiki_ids = self._classify(ctx.collection_ids)
        # Default path: wiki not opted in (or no wiki collection) → pure
        # chunk-RAG, exactly as before. Zero behaviour change.
        if not (ctx.wiki_query and wiki_ids):
            async for ev in self._base.run(prompt, ctx):
                yield ev
            return

        # Wiki path. The single wiki-only collection streams its reader live;
        # anything multi-source collects drafts and streams the merge.
        if not rag_ids and len(wiki_ids) == 1:
            sub = self._wiki_ctx(ctx, wiki_ids[0])
            async for ev in self._base.run(prompt, sub):
                yield ev
            ctx.kb_passages[:] = sub.kb_passages
            return

        # Collect a draft per source: chunk (over use_rag collections) + one
        # per use_wiki collection.
        drafts: list[tuple[str, list[RetrievedPassage]]] = []
        if rag_ids:
            sub = self._chunk_ctx(ctx, rag_ids)
            answer = await self._collect(prompt, sub)
            if answer.strip():
                drafts.append((answer, list(sub.kb_passages)))
        for cid in wiki_ids:
            sub = self._wiki_ctx(ctx, cid)
            answer = await self._collect(prompt, sub)
            if answer.strip():
                drafts.append((answer, list(sub.kb_passages)))

        # Renumber each draft's [n] into one shared source list.
        combined: list[RetrievedPassage] = []
        blocks: list[str] = []
        for answer, passages in drafts:
            blocks.append(_shift_markers(answer, len(combined)))
            combined.extend(passages)
        ctx.kb_passages[:] = combined

        if not blocks:
            yield MessageDelta(
                text="I couldn't find anything relevant in the documents or the wiki."
            )
            return
        if len(blocks) == 1:
            # Only one source produced an answer — stream it as-is (it already
            # ran through a streaming sub-agent); no merge needed.
            yield MessageDelta(text=blocks[0])
            return

        merge_q = _merge_question(prompt, blocks)
        merge_ctx = AgentToolContext(
            agent_config=self._merge_config,
            kb_passages=ctx.kb_passages,  # shared list → route resolves [n]
            reasoning_effort=ctx.reasoning_effort,
        )
        async for ev in self._base.run(merge_q, merge_ctx):
            yield ev


def _merge_question(question: str, blocks: list[str]) -> str:
    """Build the merge agent's user message: the question + the drafts (already
    renumbered into the shared source list)."""
    parts = [f"Question: {question}", ""]
    for i, block in enumerate(blocks, 1):
        parts.append(f"--- Draft {i} ---")
        parts.append(block)
        parts.append("")
    return "\n".join(parts)
