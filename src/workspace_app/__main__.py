"""Default entry point: `uv run python -m workspace_app`.

Thin composition root: read `Settings` from config.yaml (+ ${ENV_VAR}
interpolation), build each Protocol implementation via the
`factories.get_*` functions, and wire them into `create_app`. To change
which implementation backs a seam, edit `configs/config.yaml` — no code
change. To compose differently in code, import `workspace_app.factories`
(or `create_app`) directly.

See `configs/config.example.yaml` for the full schema. Env vars are
referenced via `${VAR}` inside YAML string values (Q2 of the
config-refactor grill — the only env override mechanism).
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import uvicorn

from workspace_app.api import create_app
from workspace_app.config.dump import emit_config_dump
from workspace_app.config.loader import load_with_provenance
from workspace_app.factories import (
    build_message_queue_factory,
    get_agent_config_catalog,
    get_card_drafter_llm,
    get_chat_pipeline,
    get_check_registry,
    get_code_embedder,
    get_designed_pptx_vlm,
    get_doc_pipeline,
    get_embedder,
    get_event_bus,
    get_filestore,
    get_goal_checker_llm,
    get_image_embedder,
    get_image_fetcher,
    get_infer_modules_run_config,
    get_kb_describer,
    get_kb_llm,
    get_kb_quality_judge_llm,
    get_parser_registry,
    get_replay_service,
    get_runner,
    get_sandbox,
    get_sandbox_filestore,
    get_sanity_judge_llm,
    get_sanity_llm_factory,
    get_sanity_models,
    get_spec,
    get_wiki_endpoint,
)
from workspace_app.monitor import SpecstarMonitor
from workspace_app.observability.boot import boot_step
from workspace_app.observability.setup import install_llm_logging
from workspace_app.tooling.packages import PACKAGES, PREBUILT_DIR
from workspace_app.tooling.registry import discover_packages


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI surface — currently just `--config / -c` to point at a
    specific config.yaml. Kept separate from `main()` so tests can
    drive it without spinning up uvicorn."""
    p = argparse.ArgumentParser(
        prog="workspace_app",
        description="RCA / KB workspace agent backend.",
    )
    p.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help=(
            "Path to a config.yaml. Falls back to $WORKSPACE_APP_CONFIG, then "
            "./config.yaml when both are unset."
        ),
    )
    return p.parse_args(argv)


