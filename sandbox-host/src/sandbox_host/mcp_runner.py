"""Run any published tool as an MCP server, from its artifact URL (#674).

    docker run --rm -i -v mcp-tools:/cache -v "$PWD:/work" \\
        -e TOOL_ARTIFACT_TOKEN registry/ai-workspace/mcp-runner:<tag> \\
        wafer-history https://gitlab.example/.../tool.manifest.json

One image, every tool. The bundle arrives the same way it arrives on the
platform — fetched from its artifact URL, gated on the builder that produced
it, checked against the sha its manifest publishes, unpacked into a cache
keyed by that sha — and then this process becomes the tool's own MCP server.

**Why not an image per tool.** A bundle already exists once, in the artifact
store; copying it into a container image stores it a second time, per tool and
per version. Worse, an image with the bundle baked in has nothing left to
verify at run time: whatever was copied in is what runs. Here the same
`ToolResolver` the host uses applies the same gates, so an engineer's copy and
the platform's copy are the same bytes, checked the same way — and a new
publish is picked up on the next start rather than needing a fresh image.

The cost is a fetch on first use. The cache is content-addressed and meant to
live on a volume, so it is paid once per tool version rather than per start.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable
from pathlib import Path

from sandbox_host.artifact import ArtifactError
from sandbox_host.tool_cache import ToolCache
from sandbox_host.tool_resolve import Fetcher, ToolResolver, _http_get

_USAGE = (
    "usage: mcp-runner <tool-name> <url ending in tool.manifest.json>\n"
    "\n"
    "The name is the one the tool publishes; it is checked against the "
    "manifest so a URL cannot quietly start serving something else."
)

#: Where bundles are unpacked. A volume in practice — that is what makes the
#: fetch a once-per-version cost instead of a once-per-start one.
CACHE_ENV = "TOOL_CACHE_DIR"
DEFAULT_CACHE = "/cache"


def _unguarded(_root: Path) -> None:
    """The host makes an installed tool root-owned, because many sandboxes
    with different uids share one tree and none of them may rewrite a tool the
    others run. Here the cache has one consumer: the engineer who mounted it.

    So there is nothing to harden, and pretending otherwise would mean
    chowning a volume to root on a machine where we are not necessarily root.
    Keep the volume to one user, as its own permissions already imply."""


def _hand_over(entry: Path) -> None:  # pragma: no cover - replaces the process
    """Become the tool's MCP server.

    `execv`, not a subprocess: stdin and stdout are the transport, and every
    layer between the client and the tool is a layer that can buffer, mangle,
    or outlive them."""
    os.execv(str(entry), [str(entry)])


def main(
    argv: list[str],
    *,
    fetch: Fetcher = _http_get,
    hand_over: Callable[[Path], None] = _hand_over,
) -> int:
    if len(argv) != 2:
        print(_USAGE, file=sys.stderr)
        return 2
    name, manifest_url = argv

    # Baked into the image beside the code that enforces it, exactly as on the
    # host: a bundle built against another base carries an interpreter that
    # will not load here, and the failure would be a segfault.
    builder = os.environ.get("TOOL_BUILDER_ID")
    if not builder:
        print(
            "TOOL_BUILDER_ID is not set — without it there is nothing to check "
            "a bundle's ABI against, and an incompatible one crashes instead of "
            "being refused",
            file=sys.stderr,
        )
        return 2

    root = Path(os.environ.get(CACHE_ENV, DEFAULT_CACHE))
    root.mkdir(parents=True, exist_ok=True)
    cache = ToolCache(root, harden=_unguarded)
    resolver = ToolResolver(
        cache,
        builder_id=builder,
        arch=platform.machine(),
        fetch=fetch,
        state_dir=root,
    )

    try:
        resolved = resolver.resolve(name, manifest_url)
    except ArtifactError as exc:
        # Everything the resolver refuses arrives here already phrased for a
        # person: which gate, and why. A traceback would bury that.
        print(f"cannot run {name}: {exc}", file=sys.stderr)
        return 1

    # stderr, always: stdout is the JSON-RPC channel, and the client reads the
    # first line it sees there as a message.
    #
    # Say only what is known. An earlier version claimed "from cache" every
    # time, including on the run that had just downloaded — a line that is
    # right half the time is worse than no line, because it gets believed.
    note = (
        " — the artifact store was unreachable, so this is the last version that resolved"
        if resolved.stale
        else ""
    )
    print(f"{resolved.name} {resolved.version} [{resolved.sha[:12]}]{note}", file=sys.stderr)

    hand_over(cache.path_for(resolved.sha) / "mcp")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via `main`
    raise SystemExit(main(sys.argv[1:]))
