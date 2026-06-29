"""Generic sub-agent bridge (#54).

ONE bridge the RCA agent's sub-agent-facing tools (``ask_knowledge_base``,
``infer_modules``, future) reach their KB sub-agent through: it resolves the
purpose's ``AgentConfig``, scopes the collections, drives ``answer_question`` on the
right runner (wiki-aware vs base), relays the sub-agent's live work to the calling
turn's sink, and logs + returns the citations. Lifted out of ``create_app`` so the
turn-driving glue and the workflow executor share one instance.
"""

from __future__ import annotations

from collections.abc import Callable

from specstar import QB, SpecStar

from ..agent.config_catalog import AgentConfigCatalog
from ..agent.context import KbSearchBudget
from ..kb.cited import record_citations
from ..kb.retriever import Enhancements, Retriever
from ..resources import AgentConfig
from ..resources.kb import Citation, Collection
from ..sandbox.protocol import OutputSink
from .events import AgentEvent
from .kb_chat_routes import answer_question, kb_progress
from .runner import AgentRunner


class SubagentBridge:
    """Run the KB sub-agent for a named purpose and bubble its answer + citations up.
    See the module docstring for the seam this replaces."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        runner: AgentRunner,
        kb_runner: AgentRunner,
        retriever: Retriever,
        catalog: AgentConfigCatalog,
        purpose_fallbacks: dict[str, AgentConfig],
        get_user_id: Callable[[], str],
        max_searches: int | None,
    ) -> None:
        self._spec = spec
        self._runner = runner
        self._kb_runner = kb_runner
        self._retriever = retriever
        self._catalog = catalog
        self._purpose_fallbacks = purpose_fallbacks
        self._get_user_id = get_user_id
        self._max_searches = max_searches

    async def run(
        self,
        purpose: str,
        payload: str,
        emit: OutputSink | None = None,
        origin_id: str | None = None,
        enhancements: Enhancements | None = None,
        reasoning_effort: str | None = None,
        wiki_query: bool = False,
        collection_ids: list[str] | None = None,
        budget: KbSearchBudget | None = None,
    ) -> tuple[str, list[Citation]]:
        """Generic sub-agent bridge — runs the sub-agent for `purpose`
        over every collection and returns its synthesized answer + the
        resolved citations. ONE bridge replaces the per-purpose
        `_ask_kb` / `_infer_modules` closures: tool impls own the
        arg→payload formatting (e.g. `infer_modules_impl` JSON-encodes
        its typed args); this bridge only knows how to ask the named
        sub-agent and bubble its work up.

        `emit` (when set) is the RCA run's output sink — the sub-agent's
        searches/reasoning relay to it as tool-log lines. `origin_id`
        is the calling investigation so its KB citations are logged
        against it. Returns the answer + citations; the tool impl
        stashes the citations into `ctx.subagent_citations[purpose]`."""
        cfg = self._catalog.default_for(purpose) or self._purpose_fallbacks.get(purpose)
        if cfg is None:
            raise ValueError(
                f"no AgentConfig registered for sub-agent purpose {purpose!r} "
                f"(catalog has: {sorted(self._catalog.purposes())}; bundled fallbacks: "
                f"{sorted(self._purpose_fallbacks)})"
            )

        # #66: infer_modules passes a pre-resolved collection scope (a single
        # configured collection, resolved ONCE per turn) so its ~1500 per-step
        # calls don't each re-list every collection. None ⇒ search them all
        # (ask_knowledge_base / unconfigured infer_modules).
        if collection_ids is not None:
            ids = collection_ids
        else:
            coll_rm = self._spec.get_resource_manager(Collection)
            ids = [
                r.info.resource_id  # ty: ignore[unresolved-attribute]
                for r in coll_rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
            ]

        def relay(ev: AgentEvent) -> None:
            if emit is None:
                return
            line = kb_progress(ev)
            if line:
                emit(line.encode())

        captured: list[Citation] = []

        def log_cites(cites: list[Citation]) -> None:
            record_citations(
                self._spec,
                cites,
                origin_kind="rca",
                origin_id=origin_id or "",
                cited_by=self._get_user_id(),
            )
            captured.extend(cites)

        # When the query opted into the wiki, drive the lookup with the
        # wiki-aware runner (chunk / wiki / both routing); otherwise the plain
        # base runner (chunk-RAG only).
        answer = await answer_question(
            self._kb_runner if wiki_query else self._runner,
            self._retriever,
            ids,
            payload,
            agent_config=cfg,
            spec=self._spec,
            enhancements=enhancements,
            reasoning_effort=reasoning_effort,
            wiki=wiki_query,
            on_event=relay,
            on_citations=log_cites,
            # #195: the RCA → KB bridge is the same KB agent — cap its searches
            # too (None ⇒ unlimited when the operator lifts the cap).
            max_searches=self._max_searches,
            # #334 Q6: when the caller hands over a shared per-turn budget, every
            # ask_knowledge_base call in the turn draws from it; absent one, the
            # bridge falls back to a fresh budget from `max_searches`.
            budget=budget,
        )
        return answer, captured
