"""Composition-root factories: `load_settings` + `get_*` return the Protocol
implementation chosen by the nested `Settings`. Downstream depends only on
the Protocol seam, not the concrete type.

The legacy flat `Settings.from_env()` + per-field 1:1 env-var override is
gone; env is consumed through `${VAR}` interpolation inside YAML string
values (see `tests/config/test_loader.py` for the loader-end-to-end
behaviour). These tests focus on factory dispatch: given a `Settings`,
do we hand back the right concrete impl?
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.config.schema import (
    EmbedderSettings,
    FilestoreSettings,
    PresetLlmSettings,
    RetrievalLlmRef,
    SandboxSettings,
    Settings,
    ToolsSettings,
)
from workspace_app.factories import (
    _backend_for,
    build_message_queue_factory,
    get_agent_config_catalog,
    get_check_registry,
    get_chunker,
    get_embedder,
    get_filestore,
    get_kb_llm,
    get_runner,
    get_sandbox,
    get_spec,
    load_settings,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder, LitellmEmbedder
from workspace_app.kb.llm import LitellmLlm
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.mock import MockSandbox

# ─── catalog wiring ─────────────────────────────────────────────────────


def test_get_agent_config_catalog_exposes_kb_chat():
    catalog = get_agent_config_catalog(Settings(), config_dir=None)
    kb = catalog.kb_chat()
    assert kb is not None
    assert "kb_search" in kb.allowed_tools  # ty: ignore[unsupported-operator]


# ─── sandbox ────────────────────────────────────────────────────────────


def _with_sandbox(kind: str) -> Settings:
    """Helper: fresh Settings with a specific sandbox.kind."""
    return replace(Settings(), sandbox=replace(SandboxSettings(), kind=kind))


def test_get_sandbox_dispatch(monkeypatch):
    assert isinstance(get_sandbox(_with_sandbox("mock")), MockSandbox)
    assert isinstance(get_sandbox(_with_sandbox("local")), LocalProcessSandbox)

    import docker

    monkeypatch.setattr(docker, "from_env", lambda: object())  # no daemon needed
    from workspace_app.sandbox.docker import DockerSandbox

    assert isinstance(get_sandbox(_with_sandbox("docker")), DockerSandbox)

    with pytest.raises(ValueError):
        get_sandbox(_with_sandbox("bogus"))


def test_sandbox_isolate_threads_through_to_local_process():
    """sandbox.isolate=None lets the LocalProcessSandbox auto-detect
    userns; True/False forces the choice."""
    s = replace(Settings(), sandbox=replace(SandboxSettings(), kind="local", isolate=True))
    sb = get_sandbox(s)
    assert isinstance(sb, LocalProcessSandbox)
    assert sb._isolate is True


def test_get_sandbox_uvrun_mode_forces_non_isolated():
    """#63: tools.mode=uv-run runs tools from live source via `uv run`, which
    needs the host env — so the sandbox must be non-isolated even when userns
    auto-detect (isolate=None) would otherwise enable the jail."""
    s = replace(
        Settings(),
        tools=ToolsSettings(mode="uv-run"),
        sandbox=replace(SandboxSettings(), kind="local", isolate=None),
    )
    sb = get_sandbox(s)
    assert isinstance(sb, LocalProcessSandbox)
    assert sb._isolate is False


def test_get_sandbox_uvrun_mode_rejects_explicit_isolate_true():
    """An operator who sets uv-run AND sandbox.isolate: true has a
    contradiction — uv run can't work inside the chroot jail. Fail loud."""
    s = replace(
        Settings(),
        tools=ToolsSettings(mode="uv-run"),
        sandbox=replace(SandboxSettings(), kind="local", isolate=True),
    )
    with pytest.raises(ValueError, match="uv-run"):
        get_sandbox(s)


# ─── infer_modules run config (#66) ─────────────────────────────────────


