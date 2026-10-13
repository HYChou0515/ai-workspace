"""`server.ai_workspace_helper_dir` — where the operator publishes a packed outside
helper (docs/plan-outside-helper.md §3.7.2). Unset ⇒ nothing published."""

from __future__ import annotations

from pathlib import Path

from workspace_app.config.loader import load


def test_unset_publishes_nothing():
    assert load(env={}).server.ai_workspace_helper_dir == ""


def test_the_folder_is_read_from_the_config(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "server:\n  ai_workspace_helper_dir: /srv/ai-workspace-helper\n", encoding="utf-8"
    )

    assert (
        load(config_path=cfg, env={}).server.ai_workspace_helper_dir == "/srv/ai-workspace-helper"
    )
