"""#775: an item whose cache cannot be prepared degrades, and says which one.

`_exec_argv` runs BEFORE the try/except that turns a command's problems into an
exit code, so anything raising there escapes `exec` and breaks its contract —
"a non-zero exit is a normal result, not an error". Preparing a download cache
is best-effort by nature: failing to costs a re-download; raising costs the turn.

Its own file rather than the shim twin (`test_project_venv_shim.py`), because
that one is GENERATED into `sandbox-host/tests/` and this case does not exist
there: the host's `_cache_key` warns and keys by handle instead of raising, so
the twin would assert a policy the host deliberately does not have.
"""

from __future__ import annotations

import logging
from pathlib import Path

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec


async def test_a_cache_name_that_cannot_be_used_does_not_escape_exec(tmp_path: Path) -> None:
    """`_cache_key` validates the id, and it used to sit one line OUTSIDE the
    guard whose own comment says this must not raise.

    `create` validates, so the id cannot arrive that way — but a handle is just
    a string, and `_require` resolves it against the shared root rather than a
    pod-local map (#345), so one can arrive from anywhere. The host twin
    already degrades on the same input instead of raising.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    await sb.create(SandboxSpec(), sandbox_id="item-a")
    # An EMPTY id: `_require` resolves `root / ""` to the shared root itself, so
    # it is a live directory and the handle gets past every earlier guard —
    # `_cache_key` is the first thing that refuses it. (`"../escape"` does NOT
    # reach here: `_require` fails first with `SandboxNotFound`, which is
    # contractual and correct.)
    unusable = SandboxHandle(id="")

    _argv, _cwd, env = sb._exec_argv(unusable, ["true"])  # must not raise

    assert env["UV_CACHE_DIR"].endswith("/.home/.cache/uv"), (
        "it must fall back to the in-sandbox cache rather than name one it could not make: "
        f"{env['UV_CACHE_DIR']}"
    )
    assert not (tmp_path / "sb" / ".uv-cache").exists(), (
        "and no cache directory is created for an id it refused"
    )


async def test_the_warning_names_the_cache_that_failed(tmp_path: Path, caplog) -> None:
    """The message is the only place this refusal is ever visible.

    The first version pre-assigned the fallback to the same variable it then
    reported, so a refused id printed the path the sandbox was about to use
    quite happily — a warning that describes the healthy outcome tells the
    reader nothing went wrong.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    await sb.create(SandboxSpec(), sandbox_id="item-a")
    # An EMPTY id: `_require` resolves `root / ""` to the shared root itself, so
    # it is a live directory and the handle gets past every earlier guard —
    # `_cache_key` is the first thing that refuses it. (`"../escape"` does NOT
    # reach here: `_require` fails first with `SandboxNotFound`, which is
    # contractual and correct.)
    unusable = SandboxHandle(id="")

    with caplog.at_level(logging.WARNING, logger="workspace_app.sandbox.local_process"):
        sb._exec_argv(unusable, ["true"])

    said = [r.getMessage() for r in caplog.records if "uv cache" in r.getMessage()]
    assert said, (
        f"a refused cache name must reach the log: {[r.getMessage() for r in caplog.records]}"
    )
    assert repr("") in said[0], (
        f"and it must name the id it refused, not the fallback it is about to use: {said[0]}"
    )
