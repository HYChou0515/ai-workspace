"""P1 — per-app sandbox resource limits: parsing, layering, and the boot clamp.

The knob has THREE layers, deliberately ordered so an existing deploy that sets
nothing new keeps its exact behaviour:

    app.json `resources`  ◇  config `resources.per_app.default`  ◇  today's knobs
                                                     (`sandbox.isolation.*` /
                                                      `filestore.workspace_quota`)

`resources.per_app.max` is the deploy's ceiling: a resolved value above it is a
BOOT failure, never a silent trim — a config that says 8 cores while the pod
hands out 2 is exactly the dead-knob class we refuse to ship.
"""

from __future__ import annotations

import pytest

from workspace_app.apps.manifest import (
    AgentManifest,
    AppManifest,
    AppResources,
    ItemNouns,
)
from workspace_app.config.schema import (
    FilestoreSettings,
    PerAppResources,
    ResourceAmounts,
    ResourceSettings,
    SandboxIsolationSettings,
    SandboxSettings,
    Settings,
)
from workspace_app.quota.limits import (
    ResourceLimitError,
    ResourceLimits,
    parse_size,
    resolve_app_limits,
)

# ─── parse_size ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("512M", 512 * 1024**2),
        ("2G", 2 * 1024**3),
        ("64K", 64 * 1024),
        ("1024", 1024),
        ("  2G  ", 2 * 1024**3),
        ("2g", 2 * 1024**3),
    ],
)
def test_parse_size_reads_friendly_units(text, want):
    assert parse_size(text) == want


@pytest.mark.parametrize("text", ["", "0", "max"])
def test_parse_size_treats_unset_zero_and_max_as_no_limit(text):
    """One sentinel for "no limit" (0) so every caller compares the same way —
    `""` (never configured), `"0"` (the pre-existing `workspace_quota: 0`
    spelling) and `"max"` (the cgroup spelling) must not be three behaviours."""
    assert parse_size(text) == 0


@pytest.mark.parametrize("text", ["abc", "12X", "-5", "1.5G"])
def test_parse_size_rejects_junk_loudly(text):
    with pytest.raises(ResourceLimitError):
        parse_size(text)


# ─── layering ──────────────────────────────────────────────────────────


def _manifest(slug: str = "demo", **sandbox) -> AppManifest:
    return AppManifest(
        slug=slug,
        title=slug.title(),
        agent=AgentManifest(prompt_file="prompts/system.md"),
        item=ItemNouns(noun="Item", noun_plural="Items"),
        resources=AppResources(**sandbox) if sandbox else None,
    )


def test_unconfigured_app_inherits_todays_knobs_unchanged():
    """The zero-change guarantee: an app.json with no `resources` block and a
    config with no `resources` section must reproduce exactly what the deploy
    runs today — `sandbox.isolation.*` for cpu/memory, `filestore.workspace_quota`
    for disk."""
    settings = Settings(
        sandbox=SandboxSettings(
            isolation=SandboxIsolationSettings(cpu_cores=1.5, memory_max="768M")
        ),
        filestore=FilestoreSettings(workspace_quota=7 * 1024**3),
    )
    got = resolve_app_limits(_manifest(), settings)
    assert got == ResourceLimits(cpu_cores=1.5, memory_bytes=768 * 1024**2, disk_bytes=7 * 1024**3)


def test_config_default_overrides_todays_knobs():
    settings = Settings(
        sandbox=SandboxSettings(
            isolation=SandboxIsolationSettings(cpu_cores=1.5, memory_max="768M")
        ),
        filestore=FilestoreSettings(workspace_quota=7 * 1024**3),
        resources=ResourceSettings(
            per_app=PerAppResources(default=ResourceAmounts(cpu=2, memory="1G", disk="9G"))
        ),
    )
    got = resolve_app_limits(_manifest(), settings)
    assert got == ResourceLimits(cpu_cores=2, memory_bytes=1024**3, disk_bytes=9 * 1024**3)


