"""LocalProcessSandbox — runs commands as subprocesses on the host.

For trusted single-host deployments (e.g. running the whole app inside a VM
or devcontainer). When unprivileged user namespaces are available (the
common case on modern Linux), `exec` runs each command inside a user+mount
namespace chrooted onto the sandbox directory, so that:

  * the user **workspace is `/root`** — the agent's cwd. File ops + `walk` are
    scoped here. `$HOME` (`~`) is a SEPARATE dir, `/.home`, in the infra area
    (see below) — so a tool's profile/cache (e.g. LibreOffice's user
    installation) never pollutes or locks on the synced workspace. The sandbox
    root (the chroot `/`) is the **infra area**: system overlays + provisioned
    tools + `.home` live there, OUTSIDE the workspace, never walked/synced.
  * the host filesystem is not reachable, and system dirs (`/usr`, `/etc`)
    are bind-mounted read-only so the agent can't tamper with the host.

Where user namespaces are unavailable it transparently falls back to a plain
subprocess in the workspace subdir (no isolation) — set `isolate=False` to
force this.
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
    RunningSandbox,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
    WalkResult,
)

logger = logging.getLogger(__name__)

# Bootstrap run (as namespace-root) before chroot: overlay the host's system
# dirs read-only onto the sandbox root, wire up a usable /dev + ephemeral
# /tmp, then chroot in and exec the user command. $1 is the jail root; the
# remaining args are the command. Device nodes are bind-mounted onto plain
# files (an unprivileged tmpfs is `nodev`, so nodes there can't be opened);
# the resulting /dev files are cleaned up by `exec` afterwards.
_JAIL_BOOTSTRAP = r"""
ROOT="$1"; shift
mkdir -p "$ROOT/usr" "$ROOT/proc" "$ROOT/dev" "$ROOT/etc" "$ROOT/tmp" "$ROOT/root" "$ROOT/.home"
mount --bind /usr "$ROOT/usr"; mount -o remount,bind,ro "$ROOT/usr"
mount --bind /etc "$ROOT/etc"; mount -o remount,bind,ro "$ROOT/etc"
# Provisioned tools: a shared host dir bind-mounted read-only at /.tools (a
# sibling of /root, so it's outside the workspace and never walked/synced).
if [ -n "$SANDBOX_TOOLS_DIR" ]; then
  mkdir -p "$ROOT/.tools"
  mount --bind "$SANDBOX_TOOLS_DIR" "$ROOT/.tools"; mount -o remount,bind,ro "$ROOT/.tools"
fi
for l in bin sbin lib lib64; do
  [ -L "$ROOT/$l" ] || [ -e "$ROOT/$l" ] || ln -s "usr/$l" "$ROOT/$l"
done
mount -t proc proc "$ROOT/proc" 2>/dev/null || true
mount -t tmpfs tmpfs "$ROOT/tmp" 2>/dev/null || true
for d in null zero full random urandom tty; do
  if [ -e "/dev/$d" ]; then : > "$ROOT/dev/$d"; mount --bind "/dev/$d" "$ROOT/dev/$d"; fi
done
# `python` shim selection. Two-tier:
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


def _validate_sandbox_id(sandbox_id: str) -> str:
    """#345: the id becomes a single path component under the (shared) sandbox
    root, so reject anything that could traverse out of / between sandboxes —
    empty, `.`/`..`, or a path separator. Returns the id unchanged when safe."""
    if (
        not sandbox_id
        or sandbox_id in (".", "..")
        or "/" in sandbox_id
        or "\\" in sandbox_id
        or "\x00" in sandbox_id
    ):
        raise ValueError(f"unsafe sandbox_id {sandbox_id!r}: must be a single safe path component")
    return sandbox_id


# The user workspace is this subdir of the sandbox root (the agent's ~/cwd).
# MUST match the `/root` the jail bootstrap cds into.
_WORKSPACE = "root"
# #366: readiness marker at the SANDBOX ROOT (a sibling of the workspace, OUTSIDE
# it) — written via mark_ready after a restore, so walk/sync/the file tree never
# see it and no user file can forge it. Teardown unlinks it FIRST (before rmtree).
_READY_MARKER = ".ready"
# Provisioned tools are made available here (a sibling of the workspace, so
# they're outside what walk/sync see). MUST match the jail bootstrap's mount.
_TOOLS = ".tools"
# Unjailed `python` shim dir (#350). The jail bootstrap (isolate=True) routes
# raw `python`/`python3*` to the python-stack carrier from inside the chroot;
# unjailed deployments — the model our pods actually run (no userns) — never
# execute that bootstrap, so without this `python` resolved via the inherited
# PATH to the host's OWN venv (the app's full dependency tree), not the carrier.
# We materialise the same shim as a real bin dir (a sibling of the workspace,
# so walk/sync never see it) and prepend it to PATH in `_exec_argv`. MUST stay
# outside the workspace.
_JAILBIN = ".jailbin"
# #393: per-sandbox HOME for the carrier launcher (caches + any `pip --user`
# install fallback). A sibling of the workspace, OUTSIDE it — so walk/sync never
# see it and it is reaped with the sandbox. The unjailed `_exec_argv` passes it
# as SANDBOX_HOME; this replaces the launcher's old shared-/tmp HOME that leaked
# a user's `pip install --break-system-packages` across sandboxes.
_HOME = ".home"

