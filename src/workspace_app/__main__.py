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
    get_doc_pipeline,
    get_embedder,
    get_filestore,
    get_infer_modules_run_config,
    get_kb_describer,
    get_kb_llm,
    get_parser_registry,
    get_replay_service,
    get_runner,
    get_sandbox,
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
    # #56: the wiki agents' model/endpoint resolve from kb.wiki.llm (the
    # preset-reference pattern), not the old flat runner.wiki_*. Empty
    # ⇒ create_app's wiki configs keep their in-code default model.
    wiki_model, wiki_llm_base_url, wiki_llm_api_key = get_wiki_endpoint(settings)
    # #66: the infer_modules tool's per-step KB depth / reasoning / fan-out.
    infer_cfg = get_infer_modules_run_config(settings)
    with boot_step("init sandbox"):
        sandbox = get_sandbox(settings, tools_dir=tools_dir)
    with boot_step("build app (create_app)"):
        app = create_app(
            spec=spec,
            get_user_id=get_user_id,
            sandbox=sandbox,
            filestore=get_filestore(settings, spec),
            # #219: single-file upload cap (streaming keeps RAM flat regardless).
            max_file_size=settings.filestore.max_file_size,
            # #245: per-workspace total-size quota (protects the shared disk root).
            workspace_quota=settings.filestore.workspace_quota,
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
            # P1: LlamaIndex IngestionPipeline replaces the hand-rolled chunker.
            # Tests/offline runs still pass `kb_chunker=` directly to create_app.
            kb_pipeline=get_doc_pipeline(settings, embedder),
            # Issue #39: custom parsers (kb.parsers) + VLM-backed bundled
            # parsers (kb.vlm_llm), minus kb.parsers_disabled.
            kb_parser_registry=get_parser_registry(settings),
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
            # P2: chat → knowledge insight extraction (None when no KB llm wired).
            kb_chat_pipeline=get_chat_pipeline(settings, embedder, kb_llm),
            kb_llm=kb_llm,
            card_drafter_llm=card_drafter_llm,
            # #112: the VLM describer the read_image agent tool uses (shared with
            # the VLM-backed ingestion parsers); None when kb.vlm_llm is unset.
            vlm_describer=get_kb_describer(settings),
            kb_retrieval_enhancements=settings.kb.retrieval.enhancements,
            # #195: per-turn kb_search cap for the KB chat turn + ask_knowledge_base
            # bridge (null in config ⇒ unlimited).
            kb_max_searches_per_turn=settings.kb.max_searches_per_turn,
            monitor=SpecstarMonitor(spec),  # persist LLM/agent telemetry (issue #11)
            root_path=settings.server.root_path,
            read_file_max_lines=settings.read_file.max_lines,
            read_file_max_chars=settings.read_file.max_chars,
            exec_output_max_chars=settings.exec.output_max_chars,
            wiki_maintainer_max_turns=settings.kb.wiki.maintainer_max_turns,
            wiki_reader_max_turns=settings.kb.wiki.reader_max_turns,
            wiki_model=wiki_model or "",
            wiki_llm_base_url=wiki_llm_base_url or "",
            wiki_llm_api_key=wiki_llm_api_key or "",
            message_queue_factory=build_message_queue_factory(settings),
            infer_modules_enhancements=infer_cfg.enhancements,
            infer_modules_reasoning_effort=infer_cfg.reasoning_effort,
            infer_modules_parallelism=infer_cfg.parallelism,
            infer_modules_collection=infer_cfg.collection,
            history_max_messages=settings.history.max_messages,
            history_max_context_tokens=settings.history.max_context_tokens,
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
        )
    if packages:
        names = ", ".join(f"{p.name}({','.join(c.name for c in p.commands)})" for p in packages)
        print(f"  provisioned tool packages (tool-demo template): {names}")
    with boot_step("start HTTP server (uvicorn)"):
        uvicorn.run(app, host=settings.server.host, port=settings.server.port)


if __name__ == "__main__":
    main()