def main() -> None:
    import os
    import sys

    args = _parse_args()
    settings, provenance = load_with_provenance(config_path=args.config)
    # Tell the operator which config file (if any) was applied — useful
    # when a setting "isn't taking effect" and it's actually the wrong
    # file getting read.
    cfg_env = os.environ.get("WORKSPACE_APP_CONFIG")
    cfg_default = Path("./config.yaml")
    if args.config:
        config_dir = args.config.parent
        print(f"  config: {args.config}  (--config)")
    elif cfg_env:
        config_dir = Path(cfg_env).parent
        print(f"  config: {cfg_env}  (WORKSPACE_APP_CONFIG)")
    elif cfg_default.is_file():
        config_dir = cfg_default.parent
        print(f"  config: {cfg_default}")
    else:
        config_dir = None
        print("  config: (none; bundled defaults)")
    # Observability: print the resolved config (provenance-annotated, secrets
    # masked) and write the full real-value copy next to config.yaml (0600).
    # Best-effort — never blocks boot.
    emit_config_dump(settings, provenance, config_dir=config_dir, stream=sys.stdout)
    # Single current-user seam, threaded into BOTH get_spec (so specstar stamps
    # `created_by`) and create_app (access layer + KB doc-id minting) so they
    # never diverge — a divergence silently breaks KB cross-ref links for any
    # non-default user (#41). Default = the configured single tenant; a real
    # deploy overrides this with a cookie/JWT reader.
    get_user_id = lambda: settings.server.default_user  # noqa: E731
    # Observability feature B: register the faithful LLM call logger into
    # litellm.callbacks before any LLM call. Best-effort; default-on with the
    # WORKSPACE_LLM_LOG=0 off-switch.
    if install_llm_logging(settings) is not None:
        print(
            f"  llm log: ON → {settings.observability.llm_log.dir}/ "
            f"(set WORKSPACE_LLM_LOG=0 to silence)"
        )
    else:
        print("  llm log: off (set WORKSPACE_LLM_LOG=1 or observability.llm_log.enabled: true)")
    # #208: from here to a live server is a string of blocking steps that used
    # to print nothing — any one stalling looked identical (silence after the
    # config dump). Each is now narrated (→ enter / ✓ done / ✗ failed) so a hang
    # names itself. The Postgres-down stall lands in create_app's `spec.apply`.
    with boot_step("connect backend & register models (get_spec)"):
        spec = get_spec(settings, get_user_id=get_user_id)
    # #219: one-time migration of any pre-#219 inline-bytes workspace files into
    # the per-file Binary shape the rewritten SpecstarFileStore now expects.
    # Idempotent (returns 0 once the legacy rows are consumed) and only relevant
    # for the specstar filestore — memory is non-persistent, so nothing to move.
    if settings.filestore.kind == "specstar":
        with boot_step("migrate workspace files (#219)"):
            from workspace_app.filestore.migrate import migrate_inline_to_binary

            migrate_inline_to_binary(spec)
    # Deploy-level provisionable tool packages (#25). discover_packages reads
    # the prebuilt bundles under PREBUILT_DIR (run `scripts/prebuild_tools.py`);
    # a real deployment swaps tool_packages.PACKAGES for its own dict. They're
    # gated per-investigation by the agent config's allowed_tools (colon
    # syntax: `"pkg"` for the whole package, `"pkg:cmd"` for one command), so
    # the tool-demo template is what turns them on.
    #
    # discover_packages is fail-loud as of the May-30 incident: a missing
    # PREBUILT_DIR or any half-built subdir raises with the offender's path
    # (silent-skip was the root cause — the agent ran for hours with zero
    # tool packages and nobody knew). The skip path is the explicit one:
    # if the deployer cleared PACKAGES they're opting out, no prebuild
    # required.
    # #63: `tools.mode: uv-run` is a lightweight DEBUG mode — instead of the
    # heavy prebuilt bundles, each package runs from its live source via
    # `uv run`, so editing a tool takes effect immediately. The bundles are
    # built fresh at startup into a sibling dir and the sandbox is forced
    # non-isolated (handled in get_sandbox).
    tools_root = PREBUILT_DIR
    with boot_step("discover tool packages"):
        if PACKAGES and settings.tools.mode == "uv-run":
            from workspace_app.tooling.prebuild import provision_uvrun

            tools_root = PREBUILT_DIR.parent / f"{PREBUILT_DIR.name}-uvrun"
            provision_uvrun(PACKAGES, tools_root)
            packages = discover_packages(tools_root)
            print(f"  tools: uv-run debug mode (live source via uv run) → {tools_root}")
        elif PACKAGES:
            packages = discover_packages(PREBUILT_DIR)
            missing = set(PACKAGES) - {p.name for p in packages}
            if missing:
                raise RuntimeError(
                    f"tool packages declared in PACKAGES but missing from prebuilt: "
                    f"{sorted(missing)}. Rerun `uv run python scripts/prebuild_tools.py`."
                )
        else:
            packages = []
    # The sandbox mounts the tools dir read-only at /.tools (outside the
    # workspace) — no per-sandbox copy. Only point at it once it's built.
    tools_dir = tools_root if packages else None
    with boot_step("init embedder"):
        embedder = get_embedder(settings)
    with boot_step("init KB LLM"):
        kb_llm = get_kb_llm(settings)
    with boot_step("init card-drafter LLM"):
        card_drafter_llm = get_card_drafter_llm(settings)
    with boot_step("init KB quality judge LLM"):
        quality_judge_llm = get_kb_quality_judge_llm(settings)
    with boot_step("init sanity judge LLM"):
        sanity_judge_llm = get_sanity_judge_llm(settings)
    # #56: the wiki agents' model/endpoint resolve from kb.wiki.llm (the
    # preset-reference pattern), not the old flat runner.wiki_*. Empty
    # ⇒ create_app's wiki configs keep their in-code default model.
    wiki_model, wiki_llm_base_url, wiki_llm_api_key = get_wiki_endpoint(settings)
    # #66: the infer_modules tool's per-step KB depth / reasoning / fan-out.
    infer_cfg = get_infer_modules_run_config(settings)
    with boot_step("init sandbox"):
        sandbox = get_sandbox(settings, tools_dir=tools_dir)
    with boot_step("build app (create_app)"):
        # #501: the API's SPECSTAR filestore (WorkspaceFile registration / blob GC /
        # #219 / shared blob pool with KB·wiki) is DISTINCT from the SANDBOX's durable
        # store. Build the API one first — constructing it registers WorkspaceFile — then
        # the sandbox durable store, which REUSES it as the nfs_tree M2 fallback. Default
        # (sandbox.durable.kind "") ⇒ sandbox_filestore IS api_filestore (unchanged).
        api_filestore = get_filestore(settings, spec)
        sandbox_filestore = get_sandbox_filestore(settings, spec, api_filestore)
        app = create_app(
            spec=spec,
            get_user_id=get_user_id,
            # #262: same superuser set threaded into get_spec(...) above, so the
            # route-level authorize() guards agree with the storage access_scope.
            superusers=frozenset(settings.server.superusers),
            sandbox=sandbox,
            filestore=sandbox_filestore,
            # #219: single-file upload cap (streaming keeps RAM flat regardless).
            max_file_size=settings.filestore.max_file_size,
            # #245: per-workspace total-size quota (protects the shared disk root).
            workspace_quota=settings.filestore.workspace_quota,
            # #345: scratch-vol soft cap — the idle reaper recycles any item whose
            # working dir grows past this (0 ⇒ off), so one runaway workspace can't
            # fill the shared scratch volume the whole fleet shares.
            # #492: when the HTTP sandbox-host owns durable via its NFS archive, the
            # app skips its own restore/mirror and writes back through the host's
            # /persist. Only meaningful for kind: http (other backends ignore it).
            host_managed_durable=(
                settings.sandbox.kind == "http"
                and settings.sandbox.http is not None
                and settings.sandbox.http.host_managed_durable
            ),
            # #245: blob-GC sweeper — reclaims orphaned blobs (0 ⇒ off).
            gc_interval=(
                timedelta(seconds=settings.filestore.gc_interval_sec)
                if settings.filestore.gc_interval_sec
                else None
            ),
            gc_t1=settings.filestore.gc_t1,
            gc_t2=settings.filestore.gc_t2,
            runner=get_runner(settings),
            agent_config_catalog=get_agent_config_catalog(settings, config_dir=config_dir),
            kb_embedder=embedder,
            # P3.0: code-specialised embedder; None ⇒ code collections fall
            # back to the default embedder.
            kb_code_embedder=get_code_embedder(settings),
            kb_image_embedder=get_image_embedder(settings),
            # P1: LlamaIndex IngestionPipeline replaces the hand-rolled chunker.
            # Tests/offline runs still pass `kb_chunker=` directly to create_app.
            kb_pipeline=get_doc_pipeline(settings, embedder),
            # Issue #39: custom parsers (kb.parsers) + VLM-backed bundled
            # parsers (kb.vlm_llm), minus kb.parsers_disabled.
            kb_parser_registry=get_parser_registry(settings),
            kb_image_fetcher=get_image_fetcher(settings),
            # Issue #51: LLM sanity checks — fast set blocks boot, full
            # capability round runs async; FE re-runs via /health/checks.
            check_registry=get_check_registry(settings),
            # Issue #51 P4: replay diagnostics (turn / doc) — pure LLM
            # probes against the live endpoints, no tool execution.
            replay_service=get_replay_service(settings, kb_llm),
            # Model-sanity battery (Diagnostics matrix): a live behavioural probe
            # per configured chat model × reasoning level, on the same ILlm seam
            # kb_search uses.
            sanity_llm_factory=get_sanity_llm_factory(settings),
            sanity_models=get_sanity_models(settings),
            sanity_judge_llm=sanity_judge_llm,
            # P2: chat → knowledge insight extraction (None when no KB llm wired).
            kb_chat_pipeline=get_chat_pipeline(settings, embedder, kb_llm),
            kb_llm=kb_llm,
            card_drafter_llm=card_drafter_llm,
            quality_judge_llm=quality_judge_llm,
            # #112: the VLM describer the read_image agent tool uses (shared with
            # the VLM-backed ingestion parsers); None when kb.vlm_llm is unset.
            vlm_describer=get_kb_describer(settings),
            deck_vlm=get_designed_pptx_vlm(settings),
            kb_retrieval_enhancements=settings.kb.retrieval.enhancements,
            kb_quality_weight=settings.kb.retrieval.quality_weight,
            kb_quality_floor=settings.kb.retrieval.quality_floor,
            kb_sparse_corpus_cap=settings.kb.retrieval.sparse_corpus_cap,
            # #195: per-turn kb_search cap for the KB chat turn + ask_knowledge_base
            # bridge (null in config ⇒ unlimited).
            kb_max_searches_per_turn=settings.kb.max_searches_per_turn,
            kb_disclosure_enabled=settings.kb.disclosure.enabled,
            # #334: ceiling for the composer's per-message kb_search-count pick.
            kb_max_searches_ceiling=settings.kb.max_searches_ceiling,
            # #613 P3: the turn-end goal checker + the hard auto-continue budget.
            goal_checker_llm=get_goal_checker_llm(settings),
            goal_max_rounds=settings.goal.max_rounds,
            # #506: reconcile / cluster-sweeper thresholds (dedup dup proposals + Qs).
            kb_cluster_tau=settings.kb.cluster.cluster_tau,
            kb_cluster_suppress_tau=settings.kb.cluster.suppress_tau,
            kb_cluster_update_tau=settings.kb.cluster.update_tau,
            kb_cluster_merge_tau=settings.kb.cluster.merge_tau,
            kb_cluster_sweep_seconds=settings.kb.cluster.sweep_interval_seconds,
            monitor=SpecstarMonitor(spec),  # persist LLM/agent telemetry (issue #11)
            root_path=settings.server.root_path,
            read_file_max_lines=settings.read_file.max_lines,
            read_file_max_chars=settings.read_file.max_chars,
            tool_output_max_chars=settings.exec.tool_output_max_chars,
            exec_output_max_chars=settings.exec.output_max_chars,
            wiki_maintainer_max_turns=settings.kb.wiki.maintainer_max_turns,
            wiki_reader_max_turns=settings.kb.wiki.reader_max_turns,
            wiki_model=wiki_model or "",
            wiki_llm_base_url=wiki_llm_base_url or "",
            wiki_llm_api_key=wiki_llm_api_key or "",
            message_queue_factory=build_message_queue_factory(settings),
            # #312: all-in-one by default; a pod-split deploy sets
            # server.run_consumers: false on the API so it's a pure producer and
            # dedicated worker pods drain each JobType.
            run_consumers=settings.server.run_consumers,
            # #349: poll cadence for the cross-pod turn-cancel epoch.
            turn_cancel_poll_seconds=settings.server.turn_cancel_poll_seconds,
            # #43: per-session reconnect replay buffer size (0 disables).
            turn_replay_buffer_events=settings.server.turn_replay_buffer_events,
            # Cross-pod live SSE event bus (memory default | rabbitmq fanout).
            event_bus=get_event_bus(settings),
            infer_modules_enhancements=infer_cfg.enhancements,
            infer_modules_reasoning_effort=infer_cfg.reasoning_effort,
            infer_modules_parallelism=infer_cfg.parallelism,
            infer_modules_collection=infer_cfg.collection,
            history_max_messages=settings.history.max_messages,
            history_max_context_tokens=settings.history.max_context_tokens,
            # #624: the operator's declared endpoint ceiling (None ⇒ resolve per turn).
            context_limit=settings.history.context_limit,
            packages=packages,
            # `prebuilt_dir=None` even with packages: the sandbox's `tools_dir`
            # above already bind-mounts PREBUILT_DIR read-only at /.tools/ inside
            # the jail (or symlinks it in unjailed mode). `provision_tools` would
            # then try to `tar xzf ... -C ../.tools/<pkg>` into that read-only
            # mount and fail with `exit 2`. The bind-mount IS the install — no
            # tar+extract pass needed. (Provision_tools stays in the codebase
            # for hypothetical sandboxes without a tools mount, which can pass
            # prebuilt_dir explicitly.)
            prebuilt_dir=None,
            # P3.0: background sweeper for code-Collection re-syncs. Disable by
            # setting kb.git.sync_check_interval_sec: 0.
            code_sync_check_interval=(
                timedelta(seconds=settings.kb.git.sync_check_interval_sec)
                if settings.kb.git.sync_check_interval_sec > 0
                else None
            ),
            # #355: the server-local daily auto-sync time for code collections.
            code_daily_sync=settings.kb.git.daily_sync,
            # #479: the server-local daily wiki-reflection time for prose collections.
            wiki_reflect_daily=settings.kb.wiki.reflect_daily,
            # #429 P7: schedule-trigger sweeper cadence. 0 ⇒ off (headless time-triggered
            # workflows are opt-in per deploy).
            trigger_check_interval=(
                timedelta(seconds=settings.server.trigger_check_interval_sec)
                if settings.server.trigger_check_interval_sec > 0
                else None
            ),
        )
    if packages:
        names = ", ".join(f"{p.name}({','.join(c.name for c in p.commands)})" for p in packages)
        print(f"  provisioned tool packages (tool-demo template): {names}")
    with boot_step("start HTTP server (uvicorn)"):
        uvicorn.run(app, host=settings.server.host, port=settings.server.port)


if __name__ == "__main__":
    main()
