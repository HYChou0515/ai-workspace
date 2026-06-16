"""`load_with_provenance` — the resolved config plus, per leaf, where the
value came from: the operator's `config.yaml`, an `${ENV}` interpolation, or
the bundled default.

This is the data behind the startup config dump (observability feature A):
the operator's pain is "I can't tell what I set from what defaulted", so every
leaf in the resolved tree carries its source.

Tests focus on the observable contract: given a small `config.yaml` + env,
what source does each dotted path report?
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from workspace_app.config.loader import load_with_provenance


def test_provenance_marks_operator_set_scalar_vs_default(tmp_path: Path):
    """An operator-written scalar reports `config.yaml`; an untouched
    sibling in the same section reports `default`."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            server:
              port: 9090
        """),
        encoding="utf-8",
    )
    settings, prov = load_with_provenance(config_path=cfg, env={})
    assert settings.server.port == 9090
    assert prov["server.port"].kind == "config.yaml"
    assert prov["server.host"].kind == "default"


def test_provenance_marks_env_sourced_leaf(tmp_path: Path):
    """A leaf whose YAML value is a `${VAR}` template reports `env`, not
    `config.yaml` — the operator set it, but the value lives in the env."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            llm:
              base_url: ${OLLAMA_URL}
        """),
        encoding="utf-8",
    )
    settings, prov = load_with_provenance(config_path=cfg, env={"OLLAMA_URL": "http://x:11434"})
    assert settings.llm.base_url == "http://x:11434"
    assert prov["llm.base_url"].kind == "env"
    # the raw template is kept so the dump can name the env var feeding a secret
    assert prov["llm.base_url"].ref == "${OLLAMA_URL}"


def test_provenance_remaps_flat_agents_purpose_to_sub_agents(tmp_path: Path):
    """Operators write sub-agent lists flat (`agents.workspace_chat`), but the
    resolved tree nests them under `agents.sub_agents.<purpose>`. Provenance
    must follow the value to its resolved path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            agents:
              workspace_chat:
                - preset: qwen3-local
                  name: My RCA
        """),
        encoding="utf-8",
    )
    settings, prov = load_with_provenance(config_path=cfg, env={})
    assert settings.agents.sub_agents["workspace_chat"][0]["name"] == "My RCA"
    assert prov["agents.sub_agents.workspace_chat[0].name"].kind == "config.yaml"
    assert prov["agents.sub_agents.workspace_chat[0].preset"].kind == "config.yaml"


def test_provenance_all_default_when_no_config():
    """No config file at all → every leaf reports `default`."""
    settings, prov = load_with_provenance(config_path=None, env={})
    assert settings.server.port == 8000
    assert prov  # non-empty
    assert {src.kind for src in prov.values()} == {"default"}


def test_provenance_written_value_equal_to_default_is_still_config(tmp_path: Path):
    """The operator's actual pain: a value they wrote that happens to equal
    the bundled default must still report `config.yaml` — provenance answers
    'did I write this?', not 'does it differ from the default?'."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            server:
              port: 8000
        """),
        encoding="utf-8",
    )
    settings, prov = load_with_provenance(config_path=cfg, env={})
    assert settings.server.port == 8000  # equals the default
    assert prov["server.port"].kind == "config.yaml"  # but it was written