# #775: where `uv sync` builds the workspace's own environment. A sibling of
# the workspace, like `.home` and `.jailbin` — outside it, so walk/sync never
# see it and the quota never charges for a directory the user cannot delete
# and we discard with the sandbox anyway.
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


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the command's whole process group, then leave reaping to the
    caller. `start_new_session=True` at spawn made the child a group leader, so
    its pid IS the pgid — killing the group takes down any backgrounded
    grandchildren too (#74). A `ProcessLookupError` just means the group
    already exited between wait and kill — nothing left to do."""
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
        self._root = root_dir or Path(tempfile.gettempdir()) / "workspace-app-sandbox"
        self._root.mkdir(parents=True, exist_ok=True)
        # Shared, prebuilt provisioned-tools dir, made available at /.tools
        # (outside the workspace): read-only bind-mount when jailed, symlink when
        # not. One shared dir for all sandboxes — no per-sandbox copy.
        self._tools_dir = tools_dir
        self._dirs: dict[str, Path] = {}
        # Two peer timeouts (#70), each a hard cap; 0 disables that one:
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
        provisioned tools). The user workspace is the `_workspace` subdir.

        Resolved as a PURE function of the root + handle id (#345): a handle
        created by another process/pod on the same shared root resolves here
        without this instance having called `create` — the dir's existence on
        the shared vol is the source of truth, not a pod-local map. Missing dir
        ⇒ ``SandboxNotFound`` (cold / never materialized)."""
        path = self._root / handle.id
        if not path.is_dir():
            raise SandboxNotFound(handle.id)
        return path

    def _workspace(self, handle: SandboxHandle) -> Path:
        """The user workspace — a subdir of the sandbox root (the agent's
        `~`/cwd). File ops + walk are scoped here, so tools/caches living in the
        sandbox root (the infra area, outside this) are never seen or synced."""
        return self._require(handle) / _WORKSPACE

    async def effective_limits(self, spec: SandboxSpec) -> EnforcedLimits:
        """This backend applies no cgroup, so it caps nothing of its own: the
        answer is exactly what was asked for. A subclass that DOES cap (the
        isolated backend) overrides this. Charging its owner for a ceiling
        nobody enforces would be inventing a number."""
        return EnforcedLimits(cpu_cores=spec.cpu_cores, memory_bytes=spec.memory_bytes)

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        # #345: a given sandbox_id pins the handle id to a STABLE dir on the
        # (possibly shared) root and is IDEMPOTENT — re-creating reattaches to
        # the existing files instead of wiping them (so an item's working dir
        # survives across turns/pods). None keeps the original fresh-uuid path.
        hid = _validate_sandbox_id(sandbox_id) if sandbox_id is not None else str(uuid.uuid4())
        path = self._root / hid
        # exist_ok=True so a re-create reattaches; the first create still makes
        # the workspace subdir (and the sandbox/infra root parent).
        (path / _WORKSPACE).mkdir(parents=True, exist_ok=True)
        # #393: the per-sandbox HOME dir (a workspace sibling, in the infra area).
        # IsolatedProcessSandbox._provision chowns it to the sandbox uid.
        (path / _HOME).mkdir(exist_ok=True)
        # Unjailed: expose the shared tools dir via a symlink (jailed uses a
        # read-only bind-mount, set up per-exec in the bootstrap instead).
        # Guard on existence so a re-create doesn't raise FileExistsError.
        tools_link = path / _TOOLS
        if self._tools_dir is not None and not self._isolate and not tools_link.exists():
            tools_link.symlink_to(self._tools_dir)
        self._dirs[hid] = path
        logger.info("local_process: created sandbox %s (isolate=%s)", hid, self._isolate)
        return SandboxHandle(id=hid)

    def _install_python_shim(self, root: Path) -> bool:
        """Unjailed analogue of the jail bootstrap's three-tier `python` shim
        (#350), rebuilt per-exec like the bootstrap is. Build a `.jailbin` dir
        of `python`/`python3*` symlinks that route to the python-stack carrier's
        launcher when present, else to `/usr/bin/python3` — never the host's own
        venv that heads the inherited PATH. `_exec_argv` prepends this dir to
        PATH.

        Checks the IN-SANDBOX `<root>/.tools/python-stack/launch`, not the
        constructor's `tools_dir`, so it sees the carrier however it arrived: a
        whole-dir `.tools` symlink (tools_dir) OR a per-package `provision_tools`
        extract that lands after `create`. A plain symlink suffices: the carrier
        launch does `readlink -f "$0"`, resolving the chain to the real bundle."""
        carrier = root / _TOOLS / "python-stack" / "launch"
        # Carrier when present, else the system python3 — anything but the host's
        # own venv that heads the inherited PATH. (A deployment image always
        # ships one or the other; prod always ships the carrier.)
        has_carrier = os.access(carrier, os.X_OK)
        # #775, tier 1: the WORKSPACE's own venv, when it declared its
        # dependencies and `uv sync` built one. The carrier is what a profile
        # that said nothing falls back to — never a layer under one that spoke.
        # Checked per exec: the venv appears AFTER the sandbox exists.
        project = root / _PROJECT_VENV / "bin" / "python"
        if _usable_project_python(project):
            target = project
            from_venv = True
            has_carrier = False  # a uv venv ships no pip; see the shim tests
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
        # against a real `uv sync`; `tests/sandbox/test_project_env_e2e.py` is
        # that measurement kept.
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

    def handle_for_id(self, sandbox_id: str) -> SandboxHandle | None:
        # #345: dirs are keyed by id under the (shared) root, so the handle IS
        # the id. None for an unsafe id (routes to the snapshot rather than
        # raising in the sync routing path). Existence is not checked here — a
        # file op raises SandboxNotFound when the dir is cold.
        try:
            return SandboxHandle(id=_validate_sandbox_id(sandbox_id))
        except ValueError:
            return None

    async def running_sandboxes(self) -> list[RunningSandbox] | None:
        """`None` — this backend keeps no register of what is running.

        A sandbox here is a DIRECTORY on a shared volume that outlives every
        process and every restart, so the item dirs answer "who has files", not
        "what is running", and returning them would hand a caller the wrong
        question's answer. Nothing tracks the second question: commands are
        transient `exec`s, not a resident thing that could be listed.

        That holds for `IsolatedProcessSandbox` too, which DOES hold real cpu
        and memory (a per-item cgroup, which is what its `effective_limits`
        reports and what the owner is charged): those live with the cgroup, not
        with a registry this could read. Only the hosted backend keeps one, and
        it is the deployment this listing exists for."""
        return None

    async def kill(self, handle: SandboxHandle) -> None:
        path = self._require(handle)
        # #366: unlink `.ready` FIRST — rmtree's order is arbitrary, so a racing
        # mirror must never see "ready + files half-gone" and wipe the snapshot.
        await asyncio.to_thread((path / _READY_MARKER).unlink, missing_ok=True)
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
        self._dirs.pop(handle.id, None)
        logger.info("local_process: reaped sandbox %s", handle.id)

    async def mark_ready(self, handle: SandboxHandle) -> None:
        """#366: mark the sandbox authoritative — an empty file at the sandbox
        ROOT (`$root/id/.ready`), a sibling of the workspace, so it is never
        walked/synced nor shown in the file tree, and no user file can forge it."""
        marker = self._require(handle) / _READY_MARKER
        await asyncio.to_thread(marker.touch)
        logger.info("local_process: marked sandbox %s ready", handle.id)

    async def is_ready(self, handle: SandboxHandle) -> bool:
        """#366: True once `mark_ready` ran (and the sandbox dir still exists)."""
        marker = self._require(handle) / _READY_MARKER
        return await asyncio.to_thread(marker.is_file)

    def _ensure_home(self, handle: SandboxHandle, root: Path) -> Path:
        """The per-sandbox `$HOME` (#393/#600), guaranteed at the point it is USED.

        `create` makes this dir, but nothing puts a sandbox through a current
        `create` before its next command: the registry caches a live handle
        (create-once on a shared vol; for http it re-acquires only when the
        liveness probe reports the sandbox GONE). So a sandbox that predates the
        dir — or one built by an older image — runs for the rest of its life
        without it, while every exec below points HOME at it regardless. The
        failure is LibreOffice's "User installation could not be completed",
        because a HOME that does not exist is worse than the workspace HOME this
        replaced: that one was at least a directory.

        Per-exec for the same reason `_install_python_shim` is — what `create`
        did once is not what the next exec can rely on. `IsolatedProcessSandbox`
        extends this to chown the dir to the exec uid."""
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
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
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
                env["SANDBOX_TOOLS_DIR"] = str(self._tools_dir)
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
            # Same rule as the unjailed branch below, in the chroot's spelling:
            # the cache lives in the sandbox's own `.home` and dies with it.
            env["UV_CACHE_DIR"] = f"/{_HOME}/.cache/uv"
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
            # #775: uv's cache lives INSIDE the sandbox and dies with it.
            #
            # Never shared: uv verifies a wheel's hash when it downloads and
            # then trusts its own unpacked cache, so anyone able to write a
            # shared cache can inject code into everyone else's next install —
            # measured, and uv raises nothing. That is a cross-item code path,
            # and per-item isolation (#345) is the thing it would break.
            #
            # Never persisted either: a per-uid cache beside the sandboxes is
            # one full dependency stack per item that ever ran uv, outliving
            # even the item's deletion, and it owes a reaper forever.
            #
            # The cost is real and deliberate: a cold start re-fetches. Named
            # explicitly rather than left to uv's `$HOME/.cache/uv` default so
            # that choice is visible in the code that makes it.
            env["UV_CACHE_DIR"] = str(root / _HOME / ".cache" / "uv")
            # (Re)build + prepend the `python` shim so `python`/`python3*` route
            # to the python-stack carrier (or /usr/bin/python3), never the host's
            # own venv that heads the inherited PATH (#350). The jail path does
            # this in its per-exec bootstrap; unjailed has none, so we do it here
            # — per-exec so a carrier provisioned after `create` is seen. Survives
            # the `setpriv` wrap (no `--reset-env`) and is inherited by children.
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
            # overlay, so the images install `docker/profile.d/sandbox-jailbin.sh`
            # and it reads the dir back out of SANDBOX_JAILBIN (per-sandbox, so a
            # pod-wide file cannot name it; /etc/profile resets PATH only, so the
            # variable survives). See tests/sandbox/test_login_shell_path.py.
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
        # settings — the same precedence the tools have always had.
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
                # in the background (#74).
                start_new_session=True,
            )
        except FileNotFoundError:
            # `create_subprocess_exec` raises when the binary is missing.
            # Protocol contract says "non-zero exit returned, not raised", so
            # translate to POSIX-standard exit 127 (command not found) with a
            # stderr message — the /exec endpoint and the agent's exec tool
            # then see a normal command failure, not a 500.
            logger.warning("local_process: exec %s: %s not found (exit 127)", handle.id, cmd[0])
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=f"{cmd[0]}: command not found\n".encode(),
            )
        except PermissionError as exc:
            # The binary exists but isn't executable (no x-bit, or the jail
            # blocks it). POSIX exit 126 = "found but not executable".
            logger.warning(
                "local_process: exec %s: %s not executable (exit 126): %s",
                handle.id,
                cmd[0],
                exc,
            )
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
        last_output = start  # bumped on every chunk; drives the idle (#70) timeout

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
            # stderr streams to the same live sink (issue #23) — progress bars /
            # warnings / logs a tool writes to stderr show up live, not just at
            # the end. The result still keeps stdout/stderr separate.
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
            arrives mid-wait pushes the idle deadline back (#70)."""
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
            # #74: when the awaiting turn is stopped, take the running command
            # (and any grandchildren it spawned) down with it — don't leave it
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
            logger.warning(
                "local_process: exec %s killed: %s timeout (exit 124)",
                handle.id,
                timed_out,
            )
            return ExecResult(
                exit_code=124, stdout=stdout, stderr=b"".join(err_buf) + note.encode()
            )
        logger.debug("local_process: exec %s: %s -> exit %s", handle.id, cmd, proc.returncode)
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=b"".join(err_buf),
        )

    def _own(self, handle: SandboxHandle, target: Path) -> None:
        """Hook: make `target` (and any parent dirs this write just created, up
        to the workspace root) owned by the sandbox principal. No-op in the base
        — a plain subprocess owns everything it writes; `IsolatedProcessSandbox`
        chowns to the per-item uid so app/host-written files (restore / upload /
        create_file) match the DROPPED exec uid: real ownership, not just a
        default ACL (#504). Owner matters beyond access — git refuses a repo it
        doesn't own, and only the owner can `chmod` a file."""
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

    async def exists(self, handle: SandboxHandle, path: str) -> bool:
        cwd = self._workspace(handle)
        return await asyncio.to_thread(self._resolve(cwd, path).is_file)

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

    async def expose_port(self, handle: SandboxHandle, container_port: int) -> tuple[str, int]:
        self._require(handle)
        return ("127.0.0.1", container_port)

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