def test_get_infer_modules_run_config_defaults_enhancements_off():
    """#66: with no explicit config, the per-step classifier runs with KB
    enhancements OFF (expand/hyde/rerank 0/false) — a focused single-step
    lookup ×~1500 must not multi-query/rerank. reasoning default, parallelism 8."""
    from workspace_app.factories import get_infer_modules_run_config

    cfg = get_infer_modules_run_config(Settings())  # bundled entry, no extras
    assert cfg.enhancements is not None
    assert cfg.enhancements.expand == 0
    assert cfg.enhancements.hyde == 0
    assert cfg.enhancements.rerank is False
    assert cfg.reasoning_effort is None
    assert cfg.parallelism == 16
    assert cfg.collection == ""  # unset → search all collections


def test_get_infer_modules_run_config_resolves_per_step_keys():
    from workspace_app.factories import get_infer_modules_run_config

    s = replace(
        Settings(),
        agents=replace(
            Settings().agents,
            sub_agents={
                "infer_modules": [
                    {
                        "preset": "infer-modules-default",
                        "reasoning_effort": "high",
                        "enhancements": {"expand": 2, "rerank": True},
                        "parallelism": 16,
                        "collection": "fab-process-docs",
                    }
                ]
            },
        ),
    )
    cfg = get_infer_modules_run_config(s)
    assert cfg.reasoning_effort == "high"
    assert cfg.parallelism == 16
    assert cfg.collection == "fab-process-docs"
    assert cfg.enhancements is not None
    assert cfg.enhancements.expand == 2
    assert cfg.enhancements.rerank is True
    assert cfg.enhancements.hyde is None  # unset → inherit operator default


# ─── filestore ──────────────────────────────────────────────────────────


def test_get_filestore_dispatch():
    spec = get_spec(Settings())
    mem = replace(Settings(), filestore=replace(FilestoreSettings(), kind="memory"))
    spec_fs = replace(Settings(), filestore=replace(FilestoreSettings(), kind="specstar"))
    bogus = replace(Settings(), filestore=replace(FilestoreSettings(), kind="bogus"))
    assert isinstance(get_filestore(mem, spec), MemoryFileStore)
    assert isinstance(get_filestore(spec_fs, spec), SpecstarFileStore)
    with pytest.raises(ValueError):
        get_filestore(bogus, spec)


def test_get_spec_with_a_disk_backend_round_trips(tmp_path):
    """#58: multipod rides on a real shared backend (postgres/disk). `get_spec`
    threads the filestore's connection into `make_spec`; a disk-backed spec
    must build and round-trip a resource."""
    from workspace_app.resources import Collection

    s = replace(
        Settings(),
        filestore=replace(FilestoreSettings(), kind="specstar", disk_root=str(tmp_path / "ss")),
    )
    spec = get_spec(s)
    rm = spec.get_resource_manager(Collection)
    rid = rm.create(Collection(name="c")).resource_id
    assert rm.get(rid).data.name == "c"


# ─── postgres connect_timeout (#208: dead DB must fail fast, not hang 10 min) ──


def _backend_pg_dsn(settings) -> str:
    """The pg connection_string `_backend_for` composed (these settings always
    carry a pg_dsn, so a missing pg connection is a test-setup bug, not None)."""
    cfg = _backend_for(settings)
    assert cfg is not None and "pg" in cfg.connections
    dsn = cfg.connections["pg"].options["connection_string"]
    assert isinstance(dsn, str)
    return dsn


def _pg_settings(dsn: str, *, timeout: int = 10):
    return replace(
        Settings(),
        filestore=replace(
            FilestoreSettings(), kind="specstar", pg_dsn=dsn, pg_connect_timeout=timeout
        ),
    )


def test_backend_injects_connect_timeout_into_url_dsn():
    dsn = _backend_pg_dsn(_pg_settings("postgresql://u:p@host:5432/db"))
    assert "connect_timeout=10" in dsn


