"""AgentCardDrafter (#506 P5) — the agentic ``CardDrafter``.

``LlmCardDrafter`` digests a document with ONE ``ILlm`` pass: it sees only the
document, so "don't repeat what's already known" can only be enforced *after* the
fact (the finalize step's exact-key dedup). This drafter instead drives an agent
LOOP: before drafting, it can consult the knowledge base through a *configured*
``ask_knowledge_base`` (RAG + glossary + wiki, scoped to the document's own
collection and budgeted, #506 P3's ``AskKbSpec``) — so the digest reflects what
the KB already explains. It drafts only genuinely-new cards and asks only
genuinely-open questions instead of re-asking a term the wiki/glossary/RAG cover.

The loop's final assistant message is the same ``{cards, term_questions,
description_questions}`` JSON the one-shot drafter emitted, parsed by the exact
same tolerant :func:`_parse_digest`. ``digest`` stays SYNCHRONOUS — the card-gen
coordinator calls it inside a specstar consumer thread — so the async agent loop
runs under ``asyncio.run`` (a fresh loop per document, in that worker thread).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from ..agent.ask_kb import AskKbSpec, RunSubagent, make_ask_knowledge_base
from ..agent.context import AgentToolContext
from ..kb.card_drafter import _parse_digest, drafting_prompt
from ..kb.card_gen import DocDigest
from ..resources import AgentConfig
from .events import MessageDelta, RunError
from .runner import AgentRunner

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..agent.config_catalog import AgentConfigCatalog
    from ..kb.card_gen_coordinator import CardGenCoordinator
    from ..kb.retriever import Retriever

logger = logging.getLogger(__name__)

# The agentic drafter's user-message template (#506): the one-shot instructions
# PLUS "consult ask_knowledge_base before drafting", so it drafts only genuinely-new
# cards and asks only genuinely-open questions. Distinct from card_drafting.md, which
# the one-shot LlmCardDrafter still uses (it has no tools to consult).
_AGENTIC_PROMPT = (
    Path(__file__).parent.parent / "kb" / "prompts" / "card_drafting_agentic.md"
).read_text(encoding="utf-8")

_DRAFTER_ROLE = (
    "You are a meticulous knowledge-base editor. Consult the knowledge base with "
    "your tools before drafting, so the collection accumulates knowledge instead of "
    "repeating it. Draft what the document explains and the knowledge base lacks; "
    "leave out what is already known; ask about what remains genuinely unexplained."
)


def default_card_drafter_config(
    *, model: str = "", llm_base_url: str = "", llm_api_key: str = ""
) -> AgentConfig:
    """The bundled agentic-drafter ``AgentConfig``: it delegates every KB lookup
    through ``ask_knowledge_base`` (context isolation, #270), so that is its only
    tool — never the leaves ``kb_search`` / ``search_wiki`` (which would need a
    retriever / wiki store the drafter's own context doesn't carry).

    The loop needs a TOOL-calling model, so the composition root passes the kb_chat
    model + endpoint (``model``/``llm_base_url``/``llm_api_key``); each empty value
    keeps the ``AgentConfig`` default."""
    cfg = AgentConfig(
        name="Card Drafter",
        system_prompt=_DRAFTER_ROLE,
        allowed_tools=["ask_knowledge_base"],
    )
    if model or llm_base_url or llm_api_key:
        cfg = msgspec.structs.replace(
            cfg,
            model=model or cfg.model,
            llm_base_url=llm_base_url or cfg.llm_base_url,
            llm_api_key=llm_api_key or cfg.llm_api_key,
        )
    return cfg


def default_drafter_ask_kb_spec() -> AskKbSpec:
    """The drafter's base ``AskKbSpec`` — the sub-agent it delegates to gets ONLY
    the glossary (existing context cards), NOT chunk search or the wiki.

    #506/#577 follow-up (root cause of "1000 docs → ~5 proposals, all suppressed"):
    the drafter extracts card candidates FROM a document, then — if it also searches
    the same collection's RAG + wiki — nearly every candidate is "already explained"
    in the corpus/wiki, so it declines to draft the card. That is the structural
    self-suppression #577 fixed at the RECONCILE stage; the drafter is a separate
    code path that was never fixed. A concept appearing in the wiki is exactly the
    reason it DESERVES a card, not a reason to omit it. So the drafter must not grade
    its own cards against the source corpus: ``kb_search_max=0`` + ``wiki_search_max=0``
    leave only ``lookup_glossary`` (``allowed_tools`` → ``["lookup_glossary"]``), so
    "already known" means "already has a card", never "the wiki/another doc mentions
    it". Card-vs-card dedup stays with reconcile (#577)."""
    return AskKbSpec(kb_search_max=0, wiki_search_max=0, glossary=True)


class AgentCardDrafter:
    """Digest a document by running an agent loop (see the module docstring). The
    ``build_context`` factory maps a document's ``collection_id`` to the loop's
    ``AgentToolContext`` — where the composition root wires the configured
    ``ask_knowledge_base`` (scoped to that collection), the retriever, and the
    drafter ``AgentConfig``. Keeping context assembly outside the drafter lets the
    loop-and-parse mechanism be tested with a scripted runner and a trivial
    context, and lets the drafter stay ignorant of how the KB delegation is
    wired."""

    def __init__(
        self,
        runner: AgentRunner,
        build_context: Callable[[str], AgentToolContext],
        *,
        prompt_template: str | None = None,
        max_cards: int = 30,
    ) -> None:
        self._runner = runner
        self._build_context = build_context
        # Default to the agentic template (consult-then-draft), NOT the one-shot
        # card_drafting.md — this drafter HAS a tool to consult.
        self._template = prompt_template or _AGENTIC_PROMPT
        self._max_cards = max_cards

    def digest(self, *, doc_path: str, doc_text: str, collection_id: str = "") -> DocDigest:
        """Run the agent loop over one document and parse its final message into a
        ``DocDigest``. Synchronous (the coordinator's consumer thread has no running
        loop) — the async loop runs under ``asyncio.run``."""
        return asyncio.run(
            self._adigest(doc_path=doc_path, doc_text=doc_text, collection_id=collection_id)
        )

    async def _adigest(self, *, doc_path: str, doc_text: str, collection_id: str) -> DocDigest:
        ctx = self._build_context(collection_id)
        prompt = drafting_prompt(doc_text, doc_path=doc_path, template=self._template)
        parts: list[str] = []
        async for ev in self._runner.run(prompt, ctx):
            if isinstance(ev, MessageDelta) and not ev.reasoning:
                parts.append(ev.text)
            elif isinstance(ev, RunError):
                # The runner exhausted its retry budget — a genuine give-up. Raise
                # so the coordinator marks this doc FAILED (#414 partial tolerance)
                # instead of parsing whatever partial text leaked before the error
                # into a falsely-green empty digest (#494).
                logger.error("AgentCardDrafter: run failed for doc %s: %s", doc_path, ev.message)
                raise RuntimeError(ev.message)
        return _parse_digest("".join(parts), max_cards=self._max_cards, doc_path=doc_path)


def drafter_context_builder(
    *,
    bridge_run: RunSubagent,
    agent_config: AgentConfig,
    base_spec: AskKbSpec,
) -> Callable[[str], AgentToolContext]:
    """The composition seam that gives :class:`AgentCardDrafter` its per-document
    context. Returns a ``build_context(collection_id)`` closure: the drafting agent
    runs with ``agent_config`` (which grants ``ask_knowledge_base``) and a
    ``run_subagent`` wired to a *spec-configured* ask_knowledge_base whose scope is
    FORCED to that document's own collection — so a delegated KB lookup searches
    exactly ``[collection_id]`` at the base spec's budgets, never the whole KB.

    ``bridge_run`` is the shared :class:`SubagentBridge`'s ``run`` (built once by
    the composition root); ``base_spec`` carries the drafter's budgets / prompt /
    tool choices. This is the drafter half of the factory the interactive KB agent
    (Task #1) also consumes — same :func:`make_ask_knowledge_base`, different spec."""

    def build(collection_id: str) -> AgentToolContext:
        spec = replace(base_spec, scope=[collection_id]) if collection_id else base_spec
        return AgentToolContext(
            agent_config=agent_config,
            collection_ids=[collection_id] if collection_id else [],
            run_subagent=make_ask_knowledge_base(spec, bridge_run),
        )

    return build


def wire_agentic_card_drafter(
    coordinator: CardGenCoordinator,
    *,
    spec: SpecStar,
    runner: AgentRunner,
    retriever: Retriever,
    catalog: AgentConfigCatalog,
    kb_agent_config: AgentConfig,
    max_searches: int | None,
) -> None:
    """Swap ``coordinator``'s open-loop one-shot drafter for the agentic
    :class:`AgentCardDrafter` (#506 P5). #506/#577 follow-up: the drafter's
    ``ask_knowledge_base`` consults ONLY the glossary of existing cards — not RAG,
    not the wiki — because grading a card against the same corpus it was extracted
    from suppresses nearly everything (the self-suppression #577 fixed at reconcile;
    the drafter is a separate code path). So the drafter drafts a card whenever the
    document defines a term, skipping only an exact existing-card duplicate.

    This is the SINGLE seam both composition roots call — ``create_app`` and the
    split-deployment worker's ``build_bundle`` — so a card-gen job runs the closed
    loop no matter which pod drains it (#506 worker parity). Without it the worker
    (``run_consumers=false``) would keep the open-loop drafter.

    The drafter's own subagent bridge runs headless under the system-superuser
    identity so the #305 collection-read gate passes ``[collection_id]`` through (a
    background job carries no request speaker); safe because the drafter's spec
    FORCES scope to the document's collection, so superuser status can't widen the
    search. No wiki consultant is wired: the glossary-only spec never grants
    ``ask_wiki``, so there is nothing to delegate to."""
    from ..kb.help_collection import HELP_SYSTEM_USER
    from .subagent_bridge import SubagentBridge

    bridge = SubagentBridge(
        spec=spec,
        runner=runner,
        retriever=retriever,
        catalog=catalog,
        purpose_fallbacks={"kb_chat": kb_agent_config},
        get_user_id=lambda: HELP_SYSTEM_USER,
        max_searches=max_searches,
        superusers=frozenset({HELP_SYSTEM_USER}),
    )
    coordinator.set_drafter(
        AgentCardDrafter(
            runner,
            drafter_context_builder(
                bridge_run=bridge.run,
                # The loop needs a TOOL-calling model; the card_drafter_llm is only
                # the "drafting enabled" flag, so drive the loop with the kb_chat
                # model + endpoint.
                agent_config=default_card_drafter_config(
                    model=kb_agent_config.model,
                    llm_base_url=kb_agent_config.llm_base_url,
                    llm_api_key=kb_agent_config.llm_api_key,
                ),
                # Pass the glossary-only spec UNCHANGED. `max_searches` is
                # deliberately NOT applied here (it stays a bridge-level cap for
                # OTHER sub-agents): re-enabling the drafter's chunk/wiki search is
                # exactly the self-suppression this fix removes (#506/#577).
                base_spec=default_drafter_ask_kb_spec(),
            ),
        )
    )
