"""Top-level `load(config_path, env)` orchestrator.

The pipeline (Q1 hard cut + Q2 interpolation-only + Q7 layered defaults):

  1. discover config.yaml (explicit / WORKSPACE_APP_CONFIG / ./config.yaml)
  2. parse YAML (missing file → empty dict; no .yaml at all is fine)
  3. walk every string value, apply ${FOO} interpolation against `env`
  4. layered merge: bundled defaults ◇ operator's YAML
  5. strict validate:
       - unknown top-level / nested keys → raise
       - workspace_chat[].preset / kb_chat.preset references → must exist
       - missing required fields on a preset (model / prompt_file) → raise
  6. construct typed `Settings(...)` tree

Result is a frozen `Settings`.

These tests focus on END-TO-END behaviour: what does the operator see
when they write a small `config.yaml`?
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from workspace_app.config.interpolate import EnvVarUnset
from workspace_app.config.loader import load
from workspace_app.config.schema import Settings


def test_no_config_path_returns_bundled_defaults_settings():
    """No config.yaml + no env vars → same as `Settings()` no-arg."""
    s = load(config_path=None, env={})
    assert s.server.port == 8000
    assert s.kb.embedder.model == "ollama/bge-m3"
    assert "qwen3-local" in s.agents.presets


def test_operator_yaml_overrides_a_single_scalar_keeps_other_defaults(tmp_path: Path):
    """The whole point of Q7 — operator writes one knob, everything
    else stays bundled."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            server:
              port: 9090
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.server.port == 9090
    # Other server defaults survive.
    assert s.server.host == "127.0.0.1"
    # Other top-level sections untouched.
    assert s.kb.embedder.model == "ollama/bge-m3"


def test_http_sandbox_and_host_sections_load(tmp_path: Path):
    """#60: the client `sandbox.http` block + the standalone `sandbox_host`
    section parse into typed settings (nested http built, not left a dict)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            sandbox:
              kind: http
              http:
                base_url: http://sandbox-host:8000
                read_timeout: 0
            sandbox_host:
              uid_min: 200000
              uid_max: 209999
              memory_max: 1G
              cpu_cores: 2.0
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.sandbox.kind == "http"
    assert s.sandbox.http is not None
    assert s.sandbox.http.base_url == "http://sandbox-host:8000"
    assert s.sandbox_host.uid_min == 200000
    assert s.sandbox_host.memory_max == "1G"
    assert s.sandbox_host.cpu_cores == 2.0
    # Untouched host knobs keep their defaults.
    assert s.sandbox_host.pids_max == 512


def test_kb_cluster_thresholds_load(tmp_path: Path):
    """#506: the reconcile / cluster-sweeper τ + sweep interval parse into typed
    ClusterSettings; untouched knobs keep their defaults."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              cluster:
                cluster_tau: 0.8
                merge_tau: 0.99
                sweep_interval_seconds: 300
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.cluster.cluster_tau == 0.8
    assert s.kb.cluster.merge_tau == 0.99
    assert s.kb.cluster.sweep_interval_seconds == 300
    assert s.kb.cluster.suppress_tau == 0.92  # untouched default


def test_kb_cluster_defaults_when_absent():
    """A config with no kb.cluster section still gets conservative defaults."""
    s = load(config_path=None, env={})
    assert s.kb.cluster.cluster_tau == 0.9
    assert s.kb.cluster.suppress_tau == 0.92
    assert s.kb.cluster.merge_tau == 0.95
    assert s.kb.cluster.sweep_interval_seconds == 900.0


def test_default_config_leaves_http_sandbox_unset():
    """A default deploy keeps the local backend; `sandbox.http` is None and the
    host section carries its bundled defaults."""
    s = load(config_path=None, env={})
    assert s.sandbox.http is None
    assert s.sandbox_host.bind == "0.0.0.0:8000"


def test_kb_max_searches_per_turn_defaults_to_three():
    """#195: a fresh deploy caps kb_search at 3 calls per reply."""
    s = load(config_path=None, env={})
    assert s.kb.max_searches_per_turn == 3