def test_backend_keeps_explicit_connect_timeout_in_dsn():
    dsn = _backend_pg_dsn(_pg_settings("postgresql://u:p@h/db?connect_timeout=3"))
    assert "connect_timeout=3" in dsn
    assert "connect_timeout=10" not in dsn  # an explicit value wins


def test_backend_preserves_other_dsn_params():
    dsn = _backend_pg_dsn(_pg_settings("postgresql://u:p@h/db?sslmode=require"))
    assert "sslmode=require" in dsn
    assert "connect_timeout=10" in dsn


def test_backend_timeout_zero_leaves_dsn_untouched():
    dsn = _backend_pg_dsn(_pg_settings("postgresql://u:p@h/db", timeout=0))
    assert "connect_timeout" not in dsn


def test_backend_keeps_explicit_connect_timeout_in_keyvalue_dsn():
    dsn = _backend_pg_dsn(_pg_settings("host=db port=5432 connect_timeout=4"))
    assert "connect_timeout=4" in dsn
    assert "connect_timeout=10" not in dsn


def test_backend_injects_into_libpq_keyvalue_dsn():
    dsn = _backend_pg_dsn(_pg_settings("host=db port=5432 dbname=app"))
    assert "host=db" in dsn and "dbname=app" in dsn
    assert "connect_timeout=10" in dsn


def test_with_connect_timeout_noop_on_empty_dsn():
    from workspace_app.factories import _with_connect_timeout

    assert _with_connect_timeout("", 10) == ""


# ─── embedder ───────────────────────────────────────────────────────────


def test_get_embedder_dispatch_uses_embed_dim():
    real = get_embedder(_with_embedder_model("ollama/bge-m3"))
    assert isinstance(real, LitellmEmbedder) and real.dim == EMBED_DIM
    offline = get_embedder(_with_embedder_model(""))  # empty → offline hash
    assert isinstance(offline, HashEmbedder) and offline.dim == EMBED_DIM


def test_get_embedder_threads_its_own_endpoint():
    """kb.embedder.base_url / api_key are separate from chat llm — they
    can stay on local Ollama while chat goes hosted."""
    s = _with_embedder(model="ollama/bge-m3", base_url="http://o/v1", api_key="ek")
    e = get_embedder(s)
    assert isinstance(e, LitellmEmbedder)
    assert e._base_url == "http://o/v1"
    assert e._api_key == "ek"

    bare = get_embedder(_with_embedder_model("ollama/bge-m3"))
    assert isinstance(bare, LitellmEmbedder)
    assert bare._base_url is None and bare._api_key is None


def _with_embedder_model(model: str) -> Settings:
    return _with_embedder(model=model)


def _with_embedder(*, model: str, base_url: str = "", api_key: str = "") -> Settings:
    return replace(
        Settings(),
        kb=replace(
            Settings().kb,
            embedder=replace(EmbedderSettings(), model=model, base_url=base_url, api_key=api_key),
        ),
    )


# ─── chunker + kb_llm ───────────────────────────────────────────────────


def test_get_chunker_uses_kb_chunker_settings():
    assert isinstance(get_chunker(Settings()), FixedTokenChunker)


def test_get_kb_llm_enabled_via_bundled_ref_disabled_via_none():
    """Default `Settings()` ships `kb.retrieval_llm = RetrievalLlmRef(
    preset='kb-retrieval')`, which resolves to the bundled preset
    (Qwen3 14B) → LitellmLlm. Setting `retrieval_llm=None` disables
    enhancements (multi-query / HyDE / rerank) — factory returns None."""
    enabled = Settings()
    assert isinstance(get_kb_llm(enabled), LitellmLlm)

    disabled = replace(enabled, kb=replace(enabled.kb, retrieval_llm=None))
    assert get_kb_llm(disabled) is None


