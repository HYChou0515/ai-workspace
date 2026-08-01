"""P1 — the `resources` section must reach `Settings` from YAML, and the boot
sweep must actually run over the Apps on disk.

Both halves are here because either one alone is a dead knob: a whitelisted YAML
key that nothing constructs silently does nothing, and a ceiling nothing calls at
startup only ever fails much later, in a request.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from workspace_app.config.loader import load
from workspace_app.config.schema import PerAppResources, ResourceAmounts, ResourceSettings, Settings
from workspace_app.quota.limits import ResourceLimitError, validate_discovered_apps


def test_resources_section_loads_from_yaml(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent("""
            resources:
              per_app:
                default:
                  cpu: 2
                  memory: 1G
                  disk: 30G
                max:
                  cpu: 4
                  memory: 8G
              per_user:
                count: 10
                disk: 200G
        """),
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.resources.per_app.default.cpu == 2
    assert s.resources.per_app.default.memory == "1G"
    assert s.resources.per_app.default.disk == "30G"
    assert s.resources.per_app.max.cpu == 4
    assert s.resources.per_user.count == 10
    assert s.resources.per_user.disk == "200G"
    # Untouched dimensions keep meaning "unset", not "zero allowed".
    assert s.resources.per_app.max.disk == ""
    assert s.resources.per_user.cpu == 0.0


def test_absent_resources_section_is_all_unset(tmp_path: Path):
    """An existing deploy's config.yaml has no `resources:` block at all. It must
    load to the all-unset shape — which resolves to today's numbers."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 9090\n", encoding="utf-8")
    s = load(config_path=cfg, env={})
    assert s.resources == ResourceSettings()


def test_unknown_key_under_resources_is_rejected(tmp_path: Path):
    """The loader validates against a whitelist — a typo must fail the boot, not
    be quietly ignored (`per_user.disc` would otherwise read as no quota)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("resources:\n  per_user:\n    disc: 10G\n", encoding="utf-8")
    with pytest.raises(Exception, match="disc"):
        load(config_path=cfg, env={})


def test_bundled_apps_pass_the_boot_sweep():
    """Every App shipped in `apps/` resolves cleanly under bundled defaults —
    this is the check `__main__` runs before it serves traffic."""
    validate_discovered_apps(Settings())  # no raise


def test_boot_sweep_fails_when_a_bundled_app_exceeds_the_ceiling():
    """A ceiling low enough to bite proves the sweep really reads the Apps on
    disk rather than passing vacuously."""
    settings = Settings(
        resources=ResourceSettings(
            per_app=PerAppResources(default=ResourceAmounts(cpu=2), max=ResourceAmounts(cpu=1))
        )
    )
    with pytest.raises(ResourceLimitError, match="cpu"):
        validate_discovered_apps(settings)
