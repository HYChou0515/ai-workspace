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

import asyncio

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
from workspace_app.workflow.engine import StepFailed

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


class _Overlapping(_Declared):
    """Records how many preparations are inside the sandbox AT ONCE.

    The count, not the total: N nodes legitimately prepare N times (this
    executor is app-scoped, so a remembered "prepared" would outlive the
    sandbox it was true of). What must never happen is two of them OVERLAPPING,
    because a failed one `rm -rf`s the shared project venv.
    """

    def __init__(self) -> None:
        super().__init__()
        self.live_syncs = 0
        self.peak_syncs = 0

    async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None) -> ExecResult:
        self.calls.append(list(cmd))
        if cmd == _SYNC:
            self.live_syncs += 1
            self.peak_syncs = max(self.peak_syncs, self.live_syncs)
            # A real sync is minutes of downloading, i.e. thousands of yields.
            # ONE `sleep(0)` was not enough and made this test pass with no lock
            # at all: the first caller's sync finished before the others got out
            # of `ensure_handle`, so nothing ever overlapped and the assertion
            # measured the double's timing rather than the guard.
            for _ in range(20):
                await asyncio.sleep(0)
            self.live_syncs -= 1
            return ExecResult(exit_code=0, stdout=b"")
        await asyncio.sleep(0)
        node = cmd[:2] == ["sh", "-lc"]
        return ExecResult(exit_code=7 if node else 0, stdout=b"out" if node else b"")


async def test_parallel_run_nodes_never_prepare_one_workspace_at_once(monkeypatch):
    """`wf.map` runs a `run:` node over its elements 8-way by default
    (`_DEFAULT_MAP_CONCURRENCY`), all on ONE item — so one
    `UV_PROJECT_ENVIRONMENT`.

    Preparing without a lock there is worse than the duplicated work it looks
    like: `ensure_project_env` `rm -rf`s the project venv before raising, so a
    single element's transient sync failure deletes the venv its siblings are
    syncing into or already running against. They then fall back to the carrier
    and report exit 0 — the confident wrong answer `ProjectEnvError` exists to
    refuse, arriving through the commit that added it.

    The lock is the ITEM's (`InvestigationSession.lock`), not the context's: an
    agent turn and a workflow node touch the same directory, and two locks on
    one resource is a guarantee that only looks like one.
    """
    sandbox = _Overlapping()
    executor, item_id = _build(monkeypatch, sandbox)

    await asyncio.gather(
        *(executor.run_sandbox(item_id, f"python step{i}.py", "") for i in range(8))
    )

    assert sandbox.calls.count(_SYNC) == 8, "each node still prepares — that part is right"
    assert sandbox.peak_syncs == 1, (
        f"but never two at once in one project directory: peaked at {sandbox.peak_syncs}"
    )


async def test_an_unpreparable_environment_fails_the_node_not_the_run(monkeypatch):
    """A `run:` node's failure is an EXIT CODE. `ProjectEnvError` escaping
    `run_sandbox` leaves `run_step`'s `check:` / `retries:` loop with nothing to
    act on, marks the whole run ERROR, and — inside a `wf.map` — leaves the
    sibling elements running detached, because the gather has no
    `return_exceptions`.

    It is still a stop, not a degrade: the node does not run its command, and
    uv's own words are what the operator gets.
    """

    class _Unbuildable(_Declared):
        async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None):
            self.calls.append(list(cmd))
            if cmd == _SYNC:
                return ExecResult(exit_code=2, stderr=b"error: no `uv.lock` found")
            return ExecResult(exit_code=0, stdout=b"")

    sandbox = _Unbuildable()
    executor, item_id = _build(monkeypatch, sandbox)

    with pytest.raises(StepFailed, match="no `uv.lock` found") as caught:
        await executor.run_sandbox(item_id, "echo hi", "")

    # `StepFailed`, not an exit code: `sandbox_node` hands an exit code to the
    # node's GATE, and `produces:` / `check:` never read one — so returning
    # `(1, reason)` reported the run as finished and journalled the node as
    # passed. And not the raw `ProjectEnvError` either: `wf.map`'s gather has
    # no `return_exceptions` and catches only `StepFailed`, so the siblings
    # would be left running detached. The half that can only be asserted
    # through the DSL is in `tests/workflow/test_dsl.py`, named
    # `..._whose_environment_fails_cannot_pass_its_produces_gate`.
    assert "uv sync" in str(caught.value), (
        f"and it carries uv's own words, which is all an operator has: {caught.value}"
    )
    assert not any(c[:2] == ["sh", "-lc"] for c in sandbox.calls), (
        f"and the node's command must NOT have run: {sandbox.calls}"
    )


async def test_an_agent_turn_shares_the_items_preparation_lock(monkeypatch):
    """The other half of the same resource.

    `AgentToolContext._wake` guards a CONTEXT, and a context is built per turn
    — so it cannot see a workflow `run:` node, a WUI tool call, or a second
    chat on the same item. All of them write one `UV_PROJECT_ENVIRONMENT`, and
    a failed preparation deletes it. Two locks on one resource is a guarantee
    that only looks like one, so the turn's context is wired to the ITEM's lock
    through the registry, the same one `run_sandbox` takes.

    Asserted on the context the real composition root builds, not one this test
    wires: a hook the builder forgets to set is exactly the failure mode.
    """
    from unittest import mock

    from workspace_app.api.turn_context import TurnContextBuilder

    spec = make_spec()
    captured: dict[str, TurnContextBuilder] = {}
    real = app_mod.TurnContextBuilder

    def _capture(**kw):
        builder = real(**kw)
        captured["b"] = builder
        return builder

    with mock.patch.object(app_mod, "TurnContextBuilder", _capture):
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=SpecstarFileStore(spec),
            runner=ScriptedAgentRunner([RunDone()]),
        )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )

    async def _dummy_subagent(*_a, **_k):
        return "", []

    ctx = await captured["b"].build_chat_turn(
        item_id,
        agent_config=None,
        run_subagent=_dummy_subagent,
        history_messages=[],
        reasoning_effort=None,
        kb_enhancements=None,
        collection_ids=[],
        collection_tiers=[],
        acting_user="u",
        speaker=None,
    )

    assert ctx.prepare_env_via is not None, (
        "a turn that prepares the environment on its own holds a lock nothing "
        "else on this item can see"
    )
