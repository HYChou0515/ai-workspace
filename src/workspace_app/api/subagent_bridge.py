"""Generic sub-agent bridge (#54).

ONE bridge the RCA agent's sub-agent-facing tools (``ask_knowledge_base``,
``infer_modules``, future) reach their KB sub-agent through: it resolves the
purpose's ``AgentConfig``, scopes the collections, drives ``answer_question`` on the
right runner (wiki-aware vs base), relays the sub-agent's live work to the calling
turn's sink, and logs + returns the citations. Lifted out of ``create_app`` so the
turn-driving glue and the workflow executor share one instance.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from specstar import QB, SpecStar

from ..agent.ask_kb import AskKbSpec
from ..agent.config_catalog import AgentConfigCatalog
from ..agent.context import KbSearchBudget, WikiSearchBudget
from ..kb.cited import record_citations
from ..kb.collections import (
    all_discoverable_collection_ids,
    partition_collection_disclosure,
    resolve_effective_scope,
)
from ..kb.doc_permission import denied_doc_ids
from ..kb.retriever import Enhancements, Retriever
from ..kb.wiki.consult import WikiConsultant
from ..perm import Actor
from ..resources import AgentConfig
from ..resources.groups import groups_of
from ..resources.kb import Citation, Collection
from ..sandbox.protocol import OutputSink
from .events import AgentEvent
from .kb_chat_routes import answer_question, kb_progress
from .runner import AgentRunner

logger = logging.getLogger(__name__)


class SubagentBridge:
    """Run the KB sub-agent for a named purpose and bubble its answer + citations up.
    See the module docstring for the seam this replaces."""

    def __init__(
        self,
        *,
        spec: SpecStar,
        runner: AgentRunner,
        retriever: Retriever,
        catalog: AgentConfigCatalog,
        purpose_fallbacks: dict[str, AgentConfig],
        get_user_id: Callable[[], str],
        max_searches: int | None,
        superusers: frozenset[str] = frozenset(),
        # #537: builds the KB sub-agent's wiki consultant for whatever collections
        # the call ends up scoped to. The app-side agent never touches the wiki
        # itself (#270) — it asks the KB agent, which decides whether the wiki or
        # the documents answer this question.
        wiki_consultant_factory: Callable[[list[str]], WikiConsultant | None] | None = None,
        # The deploy's output ceilings, handed to every KB sub-agent this bridge
        # spawns so a sub-agent can't run wider than the turn that asked it.
        tool_output_max_chars: int = 200_000,
        exec_output_max_chars: int = 30_000,
        # #605: the operator disclosure switch. False ⇒ the sub-agent's
        # discoverable set is empty, so the probe never runs and nothing is
        # withheld — one fewer ANN query per kb_search.
        disclosure_enabled: bool = True,
    ) -> None:
        self._spec = spec
        self._runner = runner
        self._retriever = retriever
        self._catalog = catalog
        self._purpose_fallbacks = purpose_fallbacks
        self._get_user_id = get_user_id
        self._max_searches = max_searches
        self._superusers = superusers
        self._wiki_consultant_factory = wiki_consultant_factory
        self._tool_output_max_chars = tool_output_max_chars
        self._exec_output_max_chars = exec_output_max_chars
        self._disclosure_enabled = disclosure_enabled

    async def run(
        self,
        purpose: str,
        payload: str,
        emit: OutputSink | None = None,
        origin_id: str | None = None,
        enhancements: Enhancements | None = None,
        reasoning_effort: str | None = None,
        collection_ids: list[str] | None = None,
        budget: KbSearchBudget | None = None,
        wiki_budget: WikiSearchBudget | None = None,
        ask_kb_spec: AskKbSpec | None = None,
        withheld_sink: list[str] | None = None,
        excluded_collection_ids: list[str] | None = None,
        # #605: the caller's per-turn disclosure pick. None ⇒ the operator
        # default (the constructor switch); False ⇒ skip the probe this call;
        # True cannot re-enable a globally-off deploy.
        disclosure: bool | None = None,
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
            logger.warning("subagent_bridge: no AgentConfig registered for purpose %r", purpose)
            raise ValueError(
                f"no AgentConfig registered for sub-agent purpose {purpose!r} "
                f"(catalog has: {sorted(self._catalog.purposes())}; bundled fallbacks: "
                f"{sorted(self._purpose_fallbacks)})"
            )

        # Global-collection concept: for the KB-answer path (ask_knowledge_base),
        # the effective scope UNIONS the always-in-scope global set and drops any
        # excluded ids — unspecified ⇒ global alone (the D5 hard cutover). This runs
        # BEFORE the permission partition below. infer_modules (#66: a focused
        # classifier over a SINGLE pre-resolved collection, ~1500 calls/turn) is
        # deliberately left out — it keeps exactly its configured collection; its
        # `None` still means "search them all".
        if purpose == "kb_chat":
            ids = resolve_effective_scope(
                self._spec, collection_ids, excluded=excluded_collection_ids or ()
            )
        elif collection_ids is not None:
            ids = collection_ids
        else:
            coll_rm = self._spec.get_resource_manager(Collection)
            ids = [
                r.info.resource_id  # ty: ignore[unresolved-attribute]
                for r in coll_rm.list_resources(QB.all())  # ty: ignore[invalid-argument-type]
            ]

        # #305 transitive gate: the sub-agent consults the KB on the SPEAKER's
        # behalf, so it may only search collections the speaker could read
        # directly (read_content). A private / since-tightened / unshared
        # collection is filtered out here — the AI can't launder access to it
        # through ask_knowledge_base. If there WERE candidate collections but the
        # speaker can read NONE of them, don't run the sub-agent over an empty
        # scope; say so (a tool result to the LLM, NOT a 403 — the turn
        # continues). An already-empty scope (no collections exist) keeps its
        # prior behaviour: run the agent, which reports it found nothing.
        speaker = self._get_user_id()
        # Permission-disclosure: split into what the speaker may read (searched) vs
        # merely see-exist (read_meta only — disclosed by the probe, not searched).
        # `readable` is byte-identical to the old readable_collection_ids result.
        part = partition_collection_disclosure(
            self._spec, ids, speaker, superusers=self._superusers
        )
        readable, discoverable = part.readable, part.discoverable
        if not self._disclosure_enabled or disclosure is False or withheld_sink is None:
            # #605: disclosure off — or NOBODY CONSUMES it (no withheld sink: an
            # infer_modules-style holder has no message to attach withheld
            # sources to) — ⇒ no probe universe at all. The tool skips the probe
            # when nothing is discoverable, so the feature costs nothing here.
            # (The searched scope is untouched.)
            discoverable = []
        elif purpose == "kb_chat":
            # #605: the disclosure universe is every discoverable collection —
            # not just the picked scope. "There IS an answer you can't read"
            # must fire for a collection the speaker didn't (or couldn't) pick;
            # explicit exclusions stay excluded (#551 — deliberate is deliberate).
            discoverable = all_discoverable_collection_ids(
                self._spec,
                speaker,
                excluded=excluded_collection_ids or (),
                superusers=self._superusers,
            )
        if ids and not readable and not discoverable:
            logger.warning(
                "subagent_bridge: speaker %s can neither read nor discover any of the "
                "collections for purpose %r",
                speaker,
                purpose,
            )
            return "No accessible knowledge sources for this query.", []
        ids = readable
        # #308: beyond the collection-level gate above, resolve which individual
        # docs the speaker's per-doc override blocks (read_content) so the KB
        # sub-agent's retriever excludes them — the speaker identity lives here, not
        # in the KB ctx `answer_question` builds.
        exclude_doc_ids = denied_doc_ids(
            self._spec,
            Actor.human(speaker, groups=groups_of(self._spec, speaker)),
            ids,
            "read_content",
            superusers=self._superusers,
        )

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

        logger.info(
            "subagent_bridge: running %s sub-agent for origin %s (speaker %s)",
            purpose,
            origin_id,
            speaker,
        )
        answer = await answer_question(
            self._runner,
            self._retriever,
            ids,
            payload,
            agent_config=cfg,
            spec=self._spec,
            enhancements=enhancements,
            reasoning_effort=reasoning_effort,
            on_event=relay,
            on_citations=log_cites,
            # #537: how the KB sub-agent consults the wiki, over the collections
            # THIS call resolved to. The calling app agent has no wiki tools of its
            # own — it delegates the whole question and the KB agent picks.
            wiki_consultant_factory=self._wiki_consultant_factory,
            # #195: the RCA → KB bridge is the same KB agent — cap its searches
            # too (None ⇒ unlimited when the operator lifts the cap).
            max_searches=self._max_searches,
            # #334 Q6: when the caller hands over a shared per-turn budget, every
            # ask_knowledge_base call in the turn draws from it; absent one, the
            # bridge falls back to a fresh budget from `max_searches`.
            budget=budget,
            # #506: a configured ask_knowledge_base (make_ask_knowledge_base) rides
            # its AskKbSpec (the sub-agent's authoritative tool set + prompt) and its
            # wiki-search cap through here; both None ⇒ the interactive path unchanged.
            wiki_budget=wiki_budget,
            tool_output_max_chars=self._tool_output_max_chars,
            exec_output_max_chars=self._exec_output_max_chars,
            ask_kb_spec=ask_kb_spec,
            # #308: the speaker's per-doc-override exclusion (resolved above).
            exclude_doc_ids=exclude_doc_ids,
            # Permission-disclosure: the read_meta-only collections the sub-agent's
            # kb_search probes; the disclosed subset bubbles into `withheld_sink`
            # (the parent turn's accumulator), then onto the assistant message.
            discoverable_collection_ids=discoverable,
            on_withheld=(withheld_sink.extend if withheld_sink is not None else None),
        )
        logger.debug(
            "subagent_bridge: %s sub-agent returned %d citations for origin %s",
            purpose,
            len(captured),
            origin_id,
        )
        return answer, captured