def test_get_kb_llm_threads_the_configured_reasoning_effort():
    """kb_search's retrieval LLM (multi-query / HyDE / rerank) honours
    `kb.retrieval_llm.reasoning_effort` — e.g. "none" so qwen3 doesn't <think>
    on every expansion (litellm maps it to Ollama think=False). Unset ⇒ None
    (the param is omitted → model default)."""
    from workspace_app.config.schema import RetrievalLlmRef

    s = replace(
        Settings(),
        kb=replace(
            Settings().kb,
            retrieval_llm=RetrievalLlmRef(preset="kb-retrieval", reasoning_effort="none"),
        ),
    )
    llm = get_kb_llm(s)
    assert isinstance(llm, LitellmLlm)
    assert llm.reasoning_effort == "none"

    # default ref carries no effort ⇒ None (omit the param)
    assert get_kb_llm(Settings()).reasoning_effort is None  # ty: ignore[unresolved-attribute]


def test_get_kb_vlm_formatter_falls_back_to_retrieval_then_off():
    """Issue #115 stage-2 formatter: use `kb.vlm_format_llm` if set, else reuse
    `kb.retrieval_llm`, else None (stage 2 skipped → raw VLM text). Default
    Settings() has no dedicated formatter but a bundled retrieval preset, so the
    fallback yields a LitellmLlm."""
    from workspace_app.factories import get_kb_vlm_formatter

    assert isinstance(get_kb_vlm_formatter(Settings()), LitellmLlm)

    both_off = replace(
        Settings(), kb=replace(Settings().kb, retrieval_llm=None, vlm_format_llm=None)
    )
    assert get_kb_vlm_formatter(both_off) is None


def test_get_kb_vlm_formatter_prefers_dedicated_config():
    """A dedicated `kb.vlm_format_llm` wins over the retrieval-LLM fallback."""
    from workspace_app.config.schema import RetrievalLlmRef
    from workspace_app.factories import get_kb_vlm_formatter

    s = replace(
        Settings(),
        kb=replace(
            Settings().kb,
            vlm_format_llm=RetrievalLlmRef(preset="kb-retrieval", model="dedicated-formatter"),
        ),
    )
    llm = get_kb_vlm_formatter(s)
    assert isinstance(llm, LitellmLlm)
    assert llm._model == "dedicated-formatter"


def test_get_kb_llm_threads_chat_endpoint_when_ref_creds_unset():
    """When the ref's own `llm.base_url` / `api_key` are empty (the
    default), fall back to the top-level `llm.*` endpoint —
    single-endpoint deploys avoid duplicating creds."""
    s = replace(
        Settings(),
        llm=replace(Settings().llm, base_url="http://x/v1", api_key="k"),
    )
    llm = get_kb_llm(s)
    assert llm is not None
    assert llm._base_url == "http://x/v1"  # ty: ignore[unresolved-attribute]
    assert llm._api_key == "k"  # ty: ignore[unresolved-attribute]

    # Bare Settings: no top-level llm creds, ref creds empty too — the
    # LitellmLlm still constructs (LiteLLM falls back to provider env
    # / Ollama defaults), endpoint fields land as None.
    bare = get_kb_llm(Settings())
    assert bare is not None
    assert bare._base_url is None and bare._api_key is None  # ty: ignore[unresolved-attribute]


def test_get_kb_llm_ref_overrides_win_over_referenced_preset():
    """Usage-entry semantics: `RetrievalLlmRef(preset=..., model=...,
    llm=...)` lets an operator override just `model` / `llm` on top of
    the named preset without redefining the whole preset."""
    base = Settings()
    s = replace(
        base,
        kb=replace(
            base.kb,
            retrieval_llm=RetrievalLlmRef(
                preset="kb-retrieval",
                model="openai/gpt-4o-mini",
                llm=PresetLlmSettings(
                    base_url="http://retrieve/v1",
                    api_key="sk-retrieve",
                ),
            ),
        ),
    )
    llm = get_kb_llm(s)
    assert llm is not None
    assert llm._model == "openai/gpt-4o-mini"  # ty: ignore[unresolved-attribute]
    assert llm._base_url == "http://retrieve/v1"  # ty: ignore[unresolved-attribute]
    assert llm._api_key == "sk-retrieve"  # ty: ignore[unresolved-attribute]


