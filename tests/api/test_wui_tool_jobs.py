"""`startTool` — a package tool run as a page job (`docs/plan-wui-call-agent.md` P4).

The same tool a page's `callTool` reaches, refused for the same reasons and
handed the same environment — but run as a job: a run record, no one-minute
cap on the command, an answer with `callTool`'s own shape, and a Stop that
reaches it from any pod. The route and its doubles are `test_wui_routes`'s; the
orchestrator is the real one.
"""

from __future__ import annotations

import asyncio
import time

from specstar import SpecStar

from tests.api.test_wui_routes import _Locator, _Sandbox, build
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.protocol import ExecResult
from workspace_app.workflow.orchestrator import WorkflowOrchestrator
from workspace_app.workflow.run import RunStatus, WorkflowRun
from workspace_app.workflow.run_identity import register_run_identity

JOBS = "/a/rca/items/i1/wui/jobs"


class _JobSandbox(_Sandbox):
    """`_Sandbox`, plus the per-call deadline a job passes, and optionally a
    command that never ends."""

    def __init__(self, result: ExecResult | None = None, *, block: bool = False):
        super().__init__(result)
        self.timeouts: list[float | None] = []
        self.block = block

    async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None):
        self.timeouts.append(exec_timeout)
        out = await super().exec(handle, cmd, on_output=on_output, env=env)
        if self.block:
            await asyncio.Event().wait()
        return out


class _Engine:
    """The turn engine's cross-pod stop signal: a per-key epoch."""

    def __init__(self) -> None:
        self.epochs: dict[str, int] = {}

    async def cancel_epoch(self, key: str) -> int:
        return self.epochs.get(key, 0)

    async def cancel_current(self, key: str) -> None:
        self.epochs[key] = self.epochs.get(key, 0) + 1


def _orchestrator(spec: SpecStar, engine: _Engine | None = None) -> WorkflowOrchestrator:
    register_run_identity(spec)
    return WorkflowOrchestrator(
        spec=spec,
        store=MemoryFileStore(),
        load_run=lambda *_a: None,  # ty: ignore[invalid-argument-type]
        load_manifest=lambda *_a: None,
        wire_handle=lambda *_a: None,
        on_stop=engine.cancel_current if engine else None,
    )


def _setup(sandbox: _JobSandbox, *, allowed=None, env=None, engine=None):
    spec = make_spec(default_user="default-user")
    engine = engine or _Engine()
    orch = _orchestrator(spec, engine)
    kw = {} if allowed is None else {"allowed": allowed}
    client, sb, _reg, loc = build(
        sandbox=sandbox,
        orchestrator=orch,
        turn_engine=engine,
        locator=_Locator(["mes"], env) if env else None,
        **kw,
    )
    return client, sb, loc, spec, engine


def _wait(client, job_id: str, want: str) -> dict:
    deadline = time.monotonic() + 5
    got: dict = {}
    while time.monotonic() < deadline:
        got = client.get(f"{JOBS}/{job_id}").json()
        if got["state"] == want:
            return got
        time.sleep(0.02)
    raise AssertionError(f"{job_id} never reached {want}: {got}")


def _start(client, args=None):
    return client.post(JOBS, json={"kind": "tool", "name": "lot-status", "input": args or {}})


def test_a_started_tool_answers_with_call_tools_own_shape():
    client, sandbox, loc, spec, _ = _setup(_JobSandbox())
    with client:
        resp = _start(client, {"lot": "A1"})
        assert resp.status_code == 200, resp.text
        got = _wait(client, resp.json()["job_id"], "done")
    assert got["output"] == {"output": '{"lot":"A1"}', "exit_code": 0}
    assert sandbox.calls and sandbox.calls[-1][-1] == '{"lot": "A1"}'
    assert loc.opened == [("i1", "頁面：工具 lot-status")]
    assert loc.asked_verb[0] == "execute"
    (run,) = [r.data for r in spec.get_resource_manager(WorkflowRun).list_resources()]
    assert isinstance(run, WorkflowRun) and run.captured_user == "alice"
    assert run.page_job is not None and run.page_job.input == '{"lot": "A1"}'


def test_a_started_tool_is_not_held_to_a_tool_calls_minute():
    """The job's own total is the cap, passed to the sandbox as the command's
    deadline — not the sandbox's default minute a `callTool` gets."""
    client, sandbox, _, _, _ = _setup(_JobSandbox())
    with client:
        _wait(client, _start(client).json()["job_id"], "done")
    (deadline,) = sandbox.timeouts
    assert deadline is not None and deadline > 60


def test_a_failing_tool_is_a_result_not_a_failed_job():
    """As `callTool`: the tool's own exit code is its answer."""
    client, _, _, _, _ = _setup(_JobSandbox(ExecResult(exit_code=3, stdout=b"no such lot")))
    with client:
        got = _wait(client, _start(client).json()["job_id"], "done")
    assert got["output"] == {"output": "no such lot", "exit_code": 3}


def test_a_tool_the_app_does_not_offer_is_refused_as_call_tool_refuses_it():
    client, sandbox, loc, spec, _ = _setup(_JobSandbox(), allowed=[])
    with client:
        resp = _start(client)
    assert resp.status_code == 403
    assert "lot-status" in resp.json()["detail"]
    assert sandbox.calls == [] and loc.opened == []
    assert list(spec.get_resource_manager(WorkflowRun).list_resources()) == []


def test_a_started_tool_gets_the_items_environment():
    client, sandbox, _, _, _ = _setup(_JobSandbox(), env={"MES_URL": "http://mes"})
    with client:
        _wait(client, _start(client).json()["job_id"], "done")
    assert sandbox.envs[-1].get("MES_URL") == "http://mes"


def test_a_stop_signalled_from_another_pod_reaches_the_command():
    """A Stop pressed on a pod that is not running the job only advances the
    chat's turn epoch (`orchestrator.cancel` → `on_stop`). A turn notices that by
    itself; a tool command has no turn, so the job watches the epoch."""
    engine = _Engine()
    client, _, loc, spec, _ = _setup(_JobSandbox(block=True), engine=engine)
    with client:
        job_id = _start(client).json()["job_id"]
        _wait(client, job_id, "running")
        chat = loc.opened and spec.get_resource_manager(WorkflowRun).get(job_id).data.chat_id
        asyncio.run(engine.cancel_current(chat))
        _wait(client, job_id, "stopped")
    row = spec.get_resource_manager(WorkflowRun).get(job_id).data
    assert isinstance(row, WorkflowRun) and row.status is RunStatus.CANCELLED


def test_a_tools_input_is_an_object():
    client, _, _, _, _ = _setup(_JobSandbox())
    with client:
        resp = client.post(JOBS, json={"kind": "tool", "name": "lot-status", "input": "text"})
    assert resp.status_code == 422
