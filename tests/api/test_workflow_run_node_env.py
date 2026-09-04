"""#775: a workflow's `run:` node gets the workspace's declared environment.

`run:` reaches the sandbox through `registry.ensure_handle`, which wakes it and
nothing more. It used to inherit a prepared environment by accident: every
agent turn's pre-warm called `ensure_sandbox()`, which prepared the item's
environment whether or not that turn ever ran a command, so a `run:` node
placed after any agent node found the venv already built.

That pre-warm no longer prepares (it has nowhere to report, and a remembered
success silenced the staleness advisory for the whole turn), which turns the
accident into a hole — and puts the case that was ALWAYS broken, a `run:` node
with no agent node before it, in the same place. Both fail the same way and it
is the bad way: `_usable_project_python` correctly refuses the empty `.venv`,
`python` falls back to the carrier, and the node runs against an interpreter
that has none of the profile's declared packages with nothing said about it.
That is precisely the outcome `ProjectEnvError` exists to refuse.
"""

from __future__ import annotations

import pytest

import workspace_app.api.app as app_mod
from workspace_app.agent.python_env import _SYNC
from workspace_app.api import create_app
from workspace_app.api.events import RunDone
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle, SandboxSpec

pytestmark = pytest.mark.anyio


class _Declared(MockSandbox):
    """A workspace that declares its dependencies, recording what is run in it."""

    def __init__(self, *, declares: bool = True) -> None:
        super().__init__()
        self.calls: list[list[str]] = []
        self._declares = declares

    async def create(self, spec: SandboxSpec, sandbox_id: str | None = None) -> SandboxHandle:
        handle = await super().create(spec, sandbox_id)
        if self._declares:
            await self.upload(handle, b"[project]\nname='w'\n", "pyproject.toml")
        return handle

    async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None) -> ExecResult:
        self.calls.append(list(cmd))
        # A distinctive code for the NODE's own command, 0 for the preparation.
        # `assert exit_code == 0` here could not fail — everything returned 0 —
        # so it said nothing about whose result `run_sandbox` hands back, which
        # is the one thing a caller depends on.
        node = cmd[:2] == ["sh", "-lc"]
        return ExecResult(exit_code=7 if node else 0, stdout=b"out" if node else b"")


def _build(monkeypatch, sandbox: MockSandbox):
    """The real `WorkflowExecutor` `create_app` wires, captured on its way out.

    Driving the executor rather than a double is the point: the whole defect is
    that this method never asked for the environment, and a test that called
    `ensure_project_env` itself would have passed throughout."""
    spec = make_spec()
    captured: dict[str, object] = {}
    real = app_mod.WorkflowExecutor

    def _capture(**kw):
        executor = real(**kw)
        captured["ex"] = executor
        return executor

    monkeypatch.setattr(app_mod, "WorkflowExecutor", _capture)
    create_app(
        spec=spec,
        sandbox=sandbox,
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([RunDone()]),
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    return captured["ex"], item_id


async def test_a_run_node_prepares_the_declared_environment_before_its_command(monkeypatch):
    """The sync happens, and it happens FIRST — after the command it would be
    the carrier that ran the script."""
    sandbox = _Declared()
    executor, item_id = _build(monkeypatch, sandbox)

    exit_code, out = await executor.run_sandbox(item_id, "python analyze.py", "")

    assert (exit_code, out) == (7, "out"), (
        "the NODE's result is what a caller acts on — not the preparation's"
    )
    assert _SYNC in sandbox.calls, f"the node ran against an unprepared workspace: {sandbox.calls}"
    ran = [c for c in sandbox.calls if c and c[0] == "sh" and "-lc" in c]
    assert ran, f"the node's own command never ran: {sandbox.calls}"
    assert sandbox.calls.index(_SYNC) < sandbox.calls.index(ran[0]), (
        "preparing it after the command prepares it for nobody"
    )


async def test_a_run_node_in_a_workspace_that_declares_nothing_is_left_alone(monkeypatch):
    """The control. Every profile that predates #775 ships no manifest, and
    their nodes must run exactly as before — one `exists`, no sync, no extra
    round trip per node."""
    sandbox = _Declared(declares=False)
    executor, item_id = _build(monkeypatch, sandbox)

    await executor.run_sandbox(item_id, "echo hi", "")

    assert _SYNC not in sandbox.calls, f"nothing was declared; nothing to sync: {sandbox.calls}"
