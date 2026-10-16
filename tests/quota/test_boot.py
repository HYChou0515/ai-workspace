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
from workspace_app.quota.limits import ResourceLimitError, resolve_discovered_apps


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


@pytest.mark.parametrize(
    ("written", "read"), [("", 21600.0), ("  disk_reconcile_interval_sec: 0\n", 0.0)]
)
def test_the_disk_reconcile_interval_reaches_settings(tmp_path: Path, written: str, read: float):
    """docs/plan-storage-all-items.md decision 7: 6 h by default, 0 ⇒ never."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("resources:\n  per_user:\n    count: 1\n" + written, encoding="utf-8")
    assert load(config_path=cfg, env={}).resources.disk_reconcile_interval_sec == read


def test_absent_resources_section_is_all_unset(tmp_path: Path):
    """An existing deploy's config.yaml has no `resources:` block at all. It must
    load to the all-unset shape — which resolves to today's numbers."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 9090\n", encoding="utf-8")
    s = load(config_path=cfg, env={})
    assert s.resources == ResourceSettings()


@pytest.mark.parametrize(
    ("written", "read"),
    [("max", 0.0), ('"0"', 0.0), ('""', 0.0), ('"4"', 4.0), ("4", 4.0), ("1.5", 1.5)],
)
def test_a_cpu_amount_is_read_as_a_number_whatever_its_spelling(
    tmp_path: Path, written: str, read: float
):
    """Review round 1 (plan-item-size-ceiling D2): `per_app.max.cpu: max` reached
    Settings as the STRING "max" — the loader converts nothing — and, once the
    max became an item's ceiling, `min(2.0, "max")` raised on every sized item.
    `""` / `"0"` / `"max"` mean "not set" for cpu as they do for sizes."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "resources:\n  per_app:\n"
        f"    default:\n      cpu: {written}\n"
        f"    max:\n      cpu: {written}\n",
        encoding="utf-8",
    )
    s = load(config_path=cfg, env={})
    assert s.resources.per_app.default.cpu == read
    assert s.resources.per_app.max.cpu == read


@pytest.mark.parametrize("bad", ["four", "nan", "inf", "-1", "true", "[2]", "{a: 1}"])
def test_a_cpu_amount_that_is_not_a_number_of_cores_fails_the_boot(tmp_path: Path, bad: str):
    """Loud, naming the key — the way `parse_size` treats a size it cannot read.
    Round 2: `nan` made the cap a silent no-op (`min(2.0, nan)` is 2.0), `inf`
    printed 「最多 inf 核」, `-1` clamped every item to -1 cores, `true` was 1 core,
    and a list raised a bare TypeError naming nothing."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"resources:\n  per_app:\n    max:\n      cpu: {bad}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"per_app\.max\.cpu"):
        load(config_path=cfg, env={})


@pytest.mark.parametrize(("written", "read"), [("max", 0.0), ('"2"', 2.0), ("2", 2.0)])
def test_a_persons_cpu_budget_is_read_as_a_number_too(tmp_path: Path, written: str, read: float):
    """Round 2: the sibling key. `per_user.cpu` reached the clamp as a string
    (an env-interpolated `${C}` always is one), and `min(2.0, "2")` raised on
    every sized item — the same class as `per_app.max.cpu`, left unswept."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"resources:\n  per_user:\n    cpu: {written}\n", encoding="utf-8")
    assert load(config_path=cfg, env={}).resources.per_user.cpu == read


def test_a_persons_cpu_budget_that_is_not_a_number_fails_the_boot(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("resources:\n  per_user:\n    cpu: lots\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"per_user\.cpu"):
        load(config_path=cfg, env={})


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
    resolve_discovered_apps(Settings())  # no raise


def test_boot_sweep_fails_when_a_bundled_app_exceeds_the_ceiling():
    """A ceiling low enough to bite proves the sweep really reads the Apps on
    disk rather than passing vacuously."""
    settings = Settings(
        resources=ResourceSettings(
            per_app=PerAppResources(default=ResourceAmounts(cpu=2), max=ResourceAmounts(cpu=1))
        )
    )
    with pytest.raises(ResourceLimitError, match="cpu"):
        resolve_discovered_apps(settings)
