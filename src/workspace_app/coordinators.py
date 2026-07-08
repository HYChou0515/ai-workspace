"""Shared construction of the background job coordinators (#312).

Both the API (`create_app`) and the standalone worker entrypoint
(`python -m workspace_app.worker`) build the SAME coordinator set from here, so
the API can run as a pure *producer* (its consumers gated off) while dedicated
worker pods each block-consume one JobType — each scaling independently under
its own k8s HPA.

The construction is FastAPI-free on purpose: the worker has no HTTP app. The
API layer still owns the request-stack wiring (route registration,
`install_reindex_on_edit`, `app.state` exposure); only the coordinator
*objects* are built here.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import msgspec

from .kb.card_drafter import LlmCardDrafter, NullCardDrafter
from .kb.card_gen_coordinator import CardGenCoordinator
from .kb.chunker import FixedTokenChunker
from .kb.index_coordinator import IndexCoordinator
from .kb.ingest import Ingestor
from .kb.quality import QualityScorer
from .kb.quality_coordinator import QualityCoordinator
from .kb.wiki.coordinator import WikiMaintenanceCoordinator
from .kb.wiki.maintainer import default_wiki_maintainer_config

if TYPE_CHECKING:
    from specstar import SpecStar

    from .agent.config_catalog import AgentConfigCatalog
    from .api.runner import AgentRunner
    from .kb.embedder import Embedder
    from .kb.llm import ILlm
    from .resources import AgentConfig

# The (model, reasoning_level) -> ILlm seam the sanity battery drives. Same
# shape kb_search uses; mirrors health.sanity.coordinator.LlmFactory.
LlmFactory = Callable[[str, str], "ILlm"]

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CoordinatorBundle:
    """The background job coordinators, built once and shared. ``quality`` and
    ``sanity`` are ``None`` when their LLM seam isn't wired (scoring / the
    model-sanity matrix are opt-in live-LLM features)."""

    wiki: WikiMaintenanceCoordinator
    index: IndexCoordinator
    card_gen: CardGenCoordinator
    quality: QualityCoordinator | None
    sanity: SanityBatteryCoordinator | None


def build_ingestor(
    spec: SpecStar,
    *,
    embedder: Embedder,
    pipeline: object | None = None,
    chunker: object | None = None,
    chat_pipeline: object | None = None,
    code_embedder: Embedder | None = None,
    parser_registry: object | None = None,
) -> Ingestor:
    """Build the KB ``Ingestor`` the index coordinator runs. Pipeline mode (P1)
    takes precedence; the legacy chunker stays for tests + offline runs that
    don't construct an LlamaIndex pipeline. Extracted from ``create_app`` so the
    worker builds the ingestor identically (#312)."""
    if pipeline is not None:
        return Ingestor(
            spec,
            pipeline=pipeline,  # ty: ignore[invalid-argument-type]
            chat_pipeline=chat_pipeline,  # ty: ignore[invalid-argument-type]
            embedder=embedder,
            code_embedder=code_embedder,
            parser_registry=parser_registry,  # ty: ignore[invalid-argument-type]
        )
    return Ingestor(
        spec,
        chunker=chunker or FixedTokenChunker(),  # ty: ignore[invalid-argument-type]
        chat_pipeline=chat_pipeline,  # ty: ignore[invalid-argument-type]
        embedder=embedder,
        code_embedder=code_embedder,
        parser_registry=parser_registry,  # ty: ignore[invalid-argument-type]
    )


def resolve_wiki_config(
    catalog: AgentConfigCatalog,
    purpose: str,
    fallback: Callable[[], AgentConfig],
    *,
    wiki_model: str = "",
    wiki_llm_base_url: str = "",
    wiki_llm_api_key: str = "",
) -> AgentConfig:
    """A wiki agent's config (catalog purpose, else bundled default) with the
    operator's optional model/endpoint override applied — so a stronger
    tool-calling model can drive the wiki agents without re-stating their
    prompts/tools. Shared by the maintainer (built here) and the reader/merge
    configs (built in ``create_app`` for the KB chat runner)."""
    cfg = catalog.default_for(purpose) or fallback()
    if wiki_model or wiki_llm_base_url or wiki_llm_api_key:
        cfg = msgspec.structs.replace(
            cfg,
            model=wiki_model or cfg.model,
            llm_base_url=wiki_llm_base_url or cfg.llm_base_url,
            llm_api_key=wiki_llm_api_key or cfg.llm_api_key,
        )
    return cfg


def build_coordinators(
    spec: SpecStar,
    *,
    ingestor: Ingestor,
    runner: AgentRunner,
    catalog: AgentConfigCatalog,
    message_queue_factory: object | None,
    get_user_id: Callable[[], str] | None,
    quality_judge_llm: ILlm | None,
    card_drafter_llm: ILlm | None,
    sanity_llm_factory: LlmFactory | None,
    sanity_judge_llm: ILlm | None,
    wiki_maintainer_max_turns: int = 40,
    wiki_model: str = "",
    wiki_llm_base_url: str = "",
    wiki_llm_api_key: str = "",
) -> CoordinatorBundle:
    """Construct the background job coordinators and wire the index→wiki→quality
    chain. The returned coordinators are *not* yet consuming — the caller (API
    lifespan or worker) decides which to ``start_consuming``."""
    # #50 P3: after a doc indexes, fold it into its collection's LLM wiki. The
    # coordinator serialises maintainer runs per collection so bursty uploads
    # coalesce instead of racing the wiki pages.
    # #281: a code collection rebuilds its wiki by reading source hierarchically,
    # driven by a deterministic summariser LLM — the SAME wiki model/endpoint the
    # maintainer agent uses (kb.wiki.llm). Empty model ⇒ None ⇒ code-wiki off.
    from .kb.llm import LitellmLlm

    code_wiki_llm = (
        LitellmLlm(wiki_model, wiki_llm_base_url or None, wiki_llm_api_key or None)
        if wiki_model
        else None
    )
    # #355: the code_sync job clones a code collection's git_url + ingests it on
    # the wiki worker (off the API), so the wiki coordinator owns the CodeRepoIngestor.
    from .kb.code_repo import CodeRepoIngestor

    wiki = WikiMaintenanceCoordinator(
        spec,
        runner,
        agent_config=resolve_wiki_config(
            catalog,
            "wiki_maintainer",
            default_wiki_maintainer_config,
            wiki_model=wiki_model,
            wiki_llm_base_url=wiki_llm_base_url,
            wiki_llm_api_key=wiki_llm_api_key,
        ),
        maintainer_max_turns=wiki_maintainer_max_turns,
        message_queue_factory=message_queue_factory,
        get_user_id=get_user_id,
        code_wiki_llm=code_wiki_llm,
        code_repo=CodeRepoIngestor(spec, ingestor=ingestor),
    )
    # #105: the doc-quality judge. Built only when a quality_judge model is wired;
    # otherwise None ⇒ the index coordinator skips scoring (docs stay un-scored).
    quality = (
        QualityCoordinator(spec, QualityScorer(quality_judge_llm))
        if quality_judge_llm is not None
        else None
    )
    # #175: a background job drafts context cards from a collection's selected
    # documents for human review (mirrors the wiki/index coordinators). #377: the
    # same drafter also raises clarification questions, and the index coordinator
    # hands each ready doc to it when the collection opted into auto_digest — so
    # it's built BEFORE index and injected below.
    drafter = (
        LlmCardDrafter(card_drafter_llm) if card_drafter_llm is not None else NullCardDrafter()
    )
    card_gen = CardGenCoordinator(
        spec,
        drafter,
        message_queue_factory=message_queue_factory,
        get_user_id=get_user_id,
    )
    # #82: indexing runs off the request path on a durable, cross-pod job queue.
    # It chains the index→wiki hook, so the wiki coordinator is handed in here.
    index = IndexCoordinator(
        spec,
        ingestor,
        wiki_coordinator=wiki,
        quality_coordinator=quality,
        card_gen_coordinator=card_gen,
        message_queue_factory=message_queue_factory,
        get_user_id=get_user_id,
    )
    # Model-sanity battery: a background consumer runs matrix cells (heavy live
    # LLM) off the request path. Only built when an LLM factory is wired.
    sanity = None
    if sanity_llm_factory is not None:
        from .health.sanity.coordinator import SanityBatteryCoordinator

        sanity = SanityBatteryCoordinator(
            spec,
            sanity_llm_factory,
            judge=sanity_judge_llm,
            message_queue_factory=message_queue_factory,
        )
        logger.info("coordinators: sanity battery coordinator wired")
    logger.info("coordinators: built wiki/index/card_gen coordinators")
    return CoordinatorBundle(
        wiki=wiki, index=index, card_gen=card_gen, quality=quality, sanity=sanity
    )


if TYPE_CHECKING:
    from .health.sanity.coordinator import SanityBatteryCoordinator
