"""docs/plan-telemetry-retention.md: `observability.telemetry.keep_days`
reaches `create_app` from the real composition root — a setting the deploy
reads but never passes is a knob that silently does nothing."""

from __future__ import annotations

from textwrap import dedent

import pytest

from workspace_app.config.loader import load


class _Stop(Exception):
    pass


@pytest.mark.parametrize(
    ("written", "passed"),
    [("", 14), ("observability:\n  telemetry:\n    keep_days: 3\n", 3)],
)
def test_keep_days_reaches_create_app(tmp_path, monkeypatch, written: str, passed: int) -> None:  # noqa: ANN001
    import workspace_app.__main__ as main_mod

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
        """)
        + written,
        encoding="utf-8",
    )
    seen: dict = {}

    def spy(**kwargs):  # noqa: ANN003, ANN202
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(main_mod, "PACKAGES", {})
    monkeypatch.setattr(main_mod, "create_app", spy)
    with pytest.raises(_Stop):
        main_mod.build_app(load(config_path=cfg, env={}), config_dir=None)
    assert seen["telemetry_keep_days"] == passed
