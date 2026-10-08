"""Third-party tools, from an app's declaration to the model (#674).

An app declares `{local name: artifact url}` in its `app.json`. Once per turn
the app asks the host to resolve them and gets back one answer that feeds two
places:

* the tool definitions handed to the model, and
* the `{name: sha}` the sandbox is created with.

Both from the SAME answer, which is the whole reason the host does the
resolving. If the app read the manifest itself, an author releasing between
the app's read and the sandbox's create would leave the model calling the new
bundle with the previous release's arguments — a failure that looks like a
broken tool and reproduces for nobody.

The app never holds an artifact-store credential; only the host does.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from .registry import CommandInfo, EnvNeed, PackageInfo

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolResolvingSandbox(Protocol):
    """A backend with an artifact store behind it. Only the hosted sandbox
    has one; local/mock backends answer `False` and third-party tools are
    reported as unavailable rather than silently missing."""

    resolves_tools: bool

    async def resolve_tools(self, declared: Mapping[str, str]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ToolProvenance:
    """Which release of one third-party tool this turn got, and who published
    it (#724).

    The app cannot look any of this up: it holds no artifact-store credential
    and never reads a manifest, by design. What the host returned here is the
    only account of what actually ran, which is why it is kept rather than
    thrown away — "the tool is behaving oddly" has no answer without it."""

    version: str
    author: str | None = None
    """``None`` for a bundle built before the builder published the field."""
    stale: bool = False
    """Served from the host's last-known-good copy because the artifact store
    was unreachable. Usable, but not necessarily the latest — and the two must
    not look alike to whoever is reading."""


@dataclass(frozen=True)
class MountedTool:
    """One third-party bundle a sandbox was CREATED with (plan-tool-running-version).

    A sandbox mounts its bundles once, at create, and keeps them; the resolve a
    later turn does describes the author's LATEST release. This is the other
    half — what is actually under `/.tools/<name>` — written at the moment the
    two are the same thing, so it can be compared with the latest afterwards.
    Compared by `sha` (D8): one version string published twice is two releases."""

    sha: str
    version: str


@dataclass(frozen=True)
class Drift:
    """How a live sandbox differs from the latest resolve for ONE tool."""

    missing: bool
    """The sandbox was created without it (D11)."""
    running: str = ""
    """When present but a different bundle: the release it runs (``""`` if
    that was not recorded). Different, not "older" — a sha says no more (D13)."""


@dataclass(frozen=True)
class ExternalTools:
    """What one turn learned about this app's third-party tools."""

    packages: tuple[PackageInfo, ...] = ()
    shas: dict[str, str] = field(default_factory=dict)
    """`{name: sha}` for the sandbox spec — what to mount, when it is created."""
    refused: dict[str, str] = field(default_factory=dict)
    """`{name: reason}`. A refusal removes one tool and leaves the turn alone;
    the reason exists so the agent and the user learn WHY it is missing rather
    than watching it quietly not be there (the #480 shape)."""
    provenance: dict[str, ToolProvenance] = field(default_factory=dict)
    """`{name: provenance}` for the tools that resolved. Keyed the same as
    `shas`, and always the same set: a tool that is going to be mounted is a
    tool something eventually has to be able to describe."""

    def versions(self) -> dict[str, str]:
        """`{name: release}` for `shas` — read off `mounts()`, so the turn's
        record and a turn-less wake's are one builder, not two kept alike."""
        return {name: m.version for name, m in self.mounts().items()}

    def mounts(self) -> dict[str, MountedTool]:
        """What a sandbox created from THIS resolve mounts: each sha with the
        version the same answer gave it, so the record cannot pair a sha with
        another release's number."""
        return {
            name: MountedTool(
                sha=sha, version=p.version if (p := self.provenance.get(name)) else ""
            )
            for name, sha in self.shas.items()
        }


def _package(name: str, described: dict[str, Any]) -> PackageInfo:
    return PackageInfo(
        name=name,
        # The same sandbox-relative shape first-party packages use. The sha is
        # how the host stores the bundle; it never appears in a path the agent
        # or the model sees.
        install_dir=f"../.tools/{name}",
        third_party=True,
        commands=tuple(
            CommandInfo(
                name=c["name"],
                description=c["description"],
                params_json_schema=c["params_json_schema"],
            )
            for c in described["commands"]
        ),
        # #750. Absent stays absent: an artifact published before the
        # declaration existed says nothing, and turning that into "needs
        # nothing" here would be the same lie as inside a bundle — except
        # applied to every third-party tool at once, which is the population
        # the feature was written for.
        env_needs=(
            tuple(
                EnvNeed(
                    name=e["name"],
                    description=e.get("description", ""),
                    required=e.get("required"),
                )
                for e in described["env"]
                # A stranger's JSON, so an entry that is not an object or has
                # no string `name` cannot build an `EnvNeed` at all — dropping
                # it costs that row, where letting the `KeyError` out would
                # cost the whole resolve. TYPING the other fields is not done
                # here: that belongs once, where the response model is built
                # (`tools_routes._env_needs_of`), because the first-party
                # reader feeds the same model and would need the same rule.
                if isinstance(e, dict) and isinstance(e.get("name"), str)
            )
            if isinstance(described.get("env"), list)
            else None
        ),
        # What the tool says about ITSELF, and which release said it. The agent
        # reads a PackageInfo and never sees the provenance record (that stops
        # at the picker and a log line), so anything the model is expected to be
        # able to say about a tool has to arrive here. Absent stays absent.
        description=str(described.get("description") or ""),
        version=str(described.get("version") or ""),
        author=str(described.get("author") or ""),
    )


async def resolve_external_tools(sandbox: object, declared: Mapping[str, str]) -> ExternalTools:
    """Resolve an app's declared third-party tools for this turn."""
    if not declared:
        return ExternalTools()
    if not isinstance(sandbox, ToolResolvingSandbox) or not sandbox.resolves_tools:
        # Local/dev backends have no artifact store. Say so per tool, so the
        # absence is diagnosable instead of looking like a missing declaration.
        return ExternalTools(
            refused={name: "third-party tools need the hosted sandbox backend" for name in declared}
        )

    answer = await sandbox.resolve_tools(dict(declared))
    tools = answer.get("tools", {})
    refused = dict(answer.get("refused", {}))
    for name, reason in refused.items():
        logger.warning("tool %s unavailable this turn: %s", name, reason)
    return ExternalTools(
        packages=tuple(_package(name, described) for name, described in tools.items()),
        shas={name: described["sha"] for name, described in tools.items()},
        refused=refused,
        provenance={
            name: ToolProvenance(
                version=described["version"],
                # `.get`, not `[]`: a host that has not been redeployed yet
                # answers without this key, and an app that raised over it
                # would take every third-party tool down for the length of a
                # rolling upgrade.
                author=described.get("author"),
                stale=bool(described.get("stale")),
            )
            for name, described in tools.items()
        },
    )


def drift(external: ExternalTools, mounted: Mapping[str, MountedTool] | None) -> dict[str, Drift]:
    """THE rule for "is the live sandbox's tool the latest" — one function,
    read by the turn's confinement, the model's description and the tool
    picker, so the three cannot disagree (review round 2). Only the tools that
    differ are listed; unknown mounts (`None`) list nothing (D4). Compared by
    sha (D8)."""
    if mounted is None:
        return {}
    out: dict[str, Drift] = {}
    for name, sha in external.shas.items():
        m = mounted.get(name)
        if m is None:
            out[name] = Drift(missing=True)
        elif m.sha != sha:
            out[name] = Drift(missing=False, running=m.version)
    return out


def confine_to_mounted(
    external: ExternalTools,
    *,
    live: bool,
    mounted: Mapping[str, MountedTool] | None,
) -> ExternalTools:
    """What this turn may offer, given a sandbox that already exists.

    A sandbox mounts its bundles when it is CREATED and never again, so a tool
    that was not mounted then has no launcher inside it now. Offering it anyway
    is what `../.tools/<name>/launch: No such file or directory` is — a message
    that names neither the tool nor the reason, and reaches the model as if the
    tool itself were broken. It is refused rather than dropped, because a
    refusal carries a sentence the agent relays and a person can act on (#480);
    an absence carries nothing.

    ONLY absence. A tool mounted at a DIFFERENT sha is left alone deliberately:
    an author releasing mid-session is the documented no-op path ("they push,
    the next sandbox gets it"), and taking a working tool away for the rest of
    a live session would make routine releases hurt the people using them. The
    schemas can then describe a different release from the bundle for one
    sandbox's life — the residual of pinning at create, not something this
    function should convert into an outage; `describe_running` says so in the
    tool's description (plan-tool-running-version D2).

    `mounted=None` means UNKNOWN, not empty: no live sandbox, or one whose
    address carries no record of what went in (built before the record existed,
    plan-tool-running-version). Guessing "empty" there would take working tools
    away, so the unknown case is left exactly as resolved. A sandbox another pod
    built is no longer unknown — its address says what it mounted (D10)."""
    if not live or mounted is None or not external.shas:
        return external
    absent = [name for name, d in drift(external, mounted).items() if d.missing]
    if not absent:
        return external
    for name in absent:
        logger.info("tool %s resolved but not mounted in this item's live sandbox", name)
    return ExternalTools(
        packages=tuple(p for p in external.packages if p.name not in absent),
        shas={n: s for n, s in external.shas.items() if n not in absent},
        refused={
            **external.refused,
            **{
                name: (
                    "this tool is not installed in this workspace's current sandbox, "
                    "which was set up without it. Closing the sandbox lets the next "
                    "one be set up with it (so does the sandbox being recycled after "
                    "idling)."
                )
                for name in absent
            },
        },
        provenance={n: p for n, p in external.provenance.items() if n not in absent},
    )


async def prewarm_external_tools(
    sandbox: object,
    declared_by_app: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    """Pull every app's third-party tools into the host's cache at startup.

    Best effort, and deliberately NOT part of readiness (#674 Q17). The first
    turn to use a cold tool would otherwise pay a 150MB download, so warming is
    worth doing — but a pod that refuses to start because an artifact store is
    unreachable has turned someone else's outage into ours. That is the opposite
    of the first-party `discover_packages`, which IS fail-loud at boot, and the
    difference is deliberate: those bundles are inside our own image, so their
    absence means the image is broken.

    Returns `{name: reason}` for whatever could not be warmed, so boot logs say
    what will be missing rather than leaving it to be discovered in a turn."""
    unwarmed: dict[str, str] = {}
    for slug, declared in declared_by_app.items():
        if not declared:
            continue
        try:
            external = await resolve_external_tools(sandbox, declared)
        except Exception as exc:  # noqa: BLE001 - warming must never stop a boot
            logger.warning("tool prewarm: app %s failed entirely: %s", slug, exc)
            unwarmed.update(dict.fromkeys(declared, str(exc)))
            continue
        unwarmed.update(external.refused)
        for name in external.refused:
            logger.warning("tool prewarm: %s unavailable (%s)", name, external.refused[name])
    return unwarmed


def describe_running(
    external: ExternalTools, mounted: Mapping[str, MountedTool] | None
) -> ExternalTools:
    """Describe each tool as the release the live sandbox RUNS
    (plan-tool-running-version D2).

    A sandbox keeps the bundle it was created with while the resolve describes
    the latest release, so where the two shas differ the package's `version`
    becomes the mounted one and `latest_version` names the latest (`""` if it
    published none — nothing is invented in its place) — which is
    what `describe_command` turns into the sentence the model reads. Compared by
    sha (D8). Unknown mounts (`None`) change nothing (D4): the next sandbox is
    built from this resolve. Only the words change; what mounts does not."""
    differs = drift(external, mounted)
    packages = []
    for pkg in external.packages:
        d = differs.get(pkg.name)
        if d is None or d.missing:
            packages.append(pkg)  # same release — or absent, which confinement refuses
            continue
        packages.append(replace(pkg, version=d.running, latest_version=pkg.version))
    return replace(external, packages=tuple(packages))
