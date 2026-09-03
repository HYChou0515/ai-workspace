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
    """

    def __init__(self, result: ExecResult) -> None:
        self.result = result
        super().__init__(
            f"`uv sync` failed (exit {result.exit_code}):\n"
            f"{result.stdout.decode('utf-8', errors='replace')}"
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

    # Reported, never refused. Refusing would land on the cold-start path,
    # where the person watching can do nothing about it.
    stale = await sandbox.exec(handle, ["uv", "lock", "--check"], on_output=None)
    if stale.exit_code != 0 and on_output is not None:
        on_output(_STALE)
    # `--frozen`: the LOCK decides. Re-resolving would let one lock file
    # produce different versions on two cold starts, which is the whole thing
    # a lock is for. A hand-edited `pyproject.toml` therefore does not take
    # effect — silently, unless someone says so, which is why that is its own
    # behaviour rather than a comment.
    result = await sandbox.exec(handle, ["uv", "sync", "--frozen"], on_output=on_output)
    if result.exit_code != 0:
        raise ProjectEnvError(result)
