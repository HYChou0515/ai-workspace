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
from sandbox_host.tool_resolve import TOKEN_ENV, Fetcher, ToolResolver, _http_get

_USAGE = (
    "usage: mcp-runner <tool-name> <url ending in tool.manifest.json>\n"
    "\n"
    "The name is the platform's, and is checked against the certificate the "
    "artifact carries — so a URL cannot quietly start serving something else."
)

#: Where bundles are unpacked. A volume in practice — that is what makes the
#: fetch a once-per-version cost instead of a once-per-start one.
CACHE_ENV = "TOOL_CACHE_DIR"
DEFAULT_CACHE = "/cache"

#: The working directory, and therefore what a tool's relative paths mean.
#: Meant to be the engineer's project, mounted in.
WORK_DIR = "/work"


def _nothing_mounted_at(path: str) -> bool:
    """True when `path` is an ordinary directory in the container's own layer
    rather than something mounted in.

    A directory and a mount point look identical from inside; what separates
    them is the device they live on."""
    try:
        return os.stat(path).st_dev == os.stat("/").st_dev
    except OSError:
        return True


def _workspace_owner(path: str) -> tuple[int, int] | None:
    """Who owns the mounted workspace, or None if it cannot be read."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_uid, info.st_gid


def _unguarded(_root: Path) -> None:
    """The host makes an installed tool root-owned, because many sandboxes
    with different uids share one tree and none of them may rewrite a tool the
    others run. Here the cache has one consumer: the engineer who mounted it.

    So there is nothing to harden, and pretending otherwise would mean
    chowning a volume to root on a machine where we are not necessarily root.
    Keep the volume to one user, as its own permissions already imply."""


def _become(uid: int, gid: int) -> None:  # pragma: no cover - needs real root
    """Drop to the person whose workspace this is, irreversibly.

    Groups first, then gid, then uid: after `setuid` the privilege to do the
    other two is gone, and a half-applied drop would leave the tool running
    with a root group."""
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def _hand_over(entry: Path) -> None:  # pragma: no cover - replaces the process
    """Become the tool's MCP server.

    `execv`, not a subprocess: stdin and stdout are the transport, and every
    layer between the client and the tool is a layer that can buffer, mangle,
    or outlive them.

    Without the credential that fetched it. The token reads the artifact
    store; the tool has no business with it, and on the platform it never
    could — there the HOST fetches and the sandbox only ever sees extracted
    files. Here one process does both, so it has to be taken out by hand or
    every tool an engineer runs is handed their GitLab credential."""
    environment = {name: value for name, value in os.environ.items() if name != TOKEN_ENV}
    os.execve(str(entry), [str(entry)], environment)


def main(
    argv: list[str],
    *,
    fetch: Fetcher = _http_get,
    become: Callable[[int, int], None] = _become,
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
        # Confirm it today, or do not run it — see `serve_last_known_good`.
        # This is the only place a tool's access can be withdrawn from
        # someone running it on their own machine.
        serve_last_known_good=False,
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
    #
    # No "stale" case to report: this resolver never serves a version it could
    # not confirm today, so reaching here means the artifact store answered.
    print(f"{resolved.name} {resolved.version} [{resolved.sha[:12]}]", file=sys.stderr)

    # Said once, at startup, because the failure it warns about is silent.
    # A tool reading a missing file fails loudly; a tool WRITING one succeeds,
    # reports the path, and the file leaves with the container — so the agent
    # tells someone their file is ready and their disk disagrees.
    if _nothing_mounted_at(WORK_DIR):
        print(
            f"warning: nothing is mounted at {WORK_DIR}, so files this tool "
            f'writes are lost when it exits. Add -v "$PWD:{WORK_DIR}" to work '
            "on your own files.",
            file=sys.stderr,
        )
    else:
        # Become whoever owns the mounted workspace, so the files a tool
        # produces belong to the person who asked for them.
        #
        # Done here rather than asked for as `--user "$(id -u):$(id -g)"`: an
        # MCP config expands environment variables at best, never command
        # substitution, so that flag would have to be hand-edited with a uid
        # on every machine — and whoever forgot would find root-owned files in
        # their own project.
        #
        # Keyed on the DIRECTORY's owner, not on being root. Under rootless
        # docker this process is uid 0 and the directory is too, because the
        # person outside maps to root inside; files already land owned by them
        # and there is nothing to drop.
        owner = _workspace_owner(WORK_DIR)
        if owner is not None and os.geteuid() == 0 and owner[0] != 0:
            become(*owner)

    hand_over(cache.path_for(resolved.sha) / "mcp")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via `main`
    raise SystemExit(main(sys.argv[1:]))
