"""LocalProcessSandbox — runs commands as subprocesses on the host.

The host-side base backend. `IsolatedProcessSandbox` subclasses it to add
uid/gid + cgroup isolation. When unprivileged user namespaces are available,
`exec` runs each command inside a user+mount namespace chrooted onto the
sandbox directory, so that:

  * the user **workspace is `/root`** — the agent's cwd. File ops + `walk` are
    scoped here. `$HOME` (`~`) is a SEPARATE dir, `/.home`, in the infra area —
    so a tool's profile/cache (e.g. LibreOffice's user installation) never
    pollutes or locks on the synced workspace. The sandbox root (the chroot `/`)
    is the **infra area**: system overlays + provisioned tools + `.home` live
    there, OUTSIDE the workspace, so they're never walked, synced, or in the tree.
  * the host filesystem is not reachable, and system dirs (`/usr`, `/etc`)
    are bind-mounted read-only so the agent can't tamper with the host.

Where user namespaces are unavailable it transparently falls back to a plain
subprocess in the workspace subdir (no isolation) — set `isolate=False` to
force this. (Standalone copy — see `protocol.py` for why the host shares no
modules with `workspace_app`.)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from .protocol import (
    EnforcedLimits,
    ExecResult,
    FileEntry,
    OutputSink,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
    WalkResult,
)
from .tool_cache import BUILTIN_DIR, EXT_DIR

# Bootstrap run (as namespace-root) before chroot: overlay the host's system
# dirs read-only onto the sandbox root, wire up a usable /dev + ephemeral
# /tmp, then chroot in and exec the user command. $1 is the jail root; the
# remaining args are the command. Device nodes are bind-mounted onto plain
# files (an unprivileged tmpfs is `nodev`, so nodes there can't be opened);
# the resulting /dev files are cleaned up by `exec` afterwards.
logger = logging.getLogger(__name__)

_JAIL_BOOTSTRAP = r"""
ROOT="$1"; shift
mkdir -p "$ROOT/usr" "$ROOT/proc" "$ROOT/dev" "$ROOT/etc" "$ROOT/tmp" "$ROOT/root" "$ROOT/.home"
mount --bind /usr "$ROOT/usr"; mount -o remount,bind,ro "$ROOT/usr"
mount --bind /etc "$ROOT/etc"; mount -o remount,bind,ro "$ROOT/etc"
# Provisioned tools (#674). The host has already assembled a VIEW — one
# symlink per tool, named as this sandbox should see it, mixing first-party
# tools with whichever third-party bundles this app declared. Each link
# becomes its own read-only bind mount, so the sandbox sees the tools and
# never the layout (no `builtin/`, no shas, no bundles it was not granted).
# /.tools is a sibling of /root: outside the workspace, never walked/synced.
if [ -d "$SANDBOX_TOOLS_VIEW" ]; then
  mkdir -p "$ROOT/.tools"
  mount -t tmpfs tmpfs "$ROOT/.tools"
  for l in "$SANDBOX_TOOLS_VIEW"/*; do
    [ -e "$l" ] || continue
    n=$(basename "$l"); t=$(readlink -f "$l")
    mkdir -p "$ROOT/.tools/$n"
    mount --bind "$t" "$ROOT/.tools/$n"; mount -o remount,bind,ro "$ROOT/.tools/$n"
  done
  # Seal it. A writable /.tools would let the sandbox plant its own
  # `python-stack/launch` and capture every `python` the agent runs after.
  # The type and source must be repeated: inside a user namespace
  # util-linux cannot infer them, and a bare `-o remount,ro` fails with
  # "mount point not mounted or bad option".
  mount -o remount,ro -t tmpfs tmpfs "$ROOT/.tools"
fi
for l in bin sbin lib lib64; do
  [ -L "$ROOT/$l" ] || [ -e "$ROOT/$l" ] || ln -s "usr/$l" "$ROOT/$l"
done
mount -t proc proc "$ROOT/proc" 2>/dev/null || true
mount -t tmpfs tmpfs "$ROOT/tmp" 2>/dev/null || true
for d in null zero full random urandom tty; do
  if [ -e "/dev/$d" ]; then : > "$ROOT/dev/$d"; mount --bind "/dev/$d" "$ROOT/dev/$d"; fi
done
# `python` shim selection. Three-tier (the first was added by #775;
# it is the one an earlier pass here forgot, which is why the count
# is spelled out rather than left to be inferred from the branches):
#   0. The WORKSPACE's own venv when `uv sync` built one (see below).
#   1. If the `python-stack` venv carrier was provisioned (its prebuilt
#      bundle bind-mounted at /.tools/python-stack with the data-science
#      stack inside .venv/), prefer its launcher — the agent's raw
#      `exec(["python", "script.py"])` then sees pandas / numpy / scipy /
#      matplotlib for free, matching the SOP's "sandbox preinstalled
#      data stack" promise without depending on the host's site-packages.
#   2. Otherwise fall back to /usr/bin/python3 from the bind-mounted /usr.
#      (A Debian host's /usr/bin/python is often the legacy python2
#      symlink, so we always shim explicitly rather than inherit it.)
# The shim lives on the ephemeral tmpfs so it never touches the workspace.
mkdir -p "$ROOT/tmp/.jailbin"
# Shim BOTH `python` and `python3` (and the major-minor flavour names the
# tools' bundled launchers might use): agents commonly type `python3 -` in
# heredocs, and a bare `python` shim alone would let `python3` fall through
# to /usr/bin/python3 — the host Python with no pandas/numpy/scipy/matplotlib.
# Tier 1 (#775): the WORKSPACE's own venv, when the profile declared its
# dependencies and `uv sync` built one. A profile that says what it needs gets
# exactly that; the carrier below is the fallback for profiles that said
# nothing, never a layer underneath one that spoke.
#
# A WRAPPER, not a symlink — the same reason as the unjailed shim. CPython
# resolves its own executable path to find `pyvenv.cfg`, so a link from outside
# the venv resolves straight past it to the base interpreter and `python` runs
# with none of the packages just installed. No `pip*`: a uv venv ships none, and
# a shim that cannot work is worse than none.
# The same cycle the unjailed shim guards against, reachable the same way: this
# bootstrap puts /tmp/.jailbin FIRST on PATH, `uv sync` picks its base
# interpreter off PATH, so the venv can be built on the shim — and /tmp is a
# FRESH tmpfs every exec, so next time the shim is rebuilt as tier 1 pointing
# into that venv and `python` execs itself forever, silently.
#
# Shell cannot walk a symlink chain hop by hop the way the unjailed guard does
# (`readlink -f` resolves the whole thing and hides the hop that matters), so
# this reads the venv's own record of the interpreter it was built on. `home =`
# is exactly that, and uv writes it.
_venv_ok=""
if [ -x "$ROOT/.venv/bin/python" ]; then
  _venv_ok=yes
  _cfg="$ROOT/.venv/pyvenv.cfg"
  if [ -f "$_cfg" ] && grep -q '^home = /tmp/\.jailbin' "$_cfg"; then
    _venv_ok=""
  fi
fi
if [ -n "$_venv_ok" ]; then
  # `uv pip install` and friends read VIRTUAL_ENV, not UV_PROJECT_ENVIRONMENT.
  export VIRTUAL_ENV=/.venv
  for n in python python3 python3.10 python3.11 python3.12 python3.13; do
    # `rm -f` FIRST. `>` writes THROUGH a symlink, and the other two tiers use
    # `ln -sf`, which unlinks. `/tmp` here is a fresh tmpfs per exec — except
    # that its mount is the one line in this bootstrap allowed to fail quietly
    # (`|| true`), and when it does, a previous exec's `python -> /usr/bin/python3`
    # is still sitting there. The redirect then resolves through it and rewrites
    # the HOST's interpreter, as root: `--map-root-user` maps root to root.
    rm -f "$ROOT/tmp/.jailbin/$n"
    printf '#!/bin/sh\nexec /.venv/bin/python "$@"\n' > "$ROOT/tmp/.jailbin/$n"
    chmod 755 "$ROOT/tmp/.jailbin/$n"
  done
elif [ -x "$ROOT/.tools/python-stack/launch" ]; then
  # `pip*` too: the launcher dispatches on the name it is invoked as, so these
  # are the same symlink and `pip install X` installs into the very interpreter
  # `python` runs. Carrier branch only — the /usr/bin/python3 fallback below
  # cannot answer to `pip`.
  for n in python python3 python3.10 python3.11 python3.12 python3.13 \
           pip pip3 pip3.10 pip3.11 pip3.12 pip3.13; do
    ln -sf /.tools/python-stack/launch "$ROOT/tmp/.jailbin/$n"
  done
elif [ -e /usr/bin/python3 ]; then
  ln -sf /usr/bin/python3 "$ROOT/tmp/.jailbin/python"
  ln -sf /usr/bin/python3 "$ROOT/tmp/.jailbin/python3"
fi
export PATH="/tmp/.jailbin:/usr/bin:/bin:/usr/sbin:/sbin"
# Named so a command that must NOT see the shim can take it off the front —
# `uv sync` does, or uv builds the project venv on the shim itself. The
# unjailed path exports the same variable for the same reason.
export SANDBOX_JAILBIN=/tmp/.jailbin
# IMPORTANT — login-shell PATH guard. The agent commonly runs commands as
# `bash -lc "python3 -c …"`; the `-l` makes bash source /etc/profile, which
# on Debian/Ubuntu hard-resets PATH to "/usr/local/sbin:/usr/local/bin:..."
# and silently drops our /tmp/.jailbin first-on-PATH shim. The result was
# ModuleNotFoundError on pandas because python3 then resolved to the host's
# /usr/bin/python3 instead of our python-stack launcher. Overlay a tmpfs on
# /etc/profile.d and drop a single script that re-prepends /tmp/.jailbin
# so login shells inherit the shim. /etc is bind-mounted read-only from the
# host, so we can't write into the real /etc/profile.d — the tmpfs overlay
# is the workaround. Host's profile.d scripts are shadowed inside the jail,
# which is fine (we don't need ssh-agent/locale-config setup in a sandbox).
mount -t tmpfs tmpfs "$ROOT/etc/profile.d" 2>/dev/null || true
cat > "$ROOT/etc/profile.d/jailbin.sh" <<'PROFILED'
PATH="/tmp/.jailbin:$PATH"
export PATH
PROFILED
chmod 644 "$ROOT/etc/profile.d/jailbin.sh"
# cwd is the workspace (/root); HOME is /.home, a workspace sibling in the infra
# area (never walked/synced, reaped with the sandbox) — so a tool that writes its
# profile to $HOME (LibreOffice's user installation) doesn't pollute or lock on
# the synced workspace. Mirrors the unjailed branch's HOME=<root>/.home.
exec /usr/sbin/chroot "$ROOT" /bin/sh -ec 'cd /root; export HOME=/.home; exec "$@"' sh "$@"
"""


def _dir_size(path: Path) -> int:
    """Bytes under `path`, symlinks never followed (a cache is uv's own tree,
    but a size walk that follows links can be aimed anywhere)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            f = Path(dirpath) / name
            if not f.is_symlink():
                with contextlib.suppress(OSError):
                    total += f.stat().st_size
    return total


def _jail_argv(root: str, cmd: list[str]) -> list[str]:
    """Wrap `cmd` so it runs inside an unprivileged user+mount namespace
    chrooted onto `root`. `--kill-child` makes a SIGKILL of the wrapper tear
    down the jailed process too (so exec timeouts still reap it)."""
    return [
        "unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--fork",
        "--kill-child",
        "/bin/sh",
        "-ec",
        _JAIL_BOOTSTRAP,
        "sh",
        root,
        *cmd,
    ]


@cache
def _userns_supported() -> bool:
    """True if this host allows unprivileged user namespaces (so the jail
    can be built without root). Cached — the answer can't change at runtime."""
    try:
        proc = subprocess.run(
            ["unshare", "--user", "--map-root-user", "true"],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# The user workspace is this subdir of the sandbox root (the agent's ~/cwd).
# MUST match the `/root` the jail bootstrap cds into.
_WORKSPACE = "root"
# Provisioned tools are made available here (a sibling of the workspace, so
# they're outside what walk/sync see). MUST match the jail bootstrap's mount.
_TOOLS = ".tools"
# #674: the per-sandbox tool VIEW — one symlink per tool, named as the
# sandbox will see it. It is the single source of truth for both modes:
# unjailed points `.tools` at it, and the jail bootstrap turns each link
# into a read-only bind mount. Also in the infra area, so never walked.
_TOOLS_VIEW = ".tools-view"
# #366: readiness marker written (via mark_ready) once a restore completes; the
# app's mirror only propagates DELETIONS while `is_ready` holds. It lives at the
# SANDBOX ROOT — a sibling of the workspace, OUTSIDE it — so walk/sync/the file
# tree never see it and a user can't fake it with a workspace file. Teardown must
# unlink it FIRST (before rmtree) so a racing mirror sees an incomplete sandbox
# and never wipes the durable snapshot.
_READY_MARKER = ".ready"
# Unjailed `python` shim dir (#350). The jail bootstrap (isolate=True) routes
# raw `python`/`python3*` to the python-stack carrier from inside the chroot;
# unjailed pods — the model our deployments actually run (uid + cgroup, no
# userns) — never execute that bootstrap, so without this `python` resolved via
# the inherited PATH to the host's OWN service venv (fastapi/uvicorn/pydantic),
# not the carrier. We materialise the same shim as a real bin dir (a sibling of
# the workspace, so walk/sync never see it) and prepend it to PATH in
# `_exec_argv`. MUST stay outside the workspace.
_JAILBIN = ".jailbin"
# #393: per-sandbox HOME for the carrier launcher (caches + any `pip --user`
# install fallback). A sibling of the workspace, OUTSIDE it — so walk/sync never
# see it and it is reaped with the sandbox. The unjailed `_exec_argv` passes it
# as SANDBOX_HOME; this replaces the launcher's old shared-/tmp HOME that leaked
# a user's `pip install --break-system-packages` across sandboxes on a pod.
_HOME = ".home"
#: uv's download cache, keyed by the ITEM and living beside the sandbox dirs so
#: it outlives any one of them — a cold start then re-uses what this item
#: already downloaded instead of re-fetching its whole stack.
#:
#: The key is load-bearing, and on THIS backend it is the one that had to be
#: fixed: uids here come from a pool and are freed on kill, so a uid-keyed
#: cache means "whoever holds that uid now" and hands the next tenant whatever
#: the last one left there — poisoned or not, since uv verifies a wheel's hash
#: on DOWNLOAD and then trusts its own unpacked archive. An item id is never
#: recycled. What bounds the collection is the sweeper, not the key.
_UV_CACHE = ".uv-cache"


# #775: where `uv sync` builds the workspace's own environment. A sibling of
# the workspace, like `.home` and `.jailbin` — outside it, so walk/sync never
# see it and the quota never charges for a directory the user cannot delete
# and we throw away with the sandbox anyway.
_PROJECT_VENV = ".venv"
# Shim every flavour name the agent or a tool launcher might spell — matching
# the jail bootstrap. A bare `python` shim alone would let `python3` fall
# through to the host interpreter.
_PYTHON_SHIM_NAMES = ("python", "python3", "python3.10", "python3.11", "python3.12", "python3.13")
# `pip` rides the SAME launcher (it dispatches on the name it was invoked as), so
# `pip install X` installs into the very interpreter `python` runs. Unshimmed, it
# fell through to the image's own pip: a different interpreter AND a different
# HOME, so the install landed where the carrier never looks and the import failed
# with nothing explaining why. Carrier-only, deliberately — the no-carrier
# fallback is /usr/bin/python3, and a `pip` pointing there would run
# `python3 install X`, which is not a command; better to let the image's real pip
# answer than to shim something that cannot work.
_PIP_SHIM_NAMES = ("pip", "pip3", "pip3.10", "pip3.11", "pip3.12", "pip3.13")


#: How far to follow a symlink chain before calling it a loop. Linux gives up at
#: 40 (ELOOP); matching it means we never call "fine" something the kernel would
#: refuse to run.
_MAX_LINK_HOPS = 40


def _usable_project_python(project: Path) -> bool:
    """Is the workspace venv's interpreter something the shim may point at?

    Executable, and NOT resolving back into the shim dir. A venv built on top of
    the shim makes `python` exec ITSELF — wrapper -> venv/bin/python -> the same
    wrapper — forever, with no output and no exit until something kills it. That
    is reachable rather than theoretical: `uv sync` picks its base interpreter
    off PATH and `_exec_argv` puts `.jailbin` first on PATH, so uv can build the
    venv on the very shim that is about to point into it.

    Falling back to the carrier is the right answer: a sandbox whose project
    interpreter cannot be used should get the one a profile that declared
    nothing would have had, not a `python` that hangs.
    """
    if not os.access(project, os.X_OK):
        return False
    # Every HOP, not just the destination. `os.path.realpath` follows the whole
    # chain, so a venv python that links to the shim which links to the carrier
    # resolves to the carrier and looks innocent — while executing it still goes
    # through the shim, which is the loop. Walk the links instead.
    seen = project
    for _ in range(_MAX_LINK_HOPS):
        if _JAILBIN in seen.parts:
            return False
        if not seen.is_symlink():
            return True
        target = Path(os.readlink(seen))
        # Relative to the LINK'S OWN directory, not to where the walk started.
        # Anchoring every hop at `project.parent` invents a cycle inside the
        # venv's own `bin/`: a real `python3 -> python3.10` link elsewhere gets
        # re-rooted to `<venv>/bin/python3.10`, which links back to
        # `<venv>/bin/python`, and a perfectly good venv is refused.
        seen = target if target.is_absolute() else seen.parent / target
    return False  # a chain this long is itself a loop


def _shim_is_current(link: Path, *, want: str, script: str | None) -> bool:
    """Is this shim entry already exactly what we would write?

    Cheap, and it makes the per-exec rewrite race-free on reruns. A shim of the
    OTHER shape is never current — that is what rewrites a sandbox which has
    just gained (or lost) its project venv, rather than leaving a stale entry
    pointing at an interpreter nobody wants."""
    if script is None:
        return link.is_symlink() and os.readlink(link) == want
    if link.is_symlink():
        return False
    try:
        return link.read_text() == script
    except OSError:
        return False


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the command's whole process group, then leave reaping to the
    caller. `start_new_session=True` at spawn made the child a group leader, so
    its pid IS the pgid — killing the group takes down any backgrounded
    grandchildren too. A `ProcessLookupError` just means the group already
    exited between wait and kill — nothing left to do."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


class LocalProcessSandbox:
    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        exec_timeout: float = 60.0,
        log_timeout: float = 60.0,
        isolate: bool | None = None,
        tools_dir: Path | None = None,
    ) -> None:
        self._root = root_dir or Path(tempfile.gettempdir()) / "sandbox-host"
        self._root.mkdir(parents=True, exist_ok=True)
        # Shared, prebuilt provisioned-tools dir, made available at /.tools
        # (outside the workspace): read-only bind-mount when jailed, symlink when
        # not. One shared dir for all sandboxes — no per-sandbox copy.
        self._tools_dir = tools_dir
        # #674: `tools_dir` is the LAYOUT ROOT, not a directory of tools —
        # `builtin/` beside `ext/`. What a sandbox is shown is the tools
        # themselves, so every mount below points INSIDE the layout.
        self._builtin_tools = None if tools_dir is None else tools_dir / BUILTIN_DIR
        self._dirs: dict[str, Path] = {}
        # #775: which ITEM each handle serves. The handle is a per-pod uuid, so
        # this is the only never-recycled name a persistent cache can be keyed by
        # (uids here come from a pool and are freed on kill).
        self._item_of: dict[str, str] = {}
        # Two peer timeouts, each a hard cap; 0 disables that one:
        #   exec_timeout — TOTAL wall-clock for the command (the original cap).
        #   log_timeout  — IDLE cap: kill if no stdout/stderr output for this
        #                  long (a long job that hangs stops emitting logs). A
        #                  long job sets exec_timeout=0 and relies on log_timeout.
        self._exec_timeout = exec_timeout
        self._log_timeout = log_timeout
        # Isolation: None → on iff the host supports unprivileged user
        # namespaces; otherwise honour the explicit choice.
        self._isolate = _userns_supported() if isolate is None else isolate

    def _require(self, handle: SandboxHandle) -> Path:
        """The sandbox root — the chroot root / infra area (system overlays,
        provisioned tools). The user workspace is the `_workspace` subdir."""
        path = self._dirs.get(handle.id)
        if path is None:
            raise SandboxNotFound(handle.id)
        return path

    def _workspace(self, handle: SandboxHandle) -> Path:
        """The user workspace — a subdir of the sandbox root (the agent's
        `~`/cwd). File ops + walk are scoped here, so tools/caches living in the
        sandbox root (the infra area, outside this) are never seen or synced."""
        return self._require(handle) / _WORKSPACE

    def workspace_dir(self, handle: SandboxHandle) -> Path:
        """#492: the local working dir the host rsyncs to/from the NFS archive —
        the SAME subdir `walk`/file ops are scoped to (the agent's cwd), so the
        archive mirrors exactly the user's files and nothing in the infra area."""
        return self._workspace(handle)

    async def effective_limits(self, spec: SandboxSpec) -> EnforcedLimits:
        """This backend applies no cgroup, so it caps nothing of its own: the
        answer is exactly what was asked for. A subclass that DOES cap (the
        isolated backend) overrides this. Charging its owner for a ceiling
        nobody enforces would be inventing a number."""
        return EnforcedLimits(cpu_cores=spec.cpu_cores, memory_bytes=spec.memory_bytes)

    async def create(self, spec: SandboxSpec, item_id: str | None = None) -> SandboxHandle:
        handle = SandboxHandle(id=str(uuid.uuid4()))
        path = self._root / handle.id
        # Create the workspace subdir (and its parent, the sandbox/infra root).
        (path / _WORKSPACE).mkdir(parents=True, exist_ok=False)
        # #393: the per-sandbox HOME dir (a workspace sibling, in the infra area).
        # IsolatedProcessSandbox._provision chowns it to the sandbox uid.
        (path / _HOME).mkdir(exist_ok=True)
        if self._tools_dir is not None:
            self._build_tools_view(path, spec)
            # Unjailed: point `.tools` straight at the view (no chroot, so the
            # links' absolute targets resolve). Jailed builds its own mounts
            # from the same view, per exec, in the bootstrap.
            if not self._isolate:
                (path / _TOOLS).symlink_to(path / _TOOLS_VIEW)
        self._dirs[handle.id] = path
        if item_id is not None:
            self._item_of[handle.id] = item_id
        return handle

    def _build_tools_view(self, path: Path, spec: SandboxSpec) -> None:
        """Assemble what THIS sandbox may see, as one symlink per tool.

        First-party tools come from the image; third-party ones are named by
        the deployment and stored by sha, so the link is where those two facts
        meet — `<name> -> ext/<sha>`. Nothing downstream ever sees a sha: not
        the mount, not the launcher, not a path in a prompt.

        A declared third-party tool wins a name clash with a first-party one.
        Registering that name was a deliberate act by an operator; shipping a
        tool with it was ours, and the operator is the later authority."""
        assert self._tools_dir is not None
        view = path / _TOOLS_VIEW
        view.mkdir(exist_ok=True)
        builtin = self._tools_dir / BUILTIN_DIR
        if builtin.is_dir():
            for tool in sorted(builtin.iterdir()):
                if tool.is_dir():
                    (view / tool.name).symlink_to(tool)
        for name, sha in (spec.tools or {}).items():
            link = view / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(self._tools_dir / EXT_DIR / sha)

    def tools_in_use(self) -> set[str]:
        """Which third-party bundles the live sandboxes have mounted.

        Read from the views themselves rather than from a counter kept beside
        them: a counter drifts the moment a sandbox dies in a way nobody
        recorded, and the consequence of drifting the wrong way here is
        deleting a bundle out from under a running turn."""
        if self._tools_dir is None:
            return set()
        ext = (self._tools_dir / EXT_DIR).resolve()
        in_use: set[str] = set()
        for path in self._dirs.values():
            view = path / _TOOLS_VIEW
            if not view.is_dir():
                continue
            for link in view.iterdir():
                target = link.resolve()
                if target.parent == ext:
                    in_use.add(target.name)
        return in_use

    def _install_python_shim(self, root: Path) -> bool:
        """Unjailed analogue of the jail bootstrap's three-tier `python` shim
        (#350), rebuilt per-exec like the bootstrap is. Build a `.jailbin` dir
        of `python`/`python3*` symlinks that route to the python-stack carrier's
        launcher when present, else to `/usr/bin/python3` — never the host's own
        service venv that heads the inherited PATH. `_exec_argv` prepends this
        dir to PATH.

        Checks the IN-SANDBOX `<root>/.tools/python-stack/launch`, not the
        constructor's `tools_dir`, so it sees the carrier however it arrived: a
        whole-dir `.tools` symlink (tools_dir) OR a per-package `provision_tools`
        extract that lands after `create`. A plain symlink suffices: the carrier
        launch does `readlink -f "$0"`, resolving the chain to the real bundle."""
        carrier = root / _TOOLS / "python-stack" / "launch"
        # Three tiers, most specific first:
        #   1. The WORKSPACE's own venv (#775), when it declared dependencies
        #      and `uv sync` built one. A profile that says what it needs gets
        #      exactly that — the carrier is the fallback for profiles that
        #      said nothing, never a layer underneath one that spoke.
        #   2. The `python-stack` carrier, so a raw `exec(["python", …])` sees
        #      the bundled data-science stack for free.
        #   3. The system python3 — anything but the host's own service venv,
        #      which heads the inherited PATH.
        # Checked per exec rather than once at create: the venv appears AFTER
        # the sandbox exists (ensure_sandbox syncs it), and a sandbox outlives
        # many commands.
        project = root / _PROJECT_VENV / "bin" / "python"
        has_carrier = os.access(carrier, os.X_OK)
        if _usable_project_python(project):
            target = project
            from_venv = True
            # A uv venv ships NO pip — measured: its `bin/` holds only
            # `python`, `python3`, `python3.N` and the activate scripts. So
            # there is nothing correct to point `pip` at, and the shims below
            # are removed rather than aimed at an interpreter that would answer
            # `python install X`. The route for a declared workspace is
            # `uv add`, which the sandbox prompt says.
            has_carrier = False
        else:
            target = carrier if has_carrier else Path("/usr/bin/python3")
            from_venv = False
        want = os.fspath(target)
        # A SYMLINK is right for the carrier: its launcher does `readlink -f
        # "$0"` to find its own bundle and dispatches on the name it was invoked
        # as, so the link is load-bearing. It is WRONG for a venv. CPython
        # resolves its own executable path to locate `pyvenv.cfg`, and a link
        # from outside the venv resolves straight PAST it to the base
        # interpreter — so `python` came up with none of the packages `uv sync`
        # had just installed, which is #581 ("installed into A, running in B")
        # arriving through a new door, and silently. A one-line `exec` wrapper
        # keeps argv[0] inside the venv, so `sys.prefix` — and `sys.executable`,
        # which the agent can read — name the venv. Both shapes were measured
        # against a real `uv sync`; `tests/sandbox/test_project_env_e2e.py` in
        # the app repo is that measurement kept.
        script = f'#!/bin/sh\nexec {shlex.quote(want)} "$@"\n' if from_venv else None
        jailbin = root / _JAILBIN
        jailbin.mkdir(exist_ok=True)
        if not has_carrier:
            # A carrier that went away takes its pip shims with it. Leaving them
            # behind would point `pip` at a path that no longer exists — ENOENT,
            # rather than falling through to the image's own pip. That is exactly
            # the "a shim that cannot work is worse than none" case above, just
            # arrived at by a different route.
            for name in _PIP_SHIM_NAMES:
                (jailbin / name).unlink(missing_ok=True)
        for name in _PYTHON_SHIM_NAMES + (_PIP_SHIM_NAMES if has_carrier else ()):
            link = jailbin / name
            if _shim_is_current(link, want=want, script=script):
                continue
            # Atomic swap: create a temp entry under a name nobody else can
            # pick, then rename it over `link`. `os.replace` is atomic, so
            # there is never a window with no `python`, and it replaces an entry
            # of the other SHAPE too — so gaining or losing a venv rewrites the
            # shim instead of leaving it stale.
            #
            # The suffix used to be `os.getpid()`, which is NOT unique here: the
            # #345 dir is shared between pods, and two containers routinely hold
            # the same pid. Measured on one shared dir, ~0.3-0.5% of concurrent
            # calls then raised FileExistsError or FileNotFoundError straight
            # out of `_exec_argv`, failing the agent's command. A random suffix
            # is what makes the "uniquely-named" half of that sentence true.
            tmp = jailbin / f".{name}.{uuid.uuid4().hex}.tmp"
            tmp.unlink(missing_ok=True)
            if script is None:
                tmp.symlink_to(target)
            else:
                tmp.write_text(script)
                tmp.chmod(0o755)
            os.replace(tmp, link)
        return from_venv

    async def kill(self, handle: SandboxHandle) -> None:
        path = self._require(handle)
        # #775: hand this item's uv cache back before the sandbox goes. It
        # outlives the sandbox on purpose, but it carries that sandbox's uid —
        # and where uids are pooled, the next tenant of that uid would be able
        # to read and rewrite it.
        self._release_cache(handle)
        # #366: unlink the `.ready` marker FIRST — rmtree's order is arbitrary, so
        # relying on it to remove `.ready` before the files would leave a window
        # where a racing mirror sees "ready + files half-gone" and wrongly
        # propagates the deletions, wiping the durable snapshot. The marker sits at
        # the sandbox root (outside the workspace).
        await asyncio.to_thread((path / _READY_MARKER).unlink, missing_ok=True)
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
        del self._dirs[handle.id]
        self._item_of.pop(handle.id, None)

    async def mark_ready(self, handle: SandboxHandle) -> None:
        """#366: mark the sandbox authoritative once its restore completed. The
        marker is an empty file at the sandbox ROOT (`$ROOT/id/.ready`), a sibling
        of the workspace — so it is never walked/synced nor visible in the file
        tree, and no user file can forge it."""
        marker = self._require(handle) / _READY_MARKER
        await asyncio.to_thread(marker.touch)

    async def is_ready(self, handle: SandboxHandle) -> bool:
        """#366: True once `mark_ready` ran (and the sandbox still exists)."""
        marker = self._require(handle) / _READY_MARKER
        return await asyncio.to_thread(marker.is_file)

    def _ensure_home(self, handle: SandboxHandle, root: Path) -> Path:
        """The per-sandbox `$HOME` (#393/#600), guaranteed where it is USED.

        `create` makes this dir, but a live sandbox never goes back through
        `create` — the app re-acquires only when its liveness probe reports the
        sandbox GONE. So one that predates the dir, or that an older image of
        this service built, runs for the rest of its life without it while every
        exec below points HOME at it regardless; `soffice` then aborts "User
        installation could not be completed" against a HOME that is not a
        directory at all. The jail bootstrap has always `mkdir -p`'d it per exec;
        this is the unjailed path — the one production runs — catching up.

        `IsolatedProcessSandbox` extends this to own the dir to the sandbox uid."""
        home = root / _HOME
        home.mkdir(exist_ok=True)
        return home

    def _ensure_venv(self, handle: SandboxHandle, root: Path) -> Path:
        """The project env's directory, guaranteed where it is USED.

        `UV_PROJECT_ENVIRONMENT` names `<root>/.venv`, whose PARENT is the
        sandbox root — a directory this service created and still owns. `uv
        sync` runs as the sandbox uid, so on the isolated backend it cannot
        create the dir at all:

            error: failed to create directory `…/.venv`: Permission denied

        which is every declared profile failing to start, in PRODUCTION only:
        the unjailed dev path drops no uid and creates it happily. That is the
        same failure `.home` had (#393), reached from the same side, so it gets
        the same answer — made where it is used, per exec, and owned.

        EMPTY, and only when absent. uv accepts an existing empty directory as
        its target but refuses one holding anything else ("cannot be used
        because it is not a valid Python environment"), so a real venv from an
        earlier turn has to be left exactly as it is — and a `mkdir` that
        clears anything would delete the very packages this feature installs.

        `IsolatedProcessSandbox` extends this to own the dir to the exec uid."""
        venv = root / _PROJECT_VENV
        venv.mkdir(exist_ok=True)
        return venv

    def _cache_key(self, handle: SandboxHandle) -> str:
        """The never-recycled name this sandbox's downloads belong to.

        The handle is a per-pod uuid here, so the ITEM id is what makes a cache
        survive into the item's next sandbox — the app sends it on every create.
        Absent (an older app, or a direct caller), the uuid still keys a cache
        nothing else can reach; it simply will not be re-used, and the sweeper
        collects it."""
        key = self._item_of.get(handle.id, handle.id)
        # Validated HERE, where the value becomes a path component. `item_id`
        # arrives as a raw string in the POST body; the app-side twin has always
        # run it through `_validate_sandbox_id`, and this side only checked it
        # when an NFS archive happened to be wired. `mkdir(exist_ok=True)` then
        # accepts an existing directory and `_own_cache` chowns it — so an
        # unchecked `..` would have handed an arbitrary directory to the
        # sandbox uid. Falling back to the handle keeps a bad id from taking
        # the whole service down over something only an operator can fix.
        if key in ("", ".", "..") or "/" in key or "\\" in key or "\x00" in key:
            logger.warning("local_process: unsafe item id %r; caching by handle instead", key)
            return handle.id
        return key

    def _own_cache(self, handle: SandboxHandle, cache: Path) -> None:
        """Hook: the base needs no ownership work — every exec here runs as this
        process. `IsolatedProcessSandbox` overrides it, because the uid that has
        to fill the cache is allocated per sandbox while the item, and so the
        directory, outlives it."""

    def cache_keys_in_use(self) -> set[str]:
        """The cache names a live sandbox may still write to.

        Read from the live sandboxes themselves rather than a counter, for the
        reason `tools_in_use` gives: a counter drifts the moment one dies in a
        way nobody recorded, and drifting the wrong way here means deleting a
        cache out from under a running sync."""
        return {self._cache_key(SandboxHandle(id=hid)) for hid in self._dirs}

    def cache_keys_present(self) -> set[str]:
        """Every item that has a cache on disk here, live or not.

        The sweep's caller needs this to ask a CROSS-POD question about each
        candidate: `cache_keys_in_use` can only answer for this process, and on
        a #345 shared root that is one pod's view of a directory every replica
        writes to."""
        cache_root = self._root / _UV_CACHE
        if not cache_root.is_dir():
            return set()
        try:
            return {p.name for p in cache_root.iterdir() if p.is_dir()}
        except OSError:
            return set()

    def sweep_uv_cache(self, *, in_use: set[str], max_bytes: int | None = None) -> list[str]:
        """Bring the uv cache under `max_bytes`, evicting least-recently-used
        item caches first. Returns the names removed.

        Policy copied from the tool cache (#674), including the parts it learned
        the hard way:

        * **No ceiling means no eviction.** "Unset" means "no limit" here as it
          does everywhere else in this repo. The tool cache once meant the
          opposite BY DEFAULT and so emptied itself minutes after every reap,
          which is the whole value of a cache gone.
        * **`in_use` is absolute.** A cache a live sandbox may still write to is
          never evicted, however full: over-full is a capacity problem, and
          deleting one mid-sync is a correctness one. If everything left is in
          use, that is a host needing more disk, and it says so.
        * **Oldest first**, where `_exec_argv` stamps a cache on every exec so
          "oldest" means least recently USED — read hits do not move mtime on
          their own."""
        cache_root = self._root / _UV_CACHE
        if not cache_root.is_dir() or max_bytes is None:
            return []

        # `stat` inside the sort key raises FileNotFoundError when something
        # else removed a cache between the listing and the sort — reproducible
        # with two sweeps overlapping in their deletion phase. The idle tick
        # that calls this catches only CancelledError, so one raise would stop
        # reaping for the pod's lifetime; `kill_idle` and `mirror_warm` are
        # per-item resilient for exactly this reason. A vanished cache sorts
        # oldest, which is also what it now is: gone.
        def _age(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        try:
            caches = sorted((p for p in cache_root.iterdir() if p.is_dir()), key=_age)
        except OSError:
            return []
        total = sum(_dir_size(p) for p in caches)
        removed: list[str] = []
        for path in caches:
            if total <= max_bytes:
                break
            if path.name in in_use:
                continue
            total -= _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)
        if total > max_bytes:
            logger.warning(
                "uv cache is %d bytes over its ceiling and every cache left belongs to a "
                "live sandbox — this host needs more disk, not a smaller cache",
                total - max_bytes,
            )
        return removed

    def _release_cache(self, handle: SandboxHandle) -> None:
        """Hook: the base owns nothing to hand back. `IsolatedProcessSandbox`
        overrides it, because the cache OUTLIVES the sandbox while carrying that
        sandbox's uid — and on the host those uids are pooled and handed straight
        back out. Left alone, the next tenant of that uid can read and rewrite
        the previous item's cache, which is the inheritance the item key was
        chosen to prevent, arriving one step later."""

    def _exec_argv(
        self, handle: SandboxHandle, cmd: list[str]
    ) -> tuple[list[str], Path, dict[str, str]]:
        """Build the `(argv, cwd, env)` for one command. The seam subclasses
        override to wrap `cmd` (e.g. `IsolatedProcessSandbox` prepends a
        `setpriv` + cgroup-join wrapper); the exec pump/timeout machinery below
        stays shared. Validates the handle (raises `SandboxNotFound`)."""
        root = self._require(handle)
        ws = root / _WORKSPACE
        self._ensure_home(handle, root)
        self._ensure_venv(handle, root)
        # Annotated: `pop(..., None)` below otherwise widens the inferred value
        # type to `str | None`, and an env mapping carrying a None value is a
        # TypeError at `create_subprocess_exec`, not a style problem.
        env: dict[str, str] = {**os.environ, "PYTHONUNBUFFERED": "1"}
        # The server's own interpreter choice is not the sandbox's. `UV_PYTHON`
        # inherited from this process makes uv fetch and install a MANAGED
        # CPython inside the sandbox to satisfy a version the SERVER was
        # configured with — measured on CI, where the harness sets
        # `UV_PYTHON=3.12`: a whole interpreter downloaded per sandbox, and the
        # 60s exec timeout hit. Same argument as VIRTUAL_ENV below: production
        # sets neither, and that is exactly why it must not be inherited —
        # otherwise the sandbox's toolchain is a property of how the server
        # happened to be launched.
        env.pop("UV_PYTHON", None)
        if self._isolate:
            # chroot onto the sandbox root; the bootstrap cds into /root + sets
            # HOME. The subprocess cwd is the root (the unshare wrapper runs there).
            argv = _jail_argv(str(root), cmd)
            sub_cwd = root
            # The bootstrap read-only bind-mounts this at /.tools (outside the
            # workspace) when set.
            if self._tools_dir is not None:
                env["SANDBOX_TOOLS_VIEW"] = str(self._require(handle) / _TOOLS_VIEW)
            # #393: the launcher's HOME is the sandbox's own `.home` — here in
            # its chroot-relative spelling, but the SAME dir the unjailed branch
            # names below: a sibling of the `/root` workspace, in the infra area,
            # so it is never walked/synced and is reaped with the sandbox.
            #
            # It used to be `/tmp`, and the bootstrap mounts a FRESH tmpfs over
            # /tmp on every exec — so a `pip install` here did not merely fail to
            # survive a recycle, it did not survive to the NEXT COMMAND, which is
            # the only way an install is ever used. That stayed invisible while
            # the bundled interpreter carried its PEP 668 marker (pip refused,
            # loudly); dropping the marker turned the loud refusal into a silent
            # evaporation. This is not persistence — nothing outlives the sandbox
            # — it is the jail catching up to the unjailed path.
            env["SANDBOX_HOME"] = f"/{_HOME}"
            # #775: uv builds the project env HERE, in the infra area, not at
            # the `.venv` beside `pyproject.toml` that it would default to.
            # Inside the workspace it would be charged to the user's quota
            # while the mirror deliberately refuses to persist it — paying for
            # something they cannot delete and we discard anyway. Set for every
            # exec so a user's own `uv add` targets the same env as the sync.
            env["UV_PROJECT_ENVIRONMENT"] = f"/{_PROJECT_VENV}"
            # The bootstrap exports VIRTUAL_ENV itself when it finds a venv —
            # it is the one that probes, per exec, after the mounts exist. Drop
            # any inherited value so the jail can never see the server's own.
            env.pop("VIRTUAL_ENV", None)
            # ⚠️ NOT the same rule as the unjailed branch below, which keeps the
            # cache OUTSIDE the sandbox so the item's next one re-uses it. Here
            # `$root/<id>` IS the chroot root, so a sibling is outside the jail
            # entirely and would need a bind-mount of its own. The jail's cache
            # therefore dies with its sandbox — a cost, not a policy, and only
            # `kind: local` + `isolate` pays it; production does not.
            env["UV_CACHE_DIR"] = f"/{_HOME}/.cache/uv"
            # The user-env file, in its chroot-relative spelling — the SAME file
            # the unjailed branch names below, a sibling of the `/root`
            # workspace. Set unconditionally: the launcher guards with `-f`, and
            # one assignment cannot disagree with the launcher about when the
        else:
            # No chroot: run directly in the workspace subdir. cwd is the
            # workspace (the user's files); HOME is the per-sandbox `.home`.
            argv = cmd
            sub_cwd = ws
            # HOME is the sandbox's own `.home`, NOT the workspace. A tool that
            # writes its profile/cache to $HOME (LibreOffice's user installation
            # is the one that surfaced this — `soffice` aborts "User installation
            # could not be completed" when it can't create/lock its profile) must
            # not land it in the workspace: there it is mirrored to (possibly NFS)
            # durable, persists across turns, and pollutes the file tree + quota.
            # `.home` is a workspace sibling in the infra area — never walked or
            # synced, reaped with the sandbox — and `_provision` chowns it to the
            # exec uid (0700). #393 moved only the CARRIER launcher's HOME here;
            # this moves EVERY exec's, so a plain `soffice`/`git`/… works the same.
            env["HOME"] = str(root / _HOME)
            # #775: uv builds the project env HERE, in the infra area, not at
            # the `.venv` beside `pyproject.toml` that it would default to.
            # Inside the workspace it would be charged to the user's quota
            # while the mirror deliberately refuses to persist it — paying for
            # something they cannot delete and we discard anyway. Set for every
            # exec so a user's own `uv add` targets the same env as the sync,
            # and it is the very path the `python` shim looks for.
            env["UV_PROJECT_ENVIRONMENT"] = str(root / _PROJECT_VENV)
            # SANDBOX_HOME names the same dir for the carrier launcher's
            # `export HOME="${SANDBOX_HOME:-…}"` (#393). Survives the `setpriv`
            # wrap (no `--reset-env`) so the dropped uid's launcher reads it.
            env["SANDBOX_HOME"] = str(root / _HOME)
            # #775: uv's cache, keyed by the ITEM (see `_UV_CACHE`) and beside
            # the sandbox dirs rather than inside one, so the item's next
            # sandbox re-uses what this one downloaded. NEVER keyed by uid —
            # this backend pools and recycles those.
            cache = self._root / _UV_CACHE / self._cache_key(handle)
            # Degrade, never raise: `_exec_argv` runs BEFORE the try/except that
            # turns a command's problems into an exit code, so an error here
            # escapes `exec` as an exception and breaks its contract ("a
            # non-zero exit is a normal result, not an error"). A cache we
            # cannot prepare costs a re-download; a raise costs the turn.
            try:
                cache.mkdir(parents=True, exist_ok=True)
                # Stamp it. The sweep evicts oldest-first, and mtime alone is not
                # a "last used" signal: an exec that never touches uv does not
                # move it, and writes DEEPER in the tree do not move the top dir.
                # ⚠️ The first version of this comment also claimed a uv cache
                # HIT never moves it — measured FALSE on both 0.7.5 and 0.12.9,
                # where an `--offline` install does. The stamp stays for the
                # case that does hold: an item busy doing something else must
                # not look idle to the sweep.
                os.utime(cache, None)
                self._own_cache(handle, cache)
            except OSError:
                logger.warning(
                    "local_process: cannot prepare the uv cache at %s; this sandbox "
                    "will re-download instead",
                    cache,
                    exc_info=True,
                )
                cache = root / _HOME / ".cache" / "uv"
            env["UV_CACHE_DIR"] = str(cache)
            # The user-env file the tool launchers export from (same file, same
            # (Re)build + prepend the `python` shim so `python`/`python3*` route
            # to the python-stack carrier (or /usr/bin/python3), never the host's
            # own service venv that heads the inherited PATH (#350). The jail path
            # does this inside its per-exec bootstrap; unjailed has none, so we do
            # it here — per-exec so a carrier provisioned after `create` is seen.
            # The PATH survives the `setpriv` wrap (no `--reset-env`) and is
            # inherited by any child the script spawns.
            if self._install_python_shim(root):
                # The rest of the ecosystem reads VIRTUAL_ENV, not
                # UV_PROJECT_ENVIRONMENT — measured: `uv pip install` ignores
                # the latter entirely and answers "No virtual environment
                # found; run `uv venv`, or pass --system".
                #
                # Following that advice is WORSE than it sounds. `uv venv` DOES
                # honour UV_PROJECT_ENVIRONMENT when cwd is a project root, so
                # in a declared workspace it does not leave a stray `.venv`
                # beside `pyproject.toml` — it rebuilds the synced environment
                # in place, emptying it. Measured: `import tinydep` works
                # before, `ModuleNotFoundError` after. Setting VIRTUAL_ENV does
                # not prevent that; it removes the reason to reach for it.
                env["VIRTUAL_ENV"] = str(root / _PROJECT_VENV)
            else:
                # Never inherited. `env` starts as a copy of this process's
                # environment, and a service started under `uv run` carries a
                # VIRTUAL_ENV naming ITS OWN venv — which would point a
                # sandbox's tooling at the service's interpreter. Production
                # sets neither (both images exec a plain interpreter), so this
                # is not a live leak; it is a value that must never be a
                # property of how the server happened to be launched.
                env.pop("VIRTUAL_ENV", None)
            env["SANDBOX_JAILBIN"] = str(root / _JAILBIN)
            env["PATH"] = f"{env['SANDBOX_JAILBIN']}{os.pathsep}{env.get('PATH', '')}"
            # A LOGIN shell (`bash -lc …`, and the `sh -lc` wrapper every
            # workflow node command rides) sources /etc/profile, which on Debian
            # HARD-RESETS PATH — throwing the line above away and routing the
            # agent back to the image's own interpreter, with none of the
            # carrier's deps and none of its HOME rewriting. The jail overlays a
            # tmpfs on /etc/profile.d to re-prepend; unjailed has no chroot to
            # overlay, so the image installs `docker/profile.d/sandbox-jailbin.sh`
            # and it reads the dir back out of SANDBOX_JAILBIN (per-sandbox, so a
            # pod-wide file cannot name it; /etc/profile resets PATH only, so the
            # variable survives).
        return argv, sub_cwd, env

    async def exec(
        self,
        handle: SandboxHandle,
        cmd: list[str],
        on_output: OutputSink | None = None,
        env: Mapping[str, str] | None = None,
        exec_timeout: float | None = None,
    ) -> ExecResult:
        argv, sub_cwd, base_env = self._exec_argv(handle, cmd)
        # A caller may name its own TOTAL wall-clock budget for one command.
        # `uv sync` does: a cold start downloads a whole dependency stack, and
        # the instance default (60s) turned a slow link into exit 124 instead of
        # a wait. The IDLE cap is untouched, so a download that actually stops is
        # still killed promptly rather than waiting the whole budget out.
        budget = self._exec_timeout if exec_timeout is None else exec_timeout
        # The caller's variables land LAST, so they win over the exec path's own
        # settings — the precedence the tools have always had.
        env = {**base_env, **env} if env else base_env
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=sub_cwd,
                # /dev/null stdin: a program reading input gets EOF instead of
                # blocking on a terminal it doesn't have.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Unbuffer Python so a long-running script's prints stream live to
                # on_output rather than sitting in a pipe buffer until it exits.
                env=env,
                # New session ⇒ the child leads its own process group (pgid==pid),
                # so a timeout / cancel can SIGKILL the WHOLE group — the command
                # plus any backgrounded grandchildren — instead of orphaning them
                # in the background.
                start_new_session=True,
            )
        except FileNotFoundError:
            # `create_subprocess_exec` raises when the binary is missing.
            # Protocol contract says "non-zero exit returned, not raised", so
            # translate to POSIX-standard exit 127 (command not found) with a
            # stderr message — the /exec endpoint and the agent's exec tool
            # then see a normal command failure, not a 500.
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=f"{cmd[0]}: command not found\n".encode(),
            )
        except PermissionError as exc:
            # The binary exists but isn't executable (no x-bit, or the jail
            # blocks it). POSIX exit 126 = "found but not executable".
            return ExecResult(
                exit_code=126,
                stdout=b"",
                stderr=f"{cmd[0]}: {exc.strerror or 'permission denied'}\n".encode(),
            )
        # stdout/stderr are PIPE above, so the StreamReaders are always present.
        assert proc.stdout is not None and proc.stderr is not None
        out_buf: list[bytes] = []
        err_buf: list[bytes] = []
        loop = asyncio.get_running_loop()
        start = loop.time()
        last_output = start  # bumped on every chunk; drives the idle timeout

        async def _pump(stream: asyncio.StreamReader, buf: list[bytes], sink: OutputSink | None):
            nonlocal last_output
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                last_output = loop.time()
                buf.append(chunk)
                if sink is not None:
                    sink(chunk)

        readers = asyncio.gather(
            _pump(proc.stdout, out_buf, on_output),
            # stderr streams to the same live sink — progress bars / warnings /
            # logs a tool writes to stderr show up live, not just at the end.
            # The result still keeps stdout/stderr separate.
            _pump(proc.stderr, err_buf, on_output),
        )

        async def _terminate() -> None:
            """Stop the pump tasks and SIGKILL the command's whole process
            group, then reap. Shared by the timeout and cancel paths."""
            readers.cancel()
            with contextlib.suppress(BaseException):
                await readers
            _kill_process_group(proc)
            with contextlib.suppress(BaseException):
                await proc.wait()

        async def _watchdog() -> str:
            """Return which deadline tripped: `exec` (total wall-clock) or `log`
            (idle — no output for log_timeout). Parks forever if both are
            disabled (0); the readers-vs-watchdog race below ends it when the
            command exits on its own. Re-checks after each sleep so output that
            arrives mid-wait pushes the idle deadline back."""
            while True:
                now = loop.time()
                waits: list[float] = []
                if budget > 0:
                    waits.append(budget - (now - start))
                if self._log_timeout > 0:
                    waits.append(self._log_timeout - (now - last_output))
                # Both timeouts disabled (0) ⇒ no deadline; park and re-check
                # (the readers-vs-watchdog race ends this when the command exits).
                delay = min(waits) if waits else 3600.0
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                now = loop.time()
                if budget > 0 and now - start >= budget:
                    return "exec"
                return "log"

        watchdog = asyncio.create_task(_watchdog())
        timed_out: str | None = None
        try:
            done, _ = await asyncio.wait({readers, watchdog}, return_when=asyncio.FIRST_COMPLETED)
            if readers in done:
                await proc.wait()  # both streams hit EOF ⇒ the process exited
            else:
                timed_out = watchdog.result()  # a deadline tripped
                await _terminate()
        except asyncio.CancelledError:
            # When the awaiting turn is stopped, take the running command (and
            # any grandchildren it spawned) down with it — don't leave it
            # running in the background. Then re-raise so cancellation propagates.
            await _terminate()
            raise
        finally:
            watchdog.cancel()
            with contextlib.suppress(BaseException):
                await watchdog
            # The jail leaves /dev device-node files behind (bind targets) at
            # the sandbox root; drop them. (They're outside the workspace now,
            # so they wouldn't reverse-sync anyway — belt and suspenders.) In the
            # isolate path `sub_cwd` IS the sandbox root.
            if self._isolate:
                await asyncio.to_thread(shutil.rmtree, sub_cwd / "dev", ignore_errors=True)

        stdout = b"".join(out_buf)
        if timed_out is not None:
            # Keep the partial output the command produced before the kill.
            if timed_out == "exec":
                note = f"timed out after {budget:g}s (total) and was killed\n"
            else:
                note = f"no output for {self._log_timeout:g}s; assumed hung and killed\n"
            return ExecResult(
                exit_code=124, stdout=stdout, stderr=b"".join(err_buf) + note.encode()
            )
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=b"".join(err_buf),
        )

    def _own(self, handle: SandboxHandle, target: Path) -> None:
        """Hook: make `target` (and any parent dirs this write just created, up
        to the workspace root) owned by the sandbox principal. No-op in the base
        — a plain subprocess owns everything it writes; `IsolatedProcessSandbox`
        chowns to the sandbox uid so app/host-written files (restore / upload /
        create_file) match the DROPPED exec uid: real ownership, not just a
        default ACL (#504). Owner matters beyond access — git refuses a repo it
        doesn't own, and only the owner can `chmod` a file."""
        return None

    async def reown(self, handle: SandboxHandle) -> None:
        """Hook (#504): recursively re-own the workspace to the sandbox principal
        after a BULK restore that bypassed per-write `_own` (the host's rsync
        writes files as root, no `-o`). No-op in the base; `IsolatedProcessSandbox`
        chowns the whole restored tree to the sandbox uid."""
        return None

    async def upload(self, handle: SandboxHandle, data: bytes, remote_path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)
        await asyncio.to_thread(self._own, handle, target)

    async def download(self, handle: SandboxHandle, remote_path: str) -> bytes:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, remote_path)
        return await asyncio.to_thread(target.read_bytes)

    async def upload_file(self, handle: SandboxHandle, local_path: Path, remote_path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # copyfile streams in chunks (shutil.COPY_BUFSIZE) — no whole-file in RAM.
        await asyncio.to_thread(shutil.copyfile, local_path, target)
        await asyncio.to_thread(self._own, handle, target)

    async def download_to_file(
        self, handle: SandboxHandle, remote_path: str, local_path: Path
    ) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, remote_path)
        if not await asyncio.to_thread(target.is_file):
            raise FileNotFoundError(remote_path)
        await asyncio.to_thread(shutil.copyfile, target, local_path)

    async def disk_usage(self, handle: SandboxHandle) -> int:
        ws = self._workspace(handle)
        if not ws.exists():
            return 0
        total = await self._du([ws])
        return total if total is not None else await asyncio.to_thread(self._du_sync, ws)

    @staticmethod
    async def _du(targets: list[Path]) -> int | None:
        """`du -sb` over the workspace, or None if `du` can't
        answer (a minimal image without coreutils, a permissions failure) — the
        caller then falls back to walking in Python.

        `-b` is apparent bytes, so the figure is comparable with the sizes the
        file tree shows; `du` does not follow symlinks without `-L`, so a link
        the agent drops in can't charge someone else's tree here; and it
        counts a hardlinked inode once per invocation, which a naive walk
        double-counts. It DOES include the directory entries themselves, so the
        total runs a few KB above the sum of file sizes — that overhead is real
        disk, and it is noise against a quota measured in GiB."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "du",
                "-sb",
                "--",
                *(str(p) for p in targets),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
        except (OSError, ValueError):
            return None
        if proc.returncode != 0:
            return None
        total = 0
        for line in out.decode(errors="replace").splitlines():
            head = line.split("\t", 1)[0].strip()
            if not head.isdigit():
                return None
            total += int(head)
        return total

    @staticmethod
    def _du_sync(base: Path) -> int:
        """Fallback for when `du` isn't available: the same total, walked here.

        `st_size`, not allocated blocks — the same quantity `walk` reports per
        file, so the figure and the file tree's sizes agree. Symlinks are never
        followed (nor descended into), so a link the agent drops into its
        workspace can't charge someone else's tree to it, or itself twice."""
        total = 0
        for dirpath, _dirnames, filenames in os.walk(base, followlinks=False):
            for fname in filenames:
                f = Path(dirpath) / fname
                if not f.is_symlink():
                    with contextlib.suppress(OSError):  # raced deletion
                        total += f.stat().st_size
        return total

    async def size_of(self, handle: SandboxHandle, path: str) -> int | None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, path)
        return await asyncio.to_thread(self._size_sync, target)

    @staticmethod
    def _size_sync(target: Path) -> int | None:
        return target.stat().st_size if target.is_file() else None

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        cwd = self._workspace(handle)
        return await asyncio.to_thread(self._resolve(cwd, path).is_file)

    async def delete(self, handle: SandboxHandle, path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, path)
        if not await asyncio.to_thread(target.is_file):
            raise FileNotFoundError(path)
        await asyncio.to_thread(target.unlink)

    async def mkdir(self, handle: SandboxHandle, path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, path)
        await asyncio.to_thread(lambda: target.mkdir(parents=True, exist_ok=True))
        await asyncio.to_thread(self._own, handle, target)

    async def rmdir(self, handle: SandboxHandle, path: str) -> None:
        cwd = self._workspace(handle)
        target = self._resolve(cwd, path)
        if not await asyncio.to_thread(target.is_dir):
            raise FileNotFoundError(path)
        await asyncio.to_thread(shutil.rmtree, target)

    async def rename(self, handle: SandboxHandle, src: str, dst: str) -> None:
        cwd = self._workspace(handle)
        s, d = self._resolve(cwd, src), self._resolve(cwd, dst)
        if not await asyncio.to_thread(s.exists):
            raise FileNotFoundError(src)
        await asyncio.to_thread(lambda: d.parent.mkdir(parents=True, exist_ok=True))
        await asyncio.to_thread(s.rename, d)
        await asyncio.to_thread(self._own, handle, d)

    async def walk(self, handle: SandboxHandle, root: str) -> WalkResult:
        cwd = self._workspace(handle)
        base = self._resolve(cwd, root) if root.strip("/") else cwd
        return await asyncio.to_thread(self._walk_sync, cwd, base)

    @staticmethod
    def _walk_sync(cwd: Path, base: Path) -> WalkResult:
        entries: list[FileEntry] = []
        dirs: list[str] = []
        # The rglob already visits directories; it used to `continue` past them,
        # which is why a folder holding no files could not be seen from outside.
        for p in base.rglob("*"):
            rel = p.relative_to(cwd).as_posix()
            if p.is_dir():
                dirs.append(f"/{rel}")
                continue
            if not p.is_file():
                continue  # symlink / socket / fifo — never round-trips to the store
            stat = p.stat()
            # mtime(ns)+size — cheap, no read; ns granularity avoids same-second collisions.
            version = f"{stat.st_mtime_ns}-{stat.st_size}"
            entries.append(FileEntry(path=f"/{rel}", size=stat.st_size, version=version))
        return WalkResult(files=entries, dirs=dirs)

    @staticmethod
    def _resolve(cwd: Path, remote_path: str) -> Path:
        # Treat absolute paths as relative-to-cwd so the agent can use
        # canonical-looking paths without escaping the sandbox.
        p = remote_path.lstrip("/")
        return cwd / p