def test_get_kb_llm_ref_creds_win_over_top_level_llm():
    """When BOTH the per-ref `llm.*` AND top-level `llm.*` are set,
    the per-ref creds win — the chat-LLM key never leaks into the
    retrieval-LLM call."""
    base = Settings()
    s = replace(
        base,
        llm=replace(base.llm, base_url="http://chat/v1", api_key="chat-key"),
        kb=replace(
            base.kb,
            retrieval_llm=RetrievalLlmRef(
                preset="kb-retrieval",
                llm=PresetLlmSettings(
                    base_url="http://retrieve/v1",
                    api_key="retrieve-key",
                ),
            ),
        ),
    )
    llm = get_kb_llm(s)
    assert llm is not None
    assert llm._base_url == "http://retrieve/v1"  # ty: ignore[unresolved-attribute]
    assert llm._api_key == "retrieve-key"  # ty: ignore[unresolved-attribute]


def test_get_kb_llm_inherits_endpoint_from_referenced_preset():
    """Operator can set `agents.presets.kb-retrieval.llm.{api_key,
    base_url}` once and have retrieval automatically pick those up
    via the ref — no need to duplicate them on the ref itself."""
    base = Settings()
    presets = dict(base.agents.presets)
    kb_retrieval = presets["kb-retrieval"]
    presets["kb-retrieval"] = replace(
        kb_retrieval,
        llm=PresetLlmSettings(base_url="http://from-preset/v1", api_key="preset-key"),
    )
    s = replace(base, agents=replace(base.agents, presets=presets))
    llm = get_kb_llm(s)
    assert llm is not None
    assert llm._base_url == "http://from-preset/v1"  # ty: ignore[unresolved-attribute]
    assert llm._api_key == "preset-key"  # ty: ignore[unresolved-attribute]


# ─── runner ─────────────────────────────────────────────────────────────


def test_get_runner_is_litellm():
    assert isinstance(get_runner(Settings()), LitellmAgentRunner)


def test_get_runner_threads_chat_endpoint_empty_is_none():
    with_endpoint = replace(
        Settings(),
        llm=replace(Settings().llm, base_url="https://hosted/v1", api_key="sk-1"),
    )
    r = get_runner(with_endpoint)
    assert r._base_url == "https://hosted/v1" and r._api_key == "sk-1"  # ty: ignore
    bare = get_runner(Settings())  # unset → None, not "" (LiteLLM defaults apply)
    assert bare._base_url is None and bare._api_key is None  # ty: ignore[unresolved-attribute]


# ─── load_settings (config.yaml entry point) ────────────────────────────


def test_load_settings_no_args_returns_bundled_defaults():
    """No config.yaml, no env reference → Settings() equivalent."""
    s = load_settings(config_path=None, env={})
    assert s.server.port == 8000
    assert s.kb.embedder.model == "ollama/bge-m3"


def test_load_settings_reads_a_yaml_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 1234\n")
    s = load_settings(config_path=cfg, env={})
    assert s.server.port == 1234


