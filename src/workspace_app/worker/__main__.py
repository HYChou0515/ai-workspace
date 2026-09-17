"""`python -m workspace_app.worker <jobtype>` — the standalone job worker (#312).

Settings-driven composition root + CLI glue (mirrors `workspace_app.workflow`
and `workspace_app.__main__`); both are omitted from coverage. The pure,
unit-tested seams (`select_coordinator`, `consume_until_stopped`) live in the
package's `__init__`.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ..config.schema import Settings
from ..coordinators import CoordinatorBundle, build_coordinators, build_ingestor
from . import _JOBTYPE_ATTR, API_REGISTRY_JOBTYPES, consume_until_stopped, select_coordinator

if TYPE_CHECKING:
    from ..kb.embedder import Embedder
    from ..kb.llm import ILlm
    from ..kb.retriever import Retriever

logger = logging.getLogger(__name__)


def build_bundle(
    settings: Settings, spec: object, *, config_dir: Path | None = None
) -> CoordinatorBundle:
    """Build the coordinator bundle from settings — the worker's composition
    root, mirroring the slice of ``__main__`` that feeds ``create_app`` (minus
    the HTTP app, sandbox, filestore and tool packages, which a worker has no
    use for)."""
    from specstar import SpecStar

    from .. import factories as f

    assert isinstance(spec, SpecStar)  # narrow the `object` param for ty (coverage-omitted)
    logger.info("worker: building coordinator bundle from settings")
    embedder = f.get_embedder(settings)
    kb_llm = f.get_kb_llm(settings)
    wiki_model, wiki_base, wiki_key = f.get_wiki_endpoint(settings)
    runner = f.get_runner(settings)
    catalog = f.get_agent_config_catalog(settings, config_dir=config_dir)
    card_drafter_llm = f.get_card_drafter_llm(settings)
    ingestor = build_ingestor(
        spec,
        embedder=embedder,
        pipeline=f.get_doc_pipeline(settings, embedder),
        chat_pipeline=f.get_chat_pipeline(settings, embedder, kb_llm),
        code_embedder=f.get_code_embedder(settings),
        parser_registry=f.get_parser_registry(settings),
        image_fetcher=f.get_image_fetcher(settings),
        image_embedder=f.get_image_embedder(settings),
    )
    bundle = build_coordinators(
        spec,
        ingestor=ingestor,
        runner=runner,
        catalog=catalog,
        message_queue_factory=f.build_message_queue_factory(settings),
        # No request user in a worker pod; specstar preserves each job's real
        # creator across the lifecycle (preserve_job_creator), so the default is
        # only the fallback for worker-authored artifacts (#83 acting-user).
        get_user_id=lambda: settings.server.default_user,
        # Same set `create_app` gates its routes on. A worker deciding an
        # `add_content` check with an empty set would refuse imports the API would
        # have allowed — the two must agree or the answer depends on which pod ran.
        superusers=frozenset(settings.server.superusers),
        quality_judge_llm=f.get_kb_quality_judge_llm(settings),
        card_drafter_llm=card_drafter_llm,
        sanity_llm_factory=f.get_sanity_llm_factory(settings),
        sanity_judge_llm=f.get_sanity_judge_llm(settings),
        # #535: retrieval-eval question generation runs on the KB llm.
        eval_llm=kb_llm,
        # #534: metric extraction runs on the KB llm.
        graph_llm=kb_llm,
        # #506 P6: the card-gen reconcile embeds candidates + cards with the same
        # embedder the ingestor uses (a worker pod builds one at line ~30).
        embedder=embedder,
        # #506: same reconcile thresholds as the API (settings.kb.cluster) so a
        # finalize on a worker pod dedups identically.
        cluster_tau=settings.kb.cluster.cluster_tau,
        suppress_tau=settings.kb.cluster.suppress_tau,
        update_tau=settings.kb.cluster.update_tau,
        merge_tau=settings.kb.cluster.merge_tau,
        graph_chunk_budget=settings.kb.graph.chunk_budget,
        wiki_maintainer_max_turns=settings.kb.wiki.maintainer_max_turns,
        wiki_model=wiki_model or "",
        wiki_llm_base_url=wiki_base or "",
        wiki_llm_api_key=wiki_key or "",
    )
    # #506 worker parity: build_coordinators wires the OPEN-loop one-shot drafter;
    # swap in the AGENTIC (closed-loop) one exactly like create_app, so a split-
    # deployment card-gen worker (run_consumers=false) also consults the KB before
    # drafting instead of re-asking / re-proposing what the collection documents. The
    # drafter's ask_knowledge_base leaf needs a retriever — built from the same
    # embedder / kb_llm the ingestor uses so query + document vectors are comparable.
    retriever = _build_retriever(settings, spec, embedder=embedder, kb_llm=kb_llm)
    if card_drafter_llm is not None:
        from ..api.card_drafter_agent import wire_agentic_card_drafter

        kb_chats = catalog.kb_chats()
        assert kb_chats, "a settings-built catalog always populates kb_chats"
        wire_agentic_card_drafter(
            bundle.card_gen,
            spec=spec,
            runner=runner,
            retriever=retriever,
            catalog=catalog,
            kb_agent_config=kb_chats[0],
            max_searches=settings.kb.max_searches_per_turn,
        )
    # #535: the eval batch handler retrieves via the SAME embedder + kb_llm the
    # ingestor uses, so query and document vectors are comparable. Injected here
    # because the Retriever is built after build_coordinators.
    if bundle.eval is not None:
        bundle.eval.set_retriever(retriever)
    return bundle


def _build_retriever(
    settings: Settings, spec: object, *, embedder: Embedder, kb_llm: ILlm | None
) -> Retriever:
    """The worker's ONE Retriever, from the same knobs `create_app` reads — the
    card drafter and the eval handler share it (two hand-copied kwargs blocks
    used to sit here, and a knob dropped from one of them reddened nothing)."""
    from specstar import SpecStar

    from .. import factories as f
    from ..kb.retriever import Retriever

    assert isinstance(spec, SpecStar)
    return Retriever(
        spec,
        embedder=embedder,
        llm=kb_llm,
        code_embedder=f.get_code_embedder(settings),
        enhancement_defaults=settings.kb.retrieval.enhancements,
        quality_weight=settings.kb.retrieval.quality_weight,
        quality_floor=settings.kb.retrieval.quality_floor,
        sparse_corpus_cap=settings.kb.retrieval.sparse_corpus_cap,
        context_chars=settings.kb.retrieval.context_chars,
        rerank_context_chars=settings.kb.retrieval.rerank_context_chars,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="workspace_app.worker",
        description="Standalone job worker (#312): block-consume ONE JobType off the shared queue.",
    )
    p.add_argument(
        "jobtype",
        choices=sorted(_JOBTYPE_ATTR),
        help="which JobType this worker drains (one worker pod per JobType)",
    )
    p.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to a config.yaml (falls back to $WORKSPACE_APP_CONFIG, then ./config.yaml).",
    )
    return p.parse_args(argv)


def build_coordinator(settings: Settings, jobtype: str, *, config_dir: Path | None) -> object:
    """The coordinator this worker consumes, from the composition its JobType
    needs. Most JobTypes come from the FastAPI-free ``build_bundle``; one in
    ``API_REGISTRY_JOBTYPES`` (blob-gc) must hold the API's WHOLE model
    registry, so it comes from the API's own composition — ``build_app``,
    built and never served — and the registries are equal by construction."""
    if jobtype in API_REGISTRY_JOBTYPES:
        from ..__main__ import build_app

        app = build_app(settings, config_dir=config_dir)
        return select_coordinator(app.state.coordinators, jobtype)
    from ..factories import get_spec

    get_user_id = lambda: settings.server.default_user  # noqa: E731
    spec = get_spec(settings, get_user_id=get_user_id)
    return select_coordinator(build_bundle(settings, spec, config_dir=config_dir), jobtype)


def main(argv: list[str] | None = None) -> None:
    from ..config.loader import load_with_provenance
    from ..observability.setup import install_llm_logging

    args = _parse_args(argv)
    settings, _provenance = load_with_provenance(config_path=args.config)
    config_dir = args.config.parent if args.config else None
    # Faithful LLM call log (default-on; WORKSPACE_LLM_LOG=0 to silence) so a
    # wiki/card-gen worker's LLM calls are as observable as the API's.
    install_llm_logging(settings)
    coordinator = build_coordinator(settings, args.jobtype, config_dir=config_dir)
    logger.info(
        "worker: booted jobtype=%s coordinator=%s", args.jobtype, type(coordinator).__name__
    )

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    logger.info("worker: consuming jobtype=%s, blocking until SIGTERM/SIGINT", args.jobtype)
    print(f"worker: consuming {args.jobtype!r} — block until SIGTERM/SIGINT", flush=True)
    consume_until_stopped(coordinator, stop)
    logger.info("worker: jobtype=%s drained and stopped", args.jobtype)
    print(f"worker: {args.jobtype!r} drained and stopped", flush=True)


if __name__ == "__main__":
    main()