def test_kb_max_searches_per_turn_operator_override(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("kb:\n  max_searches_per_turn: 5\n", encoding="utf-8")
    s = load(config_path=cfg, env={})
    assert s.kb.max_searches_per_turn == 5


def test_kb_max_searches_per_turn_null_lifts_the_cap(tmp_path: Path):
    """#195: `null` is the documented off switch — no cap at all."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("kb:\n  max_searches_per_turn: null\n", encoding="utf-8")
    s = load(config_path=cfg, env={})
    assert s.kb.max_searches_per_turn is None


def test_kb_max_searches_per_turn_zero_raises(tmp_path: Path):
    """#195: 0/negative would silently mute the KB — reject it; use null to lift."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("kb:\n  max_searches_per_turn: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kb.max_searches_per_turn"):
        load(config_path=cfg, env={})


def test_kb_max_searches_ceiling_defaults_to_ten():
    """#334: the per-message FE picker can request up to this many searches."""
    s = load(config_path=None, env={})
    assert s.kb.max_searches_ceiling == 10


def test_kb_max_searches_ceiling_operator_override(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("kb:\n  max_searches_ceiling: 6\n", encoding="utf-8")
    s = load(config_path=cfg, env={})
    assert s.kb.max_searches_ceiling == 6


def test_kb_max_searches_ceiling_zero_raises(tmp_path: Path):
    """#334: the ceiling bounds a per-message pick — it must be a positive int."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("kb:\n  max_searches_ceiling: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kb.max_searches_ceiling"):
        load(config_path=cfg, env={})


def test_tools_mode_defaults_to_prebuilt_and_parses_uv_run(tmp_path: Path):
    """#63: `tools.mode` selects how packages are provisioned. Default is
    `prebuilt`; an operator opts into the uv-run debug mode by writing it."""
    assert load(config_path=None, env={}).tools.mode == "prebuilt"

    cfg = tmp_path / "config.yaml"
    cfg.write_text("tools:\n  mode: uv-run\n", encoding="utf-8")
    assert load(config_path=cfg, env={}).tools.mode == "uv-run"


def test_infer_modules_entry_accepts_per_step_config(tmp_path: Path):
    """#66: an infer_modules usage entry carries optional reasoning_effort /
    enhancements / parallelism for the per-step classifier."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              infer_modules:
                - preset: infer-modules-default
                  reasoning_effort: high
                  parallelism: 12
                  enhancements: { expand: 2, hyde: 0, rerank: true }
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    entry = s.agents.sub_agents["infer_modules"][0]
    assert entry["reasoning_effort"] == "high"
    assert entry["parallelism"] == 12
    assert entry["enhancements"]["expand"] == 2


def test_infer_modules_bad_enhancement_key_raises(tmp_path: Path):
    """A typo'd enhancement knob is caught with its path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              infer_modules:
                - preset: infer-modules-default
                  enhancements: { expnd: 2 }
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expnd"):
        load(config_path=cfg, env={})


def test_unknown_tools_key_raises(tmp_path: Path):
    """A typo'd key under `tools` is caught with its path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("tools:\n  mdoe: uv-run\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mdoe"):
        load(config_path=cfg, env={})


def test_env_interpolation_substitutes_into_string_values(tmp_path: Path):
    """`${FOO}` inside a YAML string value is replaced from env at load."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "filestore:\n  pg_dsn: postgresql://${DB_USER}@db/main\n",
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={"DB_USER": "alice"})
    assert s.filestore.pg_dsn == "postgresql://alice@db/main"


def test_unset_env_var_in_interpolation_raises_with_var_name(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("llm:\n  api_key: ${MISSING_KEY}\n", encoding="utf-8")
    with pytest.raises(EnvVarUnset, match="MISSING_KEY"):
        load(config_path=cfg, env={})


def test_unknown_top_level_section_raises_with_offending_name(tmp_path: Path):
    """Typo defence: `serer: { ... }` (typo'd `server`) raises clearly."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("serer:\n  port: 9090\n", encoding="utf-8")
    with pytest.raises(ValueError, match="serer"):
        load(config_path=cfg, env={})


def test_unknown_nested_key_raises_with_path(tmp_path: Path):
    """`kb.embedder.timoeut` (typo) raises with the full dotted path
    so the operator finds it instantly."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("kb:\n  embedder:\n    timoeut: 30\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kb.embedder.timoeut"):
        load(config_path=cfg, env={})


def test_kb_chat_referencing_unknown_preset_raises(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              kb_chat: { preset: "nope" }
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nope"):
        load(config_path=cfg, env={})


def test_old_flat_form_retrieval_llm_raises_unknown_key(tmp_path: Path):
    """Pre-refactor shape `kb.retrieval_llm: {model, base_url, api_key}`
    is no longer accepted — `base_url` / `api_key` belong under `llm.*`
    now, just like every other preset usage entry. The strict loader
    raises on `base_url` (or `api_key`)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                model: "openai/gpt-4o-mini"
                base_url: "https://api.openai.com/v1"
                api_key: "sk-xxx"
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="base_url"):
        load(config_path=cfg, env={})


def test_bundled_defaults_carry_kb_wiki_and_message_queue():
    """#56/#59: a no-config deploy gets `kb.wiki` (preset-referenced
    wiki LLM + step budgets) and a `message_queue` backend selection."""
    s = load(config_path=None, env={})
    assert s.kb.wiki.llm is not None
    assert s.kb.wiki.llm.preset == "wiki-default"
    assert s.kb.wiki.maintainer_max_turns == 40
    assert s.message_queue.kind == "simple"


def test_kb_wiki_inline_override_merges_with_named_preset(tmp_path: Path):
    """#56: `kb.wiki.llm` is the same usage-entry shape as
    `retrieval_llm` — point it at another preset and/or layer inline
    creds. Reusing an existing hosted preset is the intended migration
    from the old flat `runner.wiki_*`."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              presets:
                my-hosted:
                  model: openai/gpt-5.5
                  llm:
                    api_key: sk-xyz
            kb:
              wiki:
                llm: { preset: my-hosted }
                maintainer_max_turns: 60
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.wiki.llm is not None
    assert s.kb.wiki.llm.preset == "my-hosted"
    assert s.kb.wiki.maintainer_max_turns == 60
    # reader budget keeps its bundled default (layered merge)
    assert s.kb.wiki.reader_max_turns == 24


def test_kb_wiki_null_llm_disables_the_wiki(tmp_path: Path):
    """`kb.wiki.llm: null` is the off switch (mirrors `vlm_llm: null`)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              wiki:
                llm: null
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.wiki.llm is None


def test_kb_wiki_referencing_unknown_preset_raises(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              wiki:
                llm: { preset: nope-wiki }
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nope-wiki"):
        load(config_path=cfg, env={})


def test_old_flat_runner_wiki_model_raises_unknown_key(tmp_path: Path):
    """#56 migration guard: the pre-refactor `runner.wiki_model` is gone;
    a stale config using it fails loud with the offending path (so the
    operator knows to move it to `kb.wiki.llm`)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            runner:
              wiki_model: openai/gpt-5.5
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="wiki_model"):
        load(config_path=cfg, env={})


def test_message_queue_kind_override(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            message_queue:
              kind: rabbitmq
              rabbitmq:
                url: amqp://localhost
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.message_queue.kind == "rabbitmq"
    assert s.message_queue.rabbitmq.url == "amqp://localhost"


def test_message_queue_rabbitmq_production_knobs_override(tmp_path: Path):
    """The broker tuning knobs load end-to-end; unset ones keep the
    specstar-matching defaults (here only the heartbeat is raised)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            message_queue:
              kind: rabbitmq
              rabbitmq:
                url: amqp://broker:5672
                queue_prefix: "rca:"
                max_retries: 7
                heartbeat_seconds: 1800
        """),
        encoding="utf-8",
    )
    rmq = load(config_path=cfg, env={}).message_queue.rabbitmq
    assert rmq.queue_prefix == "rca:"
    assert rmq.max_retries == 7
    assert rmq.heartbeat_seconds == 1800
    assert rmq.retry_delay_seconds == 10  # unset → specstar default


def test_message_queue_unknown_subkey_raises(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            message_queue:
              kind: simple
              rabbitmq:
                host: localhost
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="host"):
        load(config_path=cfg, env={})


def test_retrieval_llm_null_disables_enhancements(tmp_path: Path):
    """`kb.retrieval_llm: null` is the explicit "off switch" — the
    loader accepts it and produces `settings.kb.retrieval_llm is None`
    so `get_kb_llm` returns None and multi-query / HyDE / rerank stay
    silent."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm: null
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.retrieval_llm is None


def test_retrieval_llm_inline_override_merges_with_named_preset(tmp_path: Path):
    """Usage-entry shape: `{preset: kb-retrieval, llm: {api_key: ...}}`
    merges the inline override on top of the named preset. Loader
    accepts; downstream factory consumes the merged endpoint creds."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                preset: "kb-retrieval"
                llm:
                  api_key: "sk-override"
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.retrieval_llm is not None
    assert s.kb.retrieval_llm.preset == "kb-retrieval"
    assert s.kb.retrieval_llm.llm.api_key == "sk-override"


def test_retrieval_llm_reasoning_effort_loads(tmp_path: Path):
    """kb.retrieval_llm.reasoning_effort loads end-to-end so kb_search's
    retrieval LLM (multi-query / HyDE / rerank) can skip qwen3 thinking
    (`none`); unset defaults to "" (model default)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                preset: "kb-retrieval"
                reasoning_effort: none
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.retrieval_llm is not None
    assert s.kb.retrieval_llm.reasoning_effort == "none"
    # unset (bundled default) ⇒ "" so the param is omitted
    default = load(config_path=None, env={})
    assert default.kb.retrieval_llm is not None
    assert default.kb.retrieval_llm.reasoning_effort == ""


def test_retrieval_llm_invalid_reasoning_effort_raises(tmp_path: Path):
    """A typo (e.g. `hihg`) must fail fast at load, not silently map to
    think=False — valid values are none|low|medium|high."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                preset: "kb-retrieval"
                reasoning_effort: hihg
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reasoning_effort"):
        load(config_path=cfg, env={})


def test_retrieval_llm_referencing_unknown_preset_raises(tmp_path: Path):
    """Same invariant as workspace_chat / kb_chat: a non-existent
    preset name on retrieval_llm raises with the offending name in
    the message."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                preset: "made-up-retrieval"
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="made-up-retrieval"):
        load(config_path=cfg, env={})


def test_retrieval_llm_unknown_llm_subfield_raises(tmp_path: Path):
    """`kb.retrieval_llm.llm` only accepts `base_url` / `api_key`
    (same set as every other preset.llm). Typos like `llm.token`
    raise with the offending path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                preset: "kb-retrieval"
                llm:
                  token: "sk-xxx"
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="token"):
        load(config_path=cfg, env={})


def test_retrieval_llm_explicit_null_llm_is_treated_as_omitted(tmp_path: Path):
    """`llm: null` inside the ref is equivalent to omitting it —
    "use the named preset's endpoint and/or fall back to top-level
    llm". Validation doesn't complain (the validator only checks
    subfields when llm IS a dict), and the loader builds a
    PresetLlmSettings with empty defaults."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                preset: "kb-retrieval"
                llm: null
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.retrieval_llm is not None
    assert s.kb.retrieval_llm.llm.base_url == ""
    assert s.kb.retrieval_llm.llm.api_key == ""


def test_operator_can_raise_enhancement_defaults_and_max(tmp_path: Path):
    """Operators tune `kb.retrieval.enhancements.*` to trade latency
    for recall. Bundled defaults are light; this exercises overriding
    individual knobs without touching the others."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval:
                enhancements:
                  expand:
                    default: 3
                    max: 5
                  hyde:
                    default: 1
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    e = s.kb.retrieval.enhancements
    assert e.expand.default == 3
    assert e.expand.max == 5
    assert e.hyde.default == 1
    # hyde.max stays at bundled default (1), rerank untouched
    assert e.hyde.max == 1
    assert e.rerank.default is True
    assert e.rerank.max is True


def test_operator_can_set_retrieval_scalar_knobs(tmp_path: Path):
    """`kb.retrieval`'s scalar leaves must actually reach `RetrievalSettings`.

    The loader's key allowlist accepted these long before the builder read them, so
    setting `quality_weight` / `quality_floor` in config raised no error and did
    nothing — the value was dropped and the dataclass default silently won. Anything
    the operator can write here must land, or the knob is a lie."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval:
                quality_weight: 0.42
                quality_floor: 33
                sparse_corpus_cap: 750
        """),
        encoding="utf-8",
    )
    r = load(config_path=cfg, env={}).kb.retrieval
    assert r.quality_weight == 0.42
    assert r.quality_floor == 33
    assert r.sparse_corpus_cap == 750
    # untouched knobs keep their bundled defaults
    assert r.enhancements.expand.default == 1


def test_retrieval_scalar_knobs_keep_defaults_when_unset(tmp_path: Path):
    """Omitting them leaves the dataclass defaults — the cap in particular defaults
    to null (uncapped), so an operator who never sets it gets today's behaviour."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval:
                enhancements:
                  hyde:
                    default: 1
        """),
        encoding="utf-8",
    )
    r = load(config_path=cfg, env={}).kb.retrieval
    assert r.quality_weight == 0.10
    assert r.quality_floor is None
    assert r.sparse_corpus_cap is None


def test_unknown_enhancement_key_raises_with_path(tmp_path: Path):
    """Typos in the enhancements tree must raise — `default` is the
    correct key, `defualt` is a misspelling and should fail loud."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval:
                enhancements:
                  expand:
                    defualt: 3
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="defualt"):
        load(config_path=cfg, env={})