def test_load_settings_unknown_yaml_key_raises(tmp_path):
    """Typo defence: a misspelled key fails the deploy loud rather
    than silently doing nothing."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  por: 8001\n")
    with pytest.raises(ValueError, match="por"):
        load_settings(config_path=cfg, env={})


# ─── CLI: --config / -c ────────────────────────────────────────────────


def test_cli_argv_config_arg_parsed():
    """`python -m workspace_app --config /path/x.yaml` (long form) and
    `-c /path/x.yaml` (short) both parse to the same Namespace.config."""
    from pathlib import Path

    from workspace_app.__main__ import _parse_args

    a = _parse_args(["--config", "/tmp/x.yaml"])
    assert a.config == Path("/tmp/x.yaml")
    b = _parse_args(["-c", "/tmp/x.yaml"])
    assert b.config == Path("/tmp/x.yaml")


def test_cli_argv_config_defaults_to_none():
    """No --config given → None, so `load_settings` falls back to
    $WORKSPACE_APP_CONFIG / ./config.yaml lookups."""
    from workspace_app.__main__ import _parse_args

    assert _parse_args([]).config is None


def test_get_kb_vlm_enabled_via_bundled_ref_disabled_via_none():
    """Issue #39 P9: default `Settings()` ships `kb.vlm_llm =
    RetrievalLlmRef(preset='kb-vlm')` → the bundled qwen2.5-vl preset
    → LitellmVlm. `vlm_llm=None` disables the VLM parsers — factory
    returns None and image-only uploads stay chunk-less until an
    operator wires a VLM and reindexes."""
    from workspace_app.factories import get_kb_vlm
    from workspace_app.kb.vlm import LitellmVlm

    enabled = Settings()
    vlm = get_kb_vlm(enabled)
    assert isinstance(vlm, LitellmVlm)
    assert vlm._model == "ollama_chat/qwen2.5vl:7b"

    disabled = replace(enabled, kb=replace(enabled.kb, vlm_llm=None))
    assert get_kb_vlm(disabled) is None


def test_get_kb_describer_shares_the_vlm_and_disables_with_it():
    """#112: the read_image tool and the ingestion parsers share one describer.
    Default Settings (kb.vlm_llm wired) → a VlmDescriber; vlm_llm=None → None,
    so read_image reports it's unavailable rather than failing."""
    from workspace_app.factories import get_kb_describer
    from workspace_app.kb.vlm import VlmDescriber

    assert isinstance(get_kb_describer(Settings()), VlmDescriber)

    disabled = replace(Settings(), kb=replace(Settings().kb, vlm_llm=None))
    assert get_kb_describer(disabled) is None


def test_get_kb_vlm_resolves_endpoint_like_get_kb_llm():
    """Same resolution cascade as get_kb_llm: ref.* over preset.* over
    top-level llm.*."""
    from workspace_app.factories import get_kb_vlm

    s = replace(
        Settings(),
        llm=replace(Settings().llm, base_url="http://x/v1", api_key="k"),
    )
    vlm = get_kb_vlm(s)
    assert vlm is not None
    assert vlm._base_url == "http://x/v1"
    assert vlm._api_key == "k"


def test_get_check_registry_bundles_the_wiki_agent_probes():
    """#50 P8: the wiki maintainer/reader capability probes ship in the
    bundled registry alongside the other agent tool-call checks."""
    reg = get_check_registry(Settings())
    ids = {c.check_id for c in reg.checks()}
    assert {"agent-wiki-reader", "agent-wiki-maintainer"} <= ids


def test_get_wiki_endpoint_resolves_like_get_kb_llm():
    """#56: the wiki endpoint resolves from `kb.wiki.llm` through the
    same preset cascade as retrieval/vlm (ref.* over preset.* over
    top-level llm.*)."""
    from workspace_app.factories import get_wiki_endpoint

    base = Settings()
    presets = dict(base.agents.presets)
    presets["wiki-default"] = replace(
        presets["wiki-default"],
        model="openai/gpt-5.5",
        llm=PresetLlmSettings(base_url="http://w/v1", api_key="wk"),
    )
    s = replace(base, agents=replace(base.agents, presets=presets))
    assert get_wiki_endpoint(s) == ("openai/gpt-5.5", "http://w/v1", "wk")


def test_get_wiki_endpoint_is_none_when_wiki_disabled():
    from workspace_app.factories import get_wiki_endpoint

    base = Settings()
    s = replace(base, kb=replace(base.kb, wiki=replace(base.kb.wiki, llm=None)))
    assert get_wiki_endpoint(s) == (None, None, None)


def test_wiki_probes_test_the_configured_wiki_model_not_the_workspace_model():
    """#57: the wiki health probe must capability-test the model that
    ACTUALLY drives the wiki (`kb.wiki.llm`) — the old `runner.wiki_*`
    lived in a namespace the resolver never read, so the probe silently
    tested the workspace model instead. Point `kb.wiki.llm` at a distinct
    model and assert both wiki probes target it."""
    base = Settings()
    presets = dict(base.agents.presets)
    presets["wiki-default"] = replace(presets["wiki-default"], model="openai/gpt-5.5")
    s = replace(base, agents=replace(base.agents, presets=presets))
    checks = {c.check_id: c for c in get_check_registry(s).checks()}
    assert checks["agent-wiki-maintainer"]._model == "openai/gpt-5.5"
    assert checks["agent-wiki-reader"]._model == "openai/gpt-5.5"
    # ... and the KB-chat probe is unaffected (still the bundled local model).
    assert checks["agent-kb-chat"]._model == "ollama_chat/qwen3:14b"


def test_wiki_probes_skip_when_wiki_disabled():
    """`kb.wiki.llm: null` → no wiki model → the probes report skip
    (model None), never a false fail."""
    base = Settings()
    s = replace(base, kb=replace(base.kb, wiki=replace(base.kb.wiki, llm=None)))
    checks = {c.check_id: c for c in get_check_registry(s).checks()}
    assert checks["agent-wiki-maintainer"]._model is None
    assert checks["agent-wiki-reader"]._model is None


# ─── message queue backend (#58/#59/#82) ────────────────────────────────


def test_message_queue_factory_defaults_to_simple():
    from specstar.message_queue import SimpleMessageQueueFactory

    f = build_message_queue_factory(Settings())
    assert isinstance(f, SimpleMessageQueueFactory)


def test_message_queue_factory_rabbitmq_selected_by_kind():
    """`message_queue.kind: rabbitmq` selects the broker-backed factory
    (constructed with the configured url; no connection opened here)."""
    from specstar.message_queue import RabbitMQMessageQueueFactory

    from workspace_app.config.schema import MessageQueueSettings, RabbitmqSettings

    s = replace(
        Settings(),
        message_queue=MessageQueueSettings(
            kind="rabbitmq", rabbitmq=RabbitmqSettings(url="amqp://broker:5672")
        ),
    )
    assert isinstance(build_message_queue_factory(s), RabbitMQMessageQueueFactory)


def test_message_queue_factory_threads_all_rabbitmq_knobs():
    """Every configured production knob reaches the broker factory — not just
    the url. The `heartbeat_seconds` config maps to specstar's
    `amqp_heartbeat_seconds` (long slow-index jobs must not get reaped)."""
    from workspace_app.config.schema import MessageQueueSettings, RabbitmqSettings

    s = replace(
        Settings(),
        message_queue=MessageQueueSettings(
            kind="rabbitmq",
            rabbitmq=RabbitmqSettings(
                url="amqp://broker:5672",
                queue_prefix="rca:",
                max_retries=7,
                retry_delay_seconds=99,
                heartbeat_seconds=42,
            ),
        ),
    )
    f = build_message_queue_factory(s)
    assert f.amqp_url == "amqp://broker:5672"
    assert f.queue_prefix == "rca:"
    assert f.max_retries == 7
    assert f.retry_delay_seconds == 99
    assert f.amqp_heartbeat_seconds == 42


def test_message_queue_factory_unknown_kind_raises():
    from workspace_app.config.schema import MessageQueueSettings

    s = replace(Settings(), message_queue=MessageQueueSettings(kind="kafka"))
    with pytest.raises(ValueError, match="kafka"):
        build_message_queue_factory(s)
