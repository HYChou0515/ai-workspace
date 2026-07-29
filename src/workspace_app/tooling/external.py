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

from .registry import CommandInfo, PackageInfo

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolResolvingSandbox(Protocol):
    """A backend with an artifact store behind it. Only the hosted sandbox
    has one; local/mock backends answer `False` and third-party tools are
    reported as unavailable rather than silently missing."""

    resolves_tools: bool

    async def resolve_tools(self, declared: Mapping[str, str]) -> dict[str, Any]: ...


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
    stale: tuple[str, ...] = ()
    """Tools served from the host's last-known-good copy because the artifact
    store was unreachable. Usable, but not necessarily the latest."""


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
        stale=tuple(name for name, described in tools.items() if described.get("stale")),
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