def test_operator_can_add_arbitrary_sub_agent_purpose(tmp_path: Path):
    """B-flat schema: operators add a new sub-agent purpose by writing
    `agents.<new_purpose>: [...]`. No schema field needs to change;
    the loader walks all non-`presets` keys under `agents` and packs
    them into `sub_agents`. The catalog / downstream tools resolve by
    name at use site."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              qtime_pair_selector:
                - { preset: "kb-default" }
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert "qtime_pair_selector" in s.agents.sub_agents
    assert s.agents.sub_agents["qtime_pair_selector"][0]["preset"] == "kb-default"
    # Other bundled purposes survive the merge.
    assert "kb_chat" in s.agents.sub_agents
    assert "infer_modules" in s.agents.sub_agents


def test_arbitrary_sub_agent_purpose_with_unknown_preset_raises(tmp_path: Path):
    """Preset reference validation applies uniformly to every purpose,
    not just the bundled ones."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              qtime_pair_selector:
                - { preset: "made-up-preset" }
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="made-up-preset"):
        load(config_path=cfg, env={})


def test_retrieval_llm_explicit_null_preset_raises(tmp_path: Path):
    """Writing `preset: null` on retrieval_llm (instead of either a
    real preset name or `retrieval_llm: null` outright) is ambiguous
    and rejected — the error message points operators at the off
    switch."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              retrieval_llm:
                preset: null
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="retrieval_llm: null to disable"):
        load(config_path=cfg, env={})


def test_operator_can_add_a_new_preset_via_layered_merge(tmp_path: Path):
    """Adding a preset alongside the bundled ones — both survive
    (Q7 layered merge: presets dict is per-key merged, not replaced)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              presets:
                my-custom:
                  model: "openai/gpt-4o"
                  prompt_file: "pkg:workspace_app.kb.prompts/system.md"
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert "qwen3-local" in s.agents.presets  # bundled survives
    assert "my-custom" in s.agents.presets  # operator's added
    assert s.agents.presets["my-custom"].model == "openai/gpt-4o"


