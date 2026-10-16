"""`server.ext_ai_enabled` reaches the app (docs/plan-ext-ai.md D5).

Through `config.yaml` → `build_app`, the deploy's own composition root: every
other test hands `ext_ai_enabled=` to `create_app` itself, so a knob read by
the loader and dropped on the way would pass them all and do nothing
(review round 1).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from workspace_app.__main__ import build_app
from workspace_app.config.loader import load


class _Built(Exception):
    pass


def _create_app_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: str) -> dict:
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    seen: dict = {}

    def create_app(**kw):  # noqa: ANN003, ANN202
        seen.update(kw)
        raise _Built

    monkeypatch.setattr("workspace_app.__main__.create_app", create_app)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(f"""
            sandbox:
              root: {tmp_path / "sandbox"}
            kb:
              embedder:
                model: ""
        """)
        + (f"server:\n{server}" if server else ""),
        encoding="utf-8",
    )
    with pytest.raises(_Built):
        build_app(load(config_path=cfg, env={}), config_dir=None)
    return seen


def test_a_deploy_that_turns_it_on_gets_it(tmp_path, monkeypatch):
    assert _create_app_kwargs(tmp_path, monkeypatch, "  ext_ai_enabled: true\n")["ext_ai_enabled"]


def test_a_deploy_that_says_nothing_does_not(tmp_path, monkeypatch):
    assert _create_app_kwargs(tmp_path, monkeypatch, "")["ext_ai_enabled"] is False
