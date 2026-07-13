"""`python -m workspace_app.worker <jobtype>` — the standalone job worker (#312).

Settings-driven composition root + CLI glue (mirrors `workspace_app.workflow`
and `workspace_app.__main__`); both are omitted from coverage. The pure,
unit-tested seams (`select_coordinator`, `consume_until_stopped`) live in the
package's `__init__`.
"""

from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path

from ..config.schema import Settings
from ..coordinators import CoordinatorBundle, build_coordinators, build_ingestor
from . import _JOBTYPE_ATTR, consume_until_stopped, select_coordinator


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
        quality_judge_llm=f.get_kb_quality_judge_llm(settings),
        card_drafter_llm=card_drafter_llm,
        sanity_llm_factory=f.get_sanity_llm_factory(settings),
        sanity_judge_llm=f.get_sanity_judge_llm(settings),
        # #506 P6: the card-gen reconcile embeds candidates + cards with the same
        # embedder the ingestor uses (a worker pod builds one at line ~30).
        embedder=embedder,
        # #506: same reconcile thresholds as the API (settings.kb.cluster) so a
        # finalize on a worker pod dedups identically.
        cluster_tau=settings.kb.cluster.cluster_tau,
        suppress_tau=settings.kb.cluster.suppress_tau,
        update_tau=settings.kb.cluster.update_tau,
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
    if card_drafter_llm is not None:
        from ..api.card_drafter_agent import wire_agentic_card_drafter
        from ..kb.retriever import Retriever

        kb_chats = catalog.kb_chats()
        assert kb_chats, "a settings-built catalog always populates kb_chats"
        wire_agentic_card_drafter(
            bundle.card_gen,
            spec=spec,
            runner=runner,
            retriever=Retriever(
                spec,
                embedder=embedder,
                llm=kb_llm,
                code_embedder=f.get_code_embedder(settings),
                enhancement_defaults=settings.kb.retrieval.enhancements,
                quality_weight=settings.kb.retrieval.quality_weight,
                quality_floor=settings.kb.retrieval.quality_floor,
            ),
            catalog=catalog,
            kb_agent_config=kb_chats[0],
            max_searches=settings.kb.max_searches_per_turn,
        )
    return bundle


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


def main(argv: list[str] | None = None) -> None:
    from ..config.loader import load_with_provenance
    from ..factories import get_spec
    from ..observability.setup import install_llm_logging

    args = _parse_args(argv)
    settings, _provenance = load_with_provenance(config_path=args.config)
    config_dir = args.config.parent if args.config else None
    # Faithful LLM call log (default-on; WORKSPACE_LLM_LOG=0 to silence) so a
    # wiki/card-gen worker's LLM calls are as observable as the API's.
    install_llm_logging(settings)
    get_user_id = lambda: settings.server.default_user  # noqa: E731
    spec = get_spec(settings, get_user_id=get_user_id)
    bundle = build_bundle(settings, spec, config_dir=config_dir)
    coordinator = select_coordinator(bundle, args.jobtype)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print(f"worker: consuming {args.jobtype!r} — block until SIGTERM/SIGINT", flush=True)
    consume_until_stopped(coordinator, stop)
    print(f"worker: {args.jobtype!r} drained and stopped", flush=True)


if __name__ == "__main__":
    main()