def test_operator_can_override_a_single_preset_field(tmp_path: Path):
    """Override just `qwen3-local`'s model — its other fields keep
    bundled values (description, suggestions, …)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              presets:
                qwen3-local:
                  model: "ollama_chat/qwen3:32b"
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    p = s.agents.presets["qwen3-local"]
    assert p.model == "ollama_chat/qwen3:32b"
    # Other preset fields inherited from bundle. (#94: picker presets no longer
    # carry a prompt_file — App agents get their prompt from app.json.)
    assert p.prompt_file == ""
    assert p.description  # bundled description inherited
    assert len(p.suggestions) > 0  # bundled suggestions inherited


def test_operator_replacing_a_sub_agent_list_replaces_the_whole_list(tmp_path: Path):
    """list = replace (Q5). If an operator wants their own kb_chat picker
    list, they write the complete list — it replaces the bundled default."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              kb_chat:
                - { preset: "kb-default", name: "Only KB" }
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert len(s.agents.kb_chat) == 1
    assert s.agents.kb_chat[0]["name"] == "Only KB"


def test_load_with_no_file_returns_default_settings():
    """`load(config_path=None)` and `Settings()` give an equivalent
    settings shape (server.port etc. all match)."""
    s = load(config_path=None, env={})
    bundled = Settings()
    assert s.server == bundled.server
    assert s.kb.embedder == bundled.kb.embedder
    assert set(s.agents.presets) == set(bundled.agents.presets)


