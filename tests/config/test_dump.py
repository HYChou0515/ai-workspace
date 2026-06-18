"""`config.dump` — render a resolved `Settings` + provenance as annotated
YAML for the startup config dump (observability feature A).

Two surfaces share one renderer, differing only in secret handling:
- the on-disk file reveals real values (0600) so it's a faithful reproducer;
- stdout masks secrets so it's paste-safe.

Tests assert the observable output text, not the walk internals.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from workspace_app.config.dump import emit_config_dump, render
from workspace_app.config.loader import load_with_provenance


def _resolved(tmp_path: Path, yaml_text: str, env: dict[str, str] | None = None):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(dedent(yaml_text), encoding="utf-8")
    return load_with_provenance(config_path=cfg, env=env or {})


def test_render_shows_value_and_source_comment(tmp_path: Path):
    """Each leaf renders `key: value` with a trailing `# ← <source>` comment."""
    settings, prov = _resolved(
        tmp_path,
        """
        server:
          port: 9090
        """,
    )
    out = render(settings, prov, reveal_secrets=True)
    assert "port: 9090  # ← config.yaml" in out
    assert "host: 127.0.0.1  # ← default" in out


def test_render_masks_secret_on_stdout_reveals_in_file(tmp_path: Path):
    """A secret is masked when `reveal_secrets=False` (stdout) and shown in
    full when True (file); an env-sourced secret names its `${VAR}`."""
    settings, prov = _resolved(
        tmp_path,
        """
        llm:
          api_key: ${OPENAI_KEY}
        """,
        env={"OPENAI_KEY": "sk-secretvalue123"},  # 17 chars
    )
    masked = render(settings, prov, reveal_secrets=False)
    revealed = render(settings, prov, reveal_secrets=True)
    assert "sk-secretvalue123" not in masked
    assert "api_key: *** set (17 chars) via ${OPENAI_KEY} ***  # ← env" in masked
    assert "sk-secretvalue123" in revealed


def test_render_masks_unset_secret(tmp_path: Path):
    """An unset secret reports `*** unset ***`, not a fake length."""
    settings, prov = _resolved(tmp_path, "server:\n  port: 9090\n")
    masked = render(settings, prov, reveal_secrets=False)
    assert "api_key: *** unset ***  # ← default" in masked


def test_render_base_url_is_not_masked(tmp_path: Path):
    """`base_url` is not a secret — the operator wants to see where it calls."""
    settings, prov = _resolved(
        tmp_path,
        """
        llm:
          base_url: http://host:11434
        """,
    )
    masked = render(settings, prov, reveal_secrets=False)
    assert "base_url: http://host:11434  # ← config.yaml" in masked


def test_render_output_is_loadable_yaml_round_tripping_values(tmp_path: Path):
    """The whole tree renders to valid YAML whose values round-trip — the
    file doubles as a reproducer, so it must re-load."""
    import yaml

    settings, prov = _resolved(tmp_path, "server:\n  port: 9090\n")
    text = render(settings, prov, reveal_secrets=True)
    loaded = yaml.safe_load(text)
    assert loaded["server"]["port"] == 9090
    assert loaded["server"]["host"] == "127.0.0.1"
    assert loaded["kb"]["embedder"]["model"] == "ollama/bge-m3"
    assert loaded["agents"]["presets"]["qwen3-local"]["model"] == "ollama_chat/qwen3:14b"


def test_render_keeps_digit_string_a_string(tmp_path: Path):
    """A string value that looks numeric must stay a string on round-trip —
    bare `123` would re-load as int 123 (distortion the user forbids)."""
    import yaml

    settings, prov = _resolved(
        tmp_path,
        """
        server:
          default_user: "123"
        """,
    )
    text = render(settings, prov, reveal_secrets=True)
    loaded = yaml.safe_load(text)
    assert loaded["server"]["default_user"] == "123"
    assert isinstance(loaded["server"]["default_user"], str)


# ─── emit_config_dump: the startup orchestration ────────────────────────


def test_emit_writes_revealed_0600_file_and_prints_masked(tmp_path: Path):
    """Writes the real-value YAML next to the config at 0600, prints the
    masked dump, and tells the operator where the full file is."""
    import io
    import stat

    cfg = tmp_path / "config.yaml"
    cfg.write_text("llm:\n  api_key: sk-realsecret\n", encoding="utf-8")
    settings, prov = load_with_provenance(config_path=cfg, env={})
    out = io.StringIO()

    written = emit_config_dump(settings, prov, config_dir=tmp_path, stream=out)

    assert written == tmp_path / "config.resolved.yaml"
    file_text = written.read_text(encoding="utf-8")  # ty: ignore[unresolved-attribute]
    assert "sk-realsecret" in file_text  # real value in the 0600 file
    assert oct(stat.S_IMODE(written.stat().st_mode)) == "0o600"  # ty: ignore[unresolved-attribute]

    printed = out.getvalue()
    assert "sk-realsecret" not in printed  # masked on stdout
    assert "*** set" in printed
    assert "config.resolved.yaml" in printed  # points at the full file


def test_emit_falls_back_to_cwd_when_no_config_dir(tmp_path: Path, monkeypatch):
    """No config file → the resolved dump lands in the cwd."""
    import io

    monkeypatch.chdir(tmp_path)
    settings, prov = load_with_provenance(config_path=None, env={})
    out = io.StringIO()

    written = emit_config_dump(settings, prov, config_dir=None, stream=out)

    assert written == tmp_path / "config.resolved.yaml"
    assert written.is_file()  # ty: ignore[unresolved-attribute]


def test_emit_never_raises_when_file_unwritable(tmp_path: Path):
    """Best-effort: a failed write must not break startup — still prints,
    returns None."""
    import io

    settings, prov = load_with_provenance(config_path=None, env={})
    out = io.StringIO()
    bad_dir = tmp_path / "does-not-exist"  # parent missing → write fails

    written = emit_config_dump(settings, prov, config_dir=bad_dir, stream=out)

    assert written is None
    assert out.getvalue()  # the masked dump still printed
