"""Resolve ONE App's concrete resource limits from the three layers that may
state them, and refuse a config that asks for more than the deploy allows.

    app.json `resources`  ◇  config `resources.per_app.default`  ◇  (nobody said)

Each dimension falls through on its OWN — an App that only cares about memory
does not have to restate cpu and disk.

**What "nobody said" resolves to differs by who ENFORCES the dimension**, and
getting this wrong is not cosmetic:

- `cpu` / `memory` are enforced by the SANDBOX BACKEND, which has its own
  configured defaults (`sandbox.isolation.*` for the local one,
  `SANDBOX_HOST_*` on the host). Undeclared therefore resolves to **None** —
  "nobody stated this, use yours". Folding the local backend's numbers in here
  looked harmless because they are the same numbers; over the http wire it
  meant the app OVERWROTE the host's `SANDBOX_HOST_MEMORY_MAX` with a value no
  operator ever chose, and since both defaults are 512M/1.0 nothing could
  observe it until a host that had been tuned upward started OOM-killing.
- `disk` is enforced by THIS app, and `filestore.workspace_quota` is this app's
  own default, so it resolves to a concrete number. There is no other party
  holding an opinion about it.

The consequence worth knowing: a per-user `cpu`/`memory` cap can only bind for
Apps whose cost is actually stated (in app.json or `per_app.default`). You
cannot sum what nobody declared — and inventing a number for the sum is what
produced the bug above. `count` binds regardless.
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
    """The resolved limits for one item. Byte counts, not friendly strings —
    every consumer wants to compare numbers, and doing the parse once here means
    a typo is a boot error rather than a per-request surprise.

    `cpu_cores` / `memory_bytes` are `None` when NOBODY stated them, so the
    sandbox backend keeps whatever it was configured with (see the module
    docstring — this is the difference between honouring `SANDBOX_HOST_*` and
    silently replacing it). `0` remains "explicitly unbounded".

    `disk_bytes` is always concrete: this app enforces it and owns the fallback.
    `NO_LIMIT` (0) means unbounded."""

    cpu_cores: float | None
    memory_bytes: int | None
    disk_bytes: int


def _pick_cpu(manifest: AppResources | None, cfg: ResourceAmounts) -> float | None:
    """cpu's fall-through, ending in None (= the backend's own default).

    DELIBERATELY asymmetric with `_pick_size`: `cpu: 0` means "not stated" and
    keeps falling through, whereas `disk: "0"` means "unlimited" and stops.
    Sizes have an established spelling for unbounded (`0`, which is what
    `filestore.workspace_quota` already used, and `max`, which is what a cgroup
    writes); cpu has none — a zero-core sandbox is not a thing anyone means. An
    App that genuinely wants no cpu ceiling states a large number."""
    if manifest is not None and manifest.cpu:
        return manifest.cpu
    return cfg.cpu or None


def _pick_size(declared: str, cfg: str, fallback: int | None) -> int | None:
    """One dimension's fall-through. Note the test is on the RAW string, not on
    the parsed number: `"0"` is a deliberate "unlimited here", and must stop the
    search rather than read as unset and hand over to the layer below.

    `fallback` is None for a dimension somebody ELSE enforces (memory), and a
    number for one this app enforces (disk)."""
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
        cpu_cores=_pick_cpu(declared, per_app.default),
        # None ⇒ the sandbox backend keeps its own configured memory ceiling.
        memory_bytes=_pick_size(declared.memory if declared else "", per_app.default.memory, None),
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
    checks: list[tuple[str, float | None, float]] = [
        ("cpu", limits.cpu_cores, ceiling.cpu),
        ("memory", limits.memory_bytes, parse_size(ceiling.memory)),
        ("disk", limits.disk_bytes, parse_size(ceiling.disk)),
    ]
    for name, got, cap in checks:
        # An undeclared dimension has nothing to clamp — whatever the backend
        # applies is the operator's own configured choice, not an App's request.
        if got is not None and cap and got > cap:
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