def test_missing_config_yaml_file_path_is_fine(tmp_path: Path):
    """The path is provided but the file doesn't exist — treat as
    "no config", same as `config_path=None`. This is what happens
    when ./config.yaml is just absent in a fresh deploy."""
    missing = tmp_path / "does-not-exist.yaml"
    s = load(config_path=missing, env={})
    assert s.server.port == 8000  # bundled default


def test_workspace_app_config_env_var_picks_up_the_path(tmp_path: Path):
    """`WORKSPACE_APP_CONFIG=/path/to.yaml` env var is the deploy-time
    config-path override (same as the existing from_env behaviour)."""
    cfg = tmp_path / "elsewhere.yaml"
    cfg.write_text("server:\n  port: 7777\n", encoding="utf-8")
    s = load(config_path=None, env={"WORKSPACE_APP_CONFIG": str(cfg)})
    assert s.server.port == 7777


# ─── issue #39: kb.vlm_llm + kb.parsers_disabled ──────────────────────


def test_default_vlm_llm_references_the_bundled_kb_vlm_preset():
    """A fresh deploy gets the VLM parsers wired to the bundled
    `kb-vlm` preset (local qwen2.5-vl via Ollama) out of the box."""
    s = load(config_path=None, env={})
    assert s.kb.vlm_llm is not None
    assert s.kb.vlm_llm.preset == "kb-vlm"