def test_app_manifest_wins_over_config_default():
    settings = Settings(
        resources=ResourceSettings(
            per_app=PerAppResources(default=ResourceAmounts(cpu=2, memory="1G", disk="9G"))
        )
    )
    got = resolve_app_limits(_manifest(cpu=4, memory="4G", disk="20G"), settings)
    assert got == ResourceLimits(cpu_cores=4, memory_bytes=4 * 1024**3, disk_bytes=20 * 1024**3)


def test_app_manifest_overrides_are_per_dimension():
    """An app that only cares about memory must not have to restate cpu/disk —
    each dimension falls through on its own."""
    settings = Settings(
        resources=ResourceSettings(
            per_app=PerAppResources(default=ResourceAmounts(cpu=2, memory="1G", disk="9G"))
        )
    )
    got = resolve_app_limits(_manifest(memory="4G"), settings)
    assert got == ResourceLimits(cpu_cores=2, memory_bytes=4 * 1024**3, disk_bytes=9 * 1024**3)


def test_an_app_can_opt_out_of_a_size_limit_the_deploy_set():
    """`disk: "0"` is a STATEMENT (unlimited), not silence — it must stop the
    fall-through rather than read as unset and inherit the deploy's 9G."""
    settings = Settings(
        resources=ResourceSettings(
            per_app=PerAppResources(default=ResourceAmounts(memory="1G", disk="9G"))
        )
    )
    assert resolve_app_limits(_manifest(disk="0"), settings).disk_bytes == 0


def test_cpu_zero_is_unset_not_unlimited():
    """The deliberate asymmetry with sizes: a zero-core sandbox is not something
    anyone configures, so `cpu: 0` keeps falling through instead of meaning
    "no ceiling". Pinned so nobody "fixes" the inconsistency into a footgun."""
    settings = Settings(
        resources=ResourceSettings(per_app=PerAppResources(default=ResourceAmounts(cpu=3)))
    )
    assert resolve_app_limits(_manifest(cpu=0), settings).cpu_cores == 3


# ─── the deploy ceiling ────────────────────────────────────────────────


def test_app_asking_above_the_deploy_ceiling_fails_loud():
    settings = Settings(
        resources=ResourceSettings(
            per_app=PerAppResources(max=ResourceAmounts(cpu=2, memory="2G", disk="10G"))
        )
    )
    with pytest.raises(ResourceLimitError, match="cpu"):
        resolve_app_limits(_manifest(cpu=8), settings)


def test_ceiling_applies_to_the_deploys_own_default_too():
    """An operator whose `default` exceeds their own `max` is a config error, not
    a silently-trimmed value — the check is on the RESOLVED number, so it cannot
    be dodged by putting the too-large value in `default` instead of app.json."""
    settings = Settings(
        resources=ResourceSettings(
            per_app=PerAppResources(
                default=ResourceAmounts(memory="8G"), max=ResourceAmounts(memory="2G")
            )
        )
    )
    with pytest.raises(ResourceLimitError, match="memory"):
        resolve_app_limits(_manifest(), settings)


def test_unset_ceiling_dimension_imposes_no_limit():
    settings = Settings(
        resources=ResourceSettings(per_app=PerAppResources(max=ResourceAmounts(cpu=2)))
    )
    got = resolve_app_limits(_manifest(disk="500G"), settings)
    assert got.disk_bytes == 500 * 1024**3


def test_at_the_ceiling_is_allowed():
    settings = Settings(
        resources=ResourceSettings(per_app=PerAppResources(max=ResourceAmounts(cpu=2)))
    )
    assert resolve_app_limits(_manifest(cpu=2), settings).cpu_cores == 2


def test_the_error_names_the_offending_app():
    """The message has to say WHICH app.json is wrong, or an operator staring at
    a failed boot is left bisecting their own repo."""
    settings = Settings(
        resources=ResourceSettings(per_app=PerAppResources(max=ResourceAmounts(cpu=2)))
    )
    with pytest.raises(ResourceLimitError, match="greedy"):
        resolve_app_limits(_manifest("greedy", cpu=64), settings)
