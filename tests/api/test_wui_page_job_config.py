"""The page-job knobs reach the orchestrator the routes use (#942 D6).

Through `config.yaml` → `build_app`, the deploy's own composition root: a knob
read by the loader and dropped before `create_app` would be a setting that
silently does nothing (the `workflow_step_timeout` story).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from workspace_app.__main__ import build_app
from workspace_app.config.loader import load


def _app(tmp_path: Path, server: str = ""):
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
    return build_app(load(config_path=cfg, env={}), config_dir=None)


def test_a_deploy_that_says_nothing_gets_four_jobs_of_an_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    orch = _app(tmp_path).state.workflow_orchestrator
    assert (orch.page_job_limit, orch.page_job_timeout_s) == (4, 3600)


def test_the_deploy_sets_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    orch = _app(
        tmp_path, "  wui_page_job_limit: 2\n  wui_page_job_timeout_sec: 90\n"
    ).state.workflow_orchestrator
    assert (orch.page_job_limit, orch.page_job_timeout_s) == (2, 90)