def test_vlm_llm_null_disables_vlm_parsers(tmp_path: Path):
    """`kb.vlm_llm: null` is the off switch — `get_kb_vlm` returns None
    and the VLM-backed parsers stop matching (docs stay stored,
    reindex picks them up once a VLM is configured)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              vlm_llm: null
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.vlm_llm is None


def test_vlm_llm_referencing_unknown_preset_raises(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              vlm_llm:
                preset: "made-up-vlm"
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="made-up-vlm"):
        load(config_path=cfg, env={})


def test_parsers_disabled_round_trips(tmp_path: Path):
    """`kb.parsers_disabled` lists bundled parser class names the
    registry must skip — the Docling adaptation point: an operator
    swaps PDF handling by registering a custom parser AND disabling
    the bundled one, all in config."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              parsers_disabled: ["PdfParser", "DocxParser"]
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.parsers_disabled == ["PdfParser", "DocxParser"]


# ─── issue #284: kb.deck_vlm (make_deck multimodal model) ─────────────


def test_deck_vlm_defaults_to_none_reusing_vlm_llm():
    """`kb.deck_vlm` is unset by default — the factory then reuses `kb.vlm_llm`,
    so a deploy with a VLM wired gets `make_deck` for free."""
    s = load(config_path=None, env={})
    assert s.kb.deck_vlm is None


