"""Bring a workspace's python environment in line with its own `uv.lock`.

Runs beside ``provision_tools`` — after the snapshot restore, before the
agent's first command — so a profile can decide what its sandbox has instead
of that being a property of the image.

Design and rejected alternatives: ``docs/plan-profile-python-env.md``.
"""

from __future__ import annotations

import logging

from ..sandbox.protocol import ExecResult, OutputSink, Sandbox, SandboxHandle

logger = logging.getLogger(__name__)


class ProjectEnvError(RuntimeError):
    """The workspace's declared environment could not be prepared.

    Raised rather than degraded on purpose. A fallback to the image's own
    interpreter would hand the agent something that LOOKS right — the bundled
    data-science stack is still there — while the profile's own dependency is
    missing, and the turn would produce a confident wrong answer instead of a
    stop. These failures (an index that is down, a wheel with no build for
    this platform) are also almost always fixable only by whoever runs the
    deployment, so hiding them from that person buys nothing.

    Carries uv's own output: the person who can act needs the actual reason,
    not our paraphrase of it.

    BOTH streams, stderr first, because stderr is the one uv uses. uv writes
    its errors AND its progress there and nothing at all to stdout, so
    formatting `stdout` alone produced a header with an empty body: the
    operator got "`uv sync` failed (exit 2):" and not one word about why. That
    survived nine unit tests because every double in them put uv's words on
    stdout and so agreed with the bug -- `tests/sandbox/test_project_env_e2e.py`
    asserts it against real uv, where the two streams cannot be confused.
    """

    def __init__(self, result: ExecResult) -> None:
        self.result = result
        said = b"\n".join(part for part in (result.stderr, result.stdout) if part)
        super().__init__(
            f"`uv sync` failed (exit {result.exit_code}):\n{said.decode('utf-8', errors='replace')}"
        )


#: The file whose presence means "this workspace declares its dependencies".
#: Absent is the ordinary case and stays that way: every profile that predates
#: this ships none, and their sandboxes must behave exactly as before.
_MANIFEST = "pyproject.toml"


#: Said when the manifest has moved on but the lock has not. `--frozen` means
#: that edit does not take effect, which is the right trade and a SILENT one —
#: so it is the one thing here that must be spoken rather than logged.
_STALE = (
    b"note: pyproject.toml has changed but uv.lock has not, so the lock is what "
    b"was installed. Run `uv add <package>` (it updates both) or `uv lock`.\n"
)


#: The sync, with the `python` shim taken off the front of PATH for this one
#: command.
#:
#: `uv sync` picks its base interpreter off PATH, and the backend puts the shim
#: FIRST there so the agent's `python` beats the host's own venv (#350). uv
#: therefore built the project environment ON the shim and recorded it as the
#: base — measured on CI with uv 0.12.9:
#:
#:     venv/bin/python -> <root>/.jailbin/python3
#:     pyvenv.cfg:       home = <root>/.jailbin
#:
#: and the shim then points into that venv, so `python` execs itself: no
#: output, no exit, killed at the exec timeout. The shim refuses such a venv
#: (`_usable_project_python`), but refusing it costs the profile its packages,
#: so the environment must not be built that way in the first place.
#:
#: `${PATH#…}` is pure POSIX parameter expansion — nothing to be missing from a
#: minimal image, and a no-op when the variable is unset or the shim is not at
#: the front. The argv stays visible as its own elements rather than being
#: pasted into the shell string, so what runs is still readable and assertable.
_SYNC = [
    "sh",
    "-c",
    'PATH="${PATH#"$SANDBOX_JAILBIN:"}"; export PATH; exec "$@"',
    "sh",
    "uv",
    "sync",
    "--frozen",
    "--inexact",
]


async def ensure_project_env(
    sandbox: Sandbox,
    handle: SandboxHandle,
    *,
    on_output: OutputSink | None = None,
) -> None:
    """Make the sandbox's python environment match the workspace's lock.

    `on_output` is the turn's own sink, so uv's progress reaches the tool card
    the user is already watching. This runs inside the agent's first `exec`,
    before its command — without it, that card sits still for as long as the
    install takes with nothing saying why.
    """
    if not await sandbox.exists(handle, _MANIFEST):
        return

    # `--frozen`: the LOCK decides. Re-resolving would let one lock file
    # produce different versions on two cold starts, which is the whole thing
    # a lock is for. A hand-edited `pyproject.toml` therefore does not take
    # effect — silently, unless someone says so, which is the advisory below.
    #
    # `--inexact`: bring the lock's packages IN, take nothing else OUT. A plain
    # `uv sync` makes the environment match the lock exactly, which means it
    # UNINSTALLS whatever the person put there themselves — measured:
    #
    #     uv pip install idna   ->  idna OK
    #     uv sync --frozen      ->  Uninstalled 1 package  - idna==3.19
    #
    # This preparation is keyed to the AgentToolContext, which is built once per
    # TURN, so without the flag a package installed by hand disappears one turn
    # later with nothing said. The settled policy is that a user may install
    # what they like and `uv add` is the route we recommend — not that we quietly
    # undo them. uv's own flag, so nothing here has to model the difference.
    result = await sandbox.exec(handle, _SYNC, on_output=on_output)
    if result.exit_code != 0:
        # uv creates the environment BEFORE it fails — a missing lock still
        # leaves a working interpreter with none of the packages. That
        # directory is exactly what the `python` shim probes, so leaving it
        # would hand the agent a stack-less interpreter AND cost it the
        # carrier's `pip` shims: #581 ("installed into A, running in B")
        # through a new door, and permanent, because the manifest outlives the
        # sandbox.
        #
        # The invariant is that `python` is the project's only if we prepared
        # one. The path is the backend's to know (it sets the variable for
        # every exec), so the shell reads it there rather than us duplicating
        # it here; unset, the guard makes this a no-op.
        await sandbox.exec(
            handle,
            ["sh", "-c", '[ -n "$UV_PROJECT_ENVIRONMENT" ] && rm -rf "$UV_PROJECT_ENVIRONMENT"'],
            on_output=None,
        )
        raise ProjectEnvError(result)

    # AFTER a sync that worked, never before. This check cannot tell "uv says
    # the lock moved on" from "uv did not run at all" — a missing binary exits
    # 127 like anything else — so reading every non-zero as staleness told the
    # person the one thing that was NOT true: that their manifest had changed.
    # A false diagnosis in front of the real error is worse than none. Past the
    # sync, uv demonstrably exists.
    #
    # Reported, never refused: refusing would land on the cold-start path,
    # where the person watching can do nothing about it.
    if on_output is None:
        return
    stale = await sandbox.exec(handle, ["uv", "lock", "--check"], on_output=None)
    if stale.exit_code != 0:
        on_output(_STALE)
