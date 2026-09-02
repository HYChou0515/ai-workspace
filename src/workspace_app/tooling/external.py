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
from dataclasses import dataclass, field
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


def _package(name: str, described: dict[str, Any]) -> PackageInfo:
    return PackageInfo(
        name=name,
        # The same sandbox-relative shape first-party packages use. The sha is
        # how the host stores the bundle; it never appears in a path the agent
        # or the model sees.
        install_dir=f"../.tools/{name}",
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


def confine_to_mounted(
    external: ExternalTools,
    *,
    live: bool,
    mounted: dict[str, str] | None,
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
    schemas can then be a release ahead of the bundle for one session's life —
    the residual of pinning at create, not something this function should
    convert into an outage.

    `mounted=None` means UNKNOWN, not empty: another pod created this sandbox
    (#366) and this one never learned what went into it. Guessing "empty" there
    would take working tools away from every multi-pod deployment, so the
    unknown case is left exactly as resolved."""
    if not live or mounted is None or not external.shas:
        return external
    absent = [name for name in external.shas if name not in mounted]
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
                    "this workspace was started before this tool was available, so "
                    "it is not installed here. It works in a new workspace, or in "
                    "this one once it has been idle long enough to be recycled."
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