def test_deck_vlm_round_trips(tmp_path: Path):
    """An operator points `make_deck` at a dedicated (stronger) multimodal
    preset via `kb.deck_vlm`."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              deck_vlm:
                preset: "kb-vlm"
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.kb.deck_vlm is not None
    assert s.kb.deck_vlm.preset == "kb-vlm"


def test_deck_vlm_referencing_unknown_preset_raises(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            kb:
              deck_vlm:
                preset: "made-up-deck-vlm"
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="made-up-deck-vlm"):
        load(config_path=cfg, env={})


def test_observability_llm_log_defaults_enabled():
    """The LLM call log ships enabled by default (the operator wants it on);
    dir + keep_days carry sensible defaults."""
    s = load(config_path=None, env={})
    assert s.observability.llm_log.enabled is True
    assert s.observability.llm_log.dir == "logs/llm"
    assert s.observability.llm_log.keep_days == 0


def test_observability_llm_log_can_be_disabled(tmp_path: Path):
    """An operator can turn the LLM call log off in config.yaml."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            observability:
              llm_log:
                enabled: false
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.observability.llm_log.enabled is False


def test_sandbox_log_timeout_defaults_to_60_and_is_configurable(tmp_path: Path):
    """#70: log_timeout is a peer of exec_timeout — same 60s default, settable
    (0 disables the idle cap so a long job can rely on log_timeout alone)."""
    assert load(config_path=None, env={}).sandbox.log_timeout == 60.0
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            sandbox:
              exec_timeout: 0
              log_timeout: 120
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.sandbox.exec_timeout == 0
    assert s.sandbox.log_timeout == 120


def test_sandbox_isolation_block_loads_as_typed_dataclass_345(tmp_path: Path):
    """#345: operator YAML for the per-item-user isolation block must construct
    the nested `SandboxIsolationSettings` (typed access), not a raw dict — and
    `max_workspace_bytes` rides the flat sandbox section."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            sandbox:
              max_workspace_bytes: 1048576
              isolation:
                enabled: true
                memory_max: 1G
                cpu_cores: 2.0
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.sandbox.max_workspace_bytes == 1048576
    iso = s.sandbox.isolation
    assert iso.enabled is True
    assert iso.memory_max == "1G"
    assert iso.cpu_cores == 2.0
    assert iso.uid_base == 1_000_000  # untouched key keeps its default


def test_sandbox_durable_block_loads_as_typed_dataclass_501(tmp_path: Path):
    """#501: the sandbox durable-store selection (nfs_tree lives HERE, scoped to
    the sandbox — NOT on the global filestore.kind) must construct a nested
    `SandboxDurableSettings`, so the API's specstar filestore stays untouched."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            sandbox:
              durable:
                kind: nfs_tree
                nfs_root: /mnt/nfs/workspaces
                migrate_from: specstar
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    d = s.sandbox.durable
    assert d.kind == "nfs_tree"
    assert d.nfs_root == "/mnt/nfs/workspaces"
    assert d.migrate_from == "specstar"


def test_sandbox_durable_defaults_to_following_the_api_filestore_501(tmp_path: Path):
    """#501: no durable block ⇒ kind "" — sandbox persistence follows the API
    specstar filestore (zero behaviour change for existing deploys)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("sandbox:\n  kind: local\n", encoding="utf-8")
    s = load(config_path=cfg, env={})
    assert s.sandbox.durable.kind == ""
    assert s.sandbox.durable.nfs_root == ""
    assert s.sandbox.durable.migrate_from == ""
