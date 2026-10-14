"""The composition roots hand the runner and the writer ONE recorder
(docs/plan-tool-usage-stats.md §3.5) — two recorders would count every call
into one and write the other, which is empty: a report that reads zero."""

from __future__ import annotations

from textwrap import dedent

from workspace_app.config.loader import load


def _settings(tmp_path):  # noqa: ANN001, ANN202
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(f"""
            server:
              run_consumers: false
            filestore:
              kind: specstar
            sandbox:
              root: {tmp_path / "sandbox"}
            skill_hub:
              git_root: {tmp_path / "skill-hub-git"}
            kb:
              embedder:
                model: ""
        """),
        encoding="utf-8",
    )
    return load(config_path=cfg, env={})


def test_the_api_counts_into_the_recorder_it_writes(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from workspace_app.__main__ import build_app

    monkeypatch.setattr("workspace_app.__main__.PACKAGES", {})
    app = build_app(_settings(tmp_path), config_dir=None)

    store = app.state.tool_usage_store
    runner = app.state.turn_engine._runner  # noqa: SLF001
    assert store is not None
    assert runner._tool_usage is store._recorder  # noqa: SLF001


def test_a_worker_counts_into_the_recorder_it_writes(tmp_path) -> None:  # noqa: ANN001
    from workspace_app.agent.tool_usage import ToolUsageRecorder
    from workspace_app.factories import get_runner

    rec = ToolUsageRecorder()

    runner = get_runner(_settings(tmp_path), tool_usage=rec)

    assert runner._tool_usage is rec  # ty: ignore[unresolved-attribute]  # noqa: SLF001
