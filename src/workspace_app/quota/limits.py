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

The consequence worth knowing: a per-user `cpu`/`memory` cap sums what each live
sandbox is ALLOWED to consume — and that allowance has two possible authors. An
App can state it, and when nobody does, the BACKEND applies its own
(`SANDBOX_HOST_*`, `sandbox.isolation.*`). Both are real ceilings that a real
cgroup enforces, so both are honest terms in the sum.

What is NOT honest is inventing a number when neither party set one: a backend
that caps nothing (mock, the plain local process) makes the dimension genuinely
unsummable, and then the cap cannot bind. That — not "did an App declare it" —
is what `warn_unenforceable_dimensions` reports. `count` binds regardless.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..apps.manifest import AppManifest, AppResources
from ..config.schema import PerUserResources, ResourceAmounts, Settings
from ..sandbox.protocol import EnforcedLimits

logger = logging.getLogger(__name__)

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
class _SummedDimension:
    """One per-person dimension that is a SUM over live sandboxes, and therefore
    depends on Apps having declared what they cost.

    Both halves live together on purpose. The previous shape read the configured
    value from one place and the declared value from an `if dimension == "cpu"
    else memory` further down — so a third dimension would silently have been
    treated as memory, and nothing would have said so."""

    configured: Callable[[PerUserResources], float]
    stated: Callable[[ResourceLimits], float | int | None]
    # What the BACKEND applies when the App states nothing. Lives here with the
    # other two for the reason the docstring above gives: a third dimension
    # added without its own accessor would silently be treated as another one's.
    backend: Callable[[EnforcedLimits], float | int | None]


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


# The dimensions a per-person cap SUMS. `count` is absent on purpose: counting
# sandboxes needs nobody to have declared anything, so it can never be inert.
_SUMMED_DIMENSIONS: dict[str, _SummedDimension] = {
    "cpu": _SummedDimension(lambda u: u.cpu, lambda lim: lim.cpu_cores, lambda e: e.cpu_cores),
    "memory": _SummedDimension(
        lambda u: parse_size(u.memory), lambda lim: lim.memory_bytes, lambda e: e.memory_bytes
    ),
}


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


def _pick_disk(declared: str, cfg: str, fallback: int) -> int:
    """`_pick_size` for the one dimension whose fallback is always a number."""
    got = _pick_size(declared, cfg, fallback)
    assert got is not None  # fallback is an int, so the chain cannot end in None
    return got


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
        # already spells unlimited as 0, so it needs no translation. The
        # fallback is an int, so this branch can never yield None — disk is the
        # dimension THIS app enforces and always has a number for.
        disk_bytes=_pick_disk(
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


def warn_unenforceable_dimensions(
    per_user: PerUserResources,
    limits: Mapping[str, ResourceLimits],
    *,
    enforced: EnforcedLimits,
) -> list[str]:
    """Name any per-person dimension the deploy set that cannot fully bind.

    `enforced` is what the sandbox BACKEND applies to a sandbox whose spec states
    nothing — `Sandbox.effective_limits(SandboxSpec())`. It is required, and it
    is the half this check used to be missing: judging on App declarations alone
    made this line announce that `per_user.cpu` "never fires" for exactly the
    configuration in which the gate does refuse a turn. Two statements about one
    config, one of them printed at boot to the operator.

    It cannot express "we could not ask". `HttpSandbox` reports an unreachable
    host as "enforces nothing" — the same value as a host that genuinely caps
    nothing — so a branch for the unknown case looked careful and was
    unreachable from the only production caller. The wording below carries that
    ambiguity instead, because the wording is the part an operator reads.

    A per-user `cpu` / `memory` cap sums what each live sandbox is ALLOWED to
    consume. A term is missing only when NEITHER the App nor the backend states
    one. Two shapes matter, and the second is the nastier one:

    * **nobody declared** — the cap never fires at all;
    * **some declared** — the cap fires against a partial sum, so the same limit
      binds or not depending on which Apps a person happens to be using. That
      one LOOKS like it is working, which is exactly why it needs saying.

    Checked PER DIMENSION. Sharing one "did anyone state anything?" answer
    across cpu and memory meant an App declaring only `memory` silenced the
    `cpu` warning while `cpu` still could not bind.

    `count` is never affected: counting sandboxes needs nobody to declare
    anything. Not fatal — the config is not wrong, it is waiting for an App to
    say what it costs.

    A deploy with NO Apps at all reports nothing: every dimension is vacuously
    complete, and there is no workload for a cap to bind against either.
    """
    messages: list[str] = []
    for name, dim in _SUMMED_DIMENSIONS.items():
        if not dim.configured(per_user):
            continue
        if dim.backend(enforced) is not None:
            # The backend caps this dimension for a sandbox that declares
            # nothing, so every sandbox contributes a real term whatever its App
            # says. Nothing to warn about.
            continue
        silent = sorted(slug for slug, lim in limits.items() if dim.stated(lim) is None)
        if not silent:
            continue  # every App states this dimension — the sum is complete
        where = f"app.json `resources`, `resources.per_app.default.{name}`, or the sandbox backend"
        if len(silent) == len(limits):
            messages.append(
                f"resources.per_user.{name} is set, but nothing this check can "
                f"see states a {name} cost ({where}), so the sum has no terms "
                f"and this limit does not fire. If the sandbox host was "
                f"unreachable just now, this line cannot tell that apart from a "
                f"host that caps nothing — re-check once it is up. "
                f"resources.per_user.count applies either way."
            )
        else:
            messages.append(
                f"resources.per_user.{name} is set, but these Apps state no "
                f"{name} cost: {', '.join(silent)} ({where}). Their sandboxes "
                f"count as zero, so the limit binds against a partial sum — it "
                f"will fire or not depending on which Apps a person is using."
            )
    messages.extend(_impossible_dimensions(per_user, limits, enforced))
    for message in messages:
        logger.warning("quota: %s", message)
    return messages


def _impossible_dimensions(
    per_user: PerUserResources,
    limits: Mapping[str, ResourceLimits],
    enforced: EnforcedLimits,
) -> list[str]:
    """Name any per-person cap so small that ONE sandbox already exceeds it.

    Then nobody can open even their first environment, and the refusal tells
    them to close one — advice that cannot be followed, on a screen that will
    show nothing to close. The limit is not "tight", it is closed, and that is
    almost never what an operator meant to type.

    Reported rather than fatal, for the same reason the rest of this function
    is: the config is not malformed, and a deploy may genuinely want a dimension
    parked at zero-usable while it is being tuned."""
    out: list[str] = []
    for name, dim in _SUMMED_DIMENSIONS.items():
        cap = dim.configured(per_user)
        if not cap:
            continue
        backend = dim.backend(enforced)
        costs = {
            slug: (stated if (stated := dim.stated(lim)) is not None else backend)
            for slug, lim in limits.items()
        }
        over = sorted(slug for slug, cost in costs.items() if cost and cost > cap)
        if over:
            out.append(
                f"resources.per_user.{name} is {cap}, which is smaller than ONE "
                f"sandbox of: {', '.join(over)}. Nobody can open even their first "
                f"environment for those Apps — the refusal will tell them to close "
                f"one they do not have."
            )
    return out


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
