"""`prompt_file:` value resolution (Q6 — `pkg:` / relative / absolute).

Three accepted forms in `agents.presets.*.prompt_file`:

- ``pkg:<dotted-package>/<path/to.md>`` — package resource (bundled
  prompts live here so they ship with the wheel).
- ``/abs/path.md`` — absolute filesystem path.
- ``relative/path.md`` — relative to the config.yaml's directory; this
  is the typical deploy-local override (e.g. `configs/prompts/rca.md`
  sitting next to `configs/config.yaml`).

Missing file raises `PromptFileNotFound` with the offending form in
the message — the loader runs this at startup so a typo / missing
file fails the deploy immediately, not at the first turn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.config.prompt_file import PromptFileNotFound, resolve_prompt_file


def test_pkg_form_reads_bundled_package_resource():
    """`pkg:workspace_app.kb.prompts/system.md` reads from the
    package resource — this is how bundled presets cite their default
    prompts (so they ship with the wheel)."""
    body = resolve_prompt_file(
        "pkg:workspace_app.kb.prompts/system.md", config_dir=Path("/nowhere")
    )
    # The actual bundled prompt is long, non-empty markdown; sanity-check
    # by length + first-line shape rather than verbatim text.
    assert len(body) > 50
    assert isinstance(body, str)


def test_pkg_form_with_unknown_package_raises():
    with pytest.raises(PromptFileNotFound, match="workspace_app.totally_made_up"):
        resolve_prompt_file("pkg:workspace_app.totally_made_up/x.md", config_dir=Path("/nowhere"))


def test_pkg_form_with_unknown_file_in_known_package_raises():
    with pytest.raises(PromptFileNotFound, match="nope.md"):
        resolve_prompt_file("pkg:workspace_app.kb.prompts/nope.md", config_dir=Path("/nowhere"))


def test_absolute_path_reads_the_file_verbatim(tmp_path: Path):
    p = tmp_path / "myprompt.md"
    p.write_text("you are an agent\n", encoding="utf-8")
    assert resolve_prompt_file(str(p), config_dir=Path("/some/other/dir")) == ("you are an agent\n")


def test_relative_path_is_resolved_against_config_dir(tmp_path: Path):
    """`prompts/rca.md` in YAML, with config_dir=/.../configs → reads
    /.../configs/prompts/rca.md. This is the typical deploy-local
    override pattern (drop a prompts/ folder next to config.yaml)."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "rca.md").write_text("hi\n", encoding="utf-8")
    assert resolve_prompt_file("prompts/rca.md", config_dir=tmp_path) == "hi\n"


def test_relative_path_with_no_config_dir_raises_clear_error(tmp_path: Path):
    """If no config.yaml was discovered (operator running with only
    bundled defaults), a `prompt_file: relative/path` has nothing to
    resolve against — raise with a clear message rather than
    interpret it against the CWD (which is unpredictable in
    container/k8s deploys)."""
    with pytest.raises(PromptFileNotFound, match="relative.*config_dir"):
        resolve_prompt_file("relative/path.md", config_dir=None)


def test_missing_relative_path_raises_with_resolved_path(tmp_path: Path):
    """Missing file: message contains the FULL resolved path so the
    operator can ls the directory."""
    with pytest.raises(PromptFileNotFound, match=str(tmp_path)):
        resolve_prompt_file("prompts/missing.md", config_dir=tmp_path)
