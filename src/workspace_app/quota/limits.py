"""Resolve ONE App's concrete resource limits from the three layers that may
state them, and refuse a config that asks for more than the deploy allows.

    app.json `resources`  ◇  config `resources.per_app.default`  ◇  today's knobs
                                                     (`sandbox.isolation.*` /
                                                      `filestore.workspace_quota`)

The bottom layer is what makes this change invisible to existing deploys: an App
that declares nothing, in a deploy that configures nothing, resolves to exactly
the numbers it runs with today. Each dimension falls through on its OWN — an App
that only cares about memory does not have to restate cpu and disk.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..apps.manifest import AppManifest, AppResources
from ..config.schema import ResourceAmounts, Settings

_SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}

# One sentinel for "no limit" across every dimension, so callers compare the
# same way everywhere: 0. It is what an unset knob resolves to, and what the
# pre-existing `workspace_quota: 0` already meant.
NO_LIMIT = 0


class ResourceLimitError(ValueError):
    """A resource value is unreadable, or asks for more than the deploy allows.

    Raised at BOOT (config parse / app scan), never mid-request — the whole point
    is that an operator learns their config is wrong before it serves traffic,
    instead of discovering it as a silently different number later."""


def parse_size(text: str) -> int:
    """Friendly size ("512M", "2G") → bytes. Unset / "0" / "max" → `NO_LIMIT`.

    Three spellings collapse to one sentinel on purpose: `""` (never
    configured), `"0"` (how `filestore.workspace_quota` already spells
    unlimited) and `"max"` (how a cgroup spells it) must not become three
    behaviours that differ only by which file the value came from."""
    text = text.strip()
    if text in ("", "0", "max"):
        return NO_LIMIT
    unit = _SIZE_UNITS.get(text[-1].upper())
    digits = text[:-1] if unit else text
    if not digits.isdigit():
        raise ResourceLimitError(
            f"unreadable size {text!r} — want an integer with an optional "
            f"K/M/G/T suffix (e.g. '512M', '2G'), or 'max' for no limit"
        )
    return int(digits) * (unit or 1)


@dataclass(frozen=True)
class ResourceLimits:
    """The resolved, concrete limits for one item. Byte counts, not friendly
    strings — every consumer wants to compare numbers, and doing the parse once
    here means a typo is a boot error rather than a per-request surprise.
    `NO_LIMIT` (0) in any dimension means unbounded."""

    cpu_cores: float
    memory_bytes: int
    disk_bytes: int


def _pick_cpu(manifest: AppResources | None, cfg: ResourceAmounts, fallback: float) -> float:
    """cpu's fall-through. DELIBERATELY asymmetric with `_pick_size`: `cpu: 0`
    means "not stated" and keeps falling through, whereas `disk: "0"` means
    "unlimited" and stops. Sizes have an established spelling for unbounded
    (`0`, which is what `filestore.workspace_quota` already used, and `max`,
    which is what a cgroup writes); cpu has none — a zero-core sandbox is not a
    thing anyone means, so reading it as unset is the only useful choice. An App
    that genuinely wants no cpu ceiling states a large number."""
    if manifest is not None and manifest.cpu:
        return manifest.cpu
    return cfg.cpu or fallback


def _pick_size(declared: str, cfg: str, fallback: int) -> int:
    """One dimension's fall-through. Note the test is on the RAW string, not on
    the parsed number: `"0"` is a deliberate "unlimited here", and must stop the
    search rather than read as unset and hand over to the layer below."""
    if declared:
        return parse_size(declared)
    if cfg:
        return parse_size(cfg)
    return fallback


def resolve_app_limits(manifest: AppManifest, settings: Settings) -> ResourceLimits:
    """The three-layer resolution for one App, validated against the deploy's
    ceiling. Raises `ResourceLimitError` if the resolved value exceeds
    `resources.per_app.max`.

    The check is on the RESOLVED number rather than on the manifest's declared
    one, so an operator cannot dodge their own ceiling by moving the too-large
    value from app.json into `per_app.default`."""
    per_app = settings.resources.per_app
    declared = manifest.resources
    limits = ResourceLimits(
        cpu_cores=_pick_cpu(declared, per_app.default, settings.sandbox.isolation.cpu_cores),
        memory_bytes=_pick_size(
            declared.memory if declared else "",
            per_app.default.memory,
            parse_size(settings.sandbox.isolation.memory_max),
        ),
        # The pre-existing per-item disk knob. It is already a byte count and
        # already spells unlimited as 0, so it needs no translation.
        disk_bytes=_pick_size(
            declared.disk if declared else "",
            per_app.default.disk,
            settings.filestore.workspace_quota,
        ),
    )
    _enforce_ceiling(manifest.slug, limits, per_app.max)
    return limits


def _enforce_ceiling(slug: str, limits: ResourceLimits, ceiling: ResourceAmounts) -> None:
    """Fail loud on anything above the deploy's ceiling. An unset ceiling
    dimension imposes nothing — `max` is opt-in per dimension, like everything
    else here."""
    checks: list[tuple[str, float, float]] = [
        ("cpu", limits.cpu_cores, ceiling.cpu),
        ("memory", limits.memory_bytes, parse_size(ceiling.memory)),
        ("disk", limits.disk_bytes, parse_size(ceiling.disk)),
    ]
    for name, got, cap in checks:
        if cap and got > cap:
            raise ResourceLimitError(
                f"app {slug!r} resolves to {name}={got}, above this deploy's "
                f"resources.per_app.max.{name}={cap}. Lower the app's "
                f"`resources` block in app.json, or raise the ceiling."
            )


def resolve_discovered_apps(settings: Settings) -> dict[str, ResourceLimits]:
    """Every App on disk → its resolved limits. What the boot sequence calls.

    Resolving and validating are ONE act on purpose: hand the caller a mapping
    built by the same pass that would have rejected a bad value, so the numbers
    the app serves with are provably the numbers the boot check approved. Two
    passes could drift, and the one that drifts is the silent one."""
    from ..apps.catalog import discover_app_slugs
    from ..apps.manifest import load_app_manifest

    manifests = [load_app_manifest(slug) for slug in discover_app_slugs()]
    return {m.slug: resolve_app_limits(m, settings) for m in manifests}
