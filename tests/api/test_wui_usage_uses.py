"""P3 of `docs/plan-wui-usage.md`: the three kinds of use (U4) are counted, with
their time (U3/U11), only on a deployed page's link (U7) — failures too (U10).

`callTool` is timed in its route. The rest are runs (`startRun`, `startTool`,
`startAgent`, and `callAgent`, which is a job underneath): the run is tied to
the page when it starts and counted when it ends.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import pytest

from workspace_app.api.wui_deploy import DeployedPages, DeployedWui, register_deployed_wui
from workspace_app.api.wui_usage import (
    DEPLOYED_HEADER,
    WuiUsage,
    WuiUsageRecorder,
    WuiUsageStore,
    register_wui_usage,
)
from workspace_app.resources import make_spec, register_workflow_run

PAGE = "/p/page.ai.yaml"


def _hdr(path: str = PAGE) -> dict[str, str]:
    return {DEPLOYED_HEADER: quote(path, safe="")}


def _deploy(spec, item_id: str, path: str = PAGE) -> None:
    register_deployed_wui(spec)
    DeployedPages(spec).record(
        DeployedWui(
            slug="playground",
            item_id=item_id,
            path=path,
            title="P",
            deployed_by="admin",
            deployed_at=1,
        )
    )


def _uses(app) -> dict[str, list[int]]:  # noqa: ANN001 — FastAPI app
    """The app's counts so far, per person (summed over pages and days)."""
    out: dict[str, list[int]] = {}
    for users in app.state.wui_usage_store.recorder.drain().values():
        for user, cell in users.items():
            mine = out.setdefault(user, [0] * len(cell))
            for i, n in enumerate(cell):
                mine[i] += n
    return out


# cell layout: [opens, workflow n, workflow ms, tool n, tool ms, agent n, agent ms]
WF, TOOL, AGENT = 1, 3, 5


# ── the facade: a run tied to a page, counted once when it ends ───────────


def _usage():
    spec = make_spec()
    register_workflow_run(spec)  # registered post-`spec.apply` in the app (#870)
    register_wui_usage(spec)
    store = WuiUsageStore(spec, WuiUsageRecorder())
    usage = WuiUsage(store, spec=spec, is_deployed=lambda _i, _p: True)
    return usage, store


def _run(usage, run_id: str = "run-1", **fields) -> None:  # noqa: ANN001, ANN003
    """The run's own record, as the driver leaves it."""
    from workspace_app.workflow.run import WorkflowRun

    spec = usage._spec  # noqa: SLF001 — the facade's own spec
    spec.get_resource_manager(WorkflowRun).create(
        WorkflowRun(item_id="item", captured_user="alice", **fields), resource_id=run_id
    )


def test_a_run_s_time_is_from_when_it_started_to_when_it_ended_not_its_wait():
    """Review round 1 (R3): the time is the run record's `started → ended` — a
    run that waited for a slot did no work for anyone while it waited."""
    usage, store = _usage()
    usage.started("run-1", "item", PAGE, user="alice", kind="workflow")
    _run(usage, started=10_000, ended=70_000)

    usage.settled("run-1")

    (users,) = store.recorder.drain().values()
    assert users["alice"][WF : WF + 2] == [1, 60_000]


def test_a_page_job_s_time_stops_at_its_deadline():
    """A job found lost long after its deadline is ended when someone looks;
    the time it is charged ends where its limit did."""
    from workspace_app.workflow.run import PageJob

    usage, store = _usage()
    usage.started("run-1", "item", PAGE, user="alice", kind="agent")
    _run(
        usage,
        started=0,
        ended=500_000,
        page_job=PageJob(kind="agent", name="a", deadline=100_000),
    )

    usage.settled("run-1")

    (users,) = store.recorder.drain().values()
    assert users["alice"][AGENT : AGENT + 2] == [1, 100_000]


def test_a_run_cancelled_before_it_started_counts_with_no_time():
    usage, store = _usage()
    usage.started("run-1", "item", PAGE, user="alice", kind="workflow")
    _run(usage, ended=5_000)

    usage.settled("run-1")

    (users,) = store.recorder.drain().values()
    assert users["alice"][WF : WF + 2] == [1, 0]


def test_a_run_ending_twice_is_counted_once():
    usage, store = _usage()
    usage.started("run-1", "item", PAGE, user="alice", kind="agent")

    usage.settled("run-1")
    usage.settled("run-1")

    (users,) = store.recorder.drain().values()
    assert users["alice"][AGENT] == 1


def test_a_run_not_tied_to_a_page_is_not_counted():
    usage, store = _usage()

    usage.settled("someone-elses-run")

    assert store.recorder.drain() == {}


# ── callAgent / startAgent / startRun through the app ────────────────────


def test_asking_a_sub_agent_from_the_deployed_link_counts_one_agent_use():
    from .test_wui_agents import SubRunner, _app, _url

    client, item_id, spec = _app(SubRunner())
    _deploy(spec, item_id)
    with client:
        r = client.post(
            _url(item_id, "agents/log-digger/call"), json={"input": "x"}, headers=_hdr()
        )
        assert r.status_code == 200, r.text
        got = _uses(client.app)

    assert got["admin"][AGENT] == 1
    assert got["admin"][TOOL] == got["admin"][WF] == 0


def test_the_same_ask_inside_the_item_is_not_counted():
    from .test_wui_agents import SubRunner, _app, _url

    client, item_id, spec = _app(SubRunner())
    _deploy(spec, item_id)
    with client:
        client.post(_url(item_id, "agents/log-digger/call"), json={"input": "x"})
        assert _uses(client.app) == {}


def test_a_page_that_is_not_deployed_is_not_counted_even_naming_itself():
    from .test_wui_agents import SubRunner, _app, _url

    client, item_id, _spec = _app(SubRunner())
    with client:
        client.post(_url(item_id, "agents/log-digger/call"), json={"input": "x"}, headers=_hdr())
        assert _uses(client.app) == {}


def test_starting_a_sub_agent_as_a_job_counts_one_agent_use_when_it_ends():
    from .test_wui_agents import SubRunner, _app, _url, _wait_state

    client, item_id, spec = _app(SubRunner())
    _deploy(spec, item_id)
    with client:
        r = client.post(
            _url(item_id, "jobs"),
            json={"kind": "agent", "name": "log-digger", "input": "x"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        _wait_state(client, item_id, r.json()["job_id"], "done")
        got = _uses(client.app)

    assert got["admin"][AGENT] == 1


def test_a_count_that_fails_never_fails_the_job_it_counts(monkeypatch):
    """Review round 1 (defect 1 / regression 1): the job has started — a
    failing count must not hide its id from the page (which would then start
    it again), so the route answers as it would with no counting at all."""
    from .test_wui_agents import SubRunner, _app, _url, _wait_state

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("store down")

    monkeypatch.setattr(WuiUsage, "started", boom)
    client, item_id, spec = _app(SubRunner())
    _deploy(spec, item_id)
    with client:
        r = client.post(
            _url(item_id, "jobs"),
            json={"kind": "agent", "name": "log-digger", "input": "x"},
            headers=_hdr(),
        )
        assert r.status_code == 200, r.text
        assert _wait_state(client, item_id, r.json()["job_id"], "done")["state"] == "done"


def test_a_sub_agent_that_fails_still_counts():
    """U10: the time was spent whether or not it worked."""
    from workspace_app.api.events import RunError

    from .test_wui_agents import SubRunner, _app, _url

    client, item_id, spec = _app(SubRunner([RunError(message="boom")]))
    _deploy(spec, item_id)
    with client:
        client.post(_url(item_id, "agents/log-digger/call"), json={"input": "x"}, headers=_hdr())
        got = _uses(client.app)

    assert got["admin"][AGENT] == 1


def test_running_a_workflow_from_the_deployed_link_counts_one_workflow_use():
    from .test_headless_env import _ONE_AGENT_STEP, _executor_app, _poll_until_terminal

    _executor, item_id, spec, _runner, client = _executor_app(None, user="presser-p")
    register_deployed_wui(spec)
    DeployedPages(spec).record(
        DeployedWui(
            slug="playground", item_id=item_id, path=PAGE, title="P", deployed_by="x", deployed_at=1
        )
    )
    with client:
        base = f"/a/playground/items/{item_id}"
        client.put(f"{base}/files/.workflows/nightly.json", content=_ONE_AGENT_STEP)
        r = client.post(f"{base}/wui/run", json={"workflow": "nightly"}, headers=_hdr())
        assert r.status_code == 200, r.text
        runs = client.get(f"{base}/runs").json()
        assert _poll_until_terminal(client, item_id, runs[0]["run_id"]) == "done"
        got = _uses(client.app)

    assert got["presser-p"][WF] == 1


# ── callTool, timed in its route (the route's own doubles) ───────────────


def _tool_client(exit_code: int = 0, takes_s: float = 0.0):
    from workspace_app.sandbox.protocol import ExecResult

    from .test_wui_routes import _Sandbox, build

    class _Slow(_Sandbox):
        async def exec(self, handle, cmd, on_output=None, env=None):  # noqa: ANN001, ANN202
            await asyncio.sleep(takes_s)
            return await super().exec(handle, cmd, on_output, env)

    spec = make_spec()
    register_workflow_run(spec)  # registered post-`spec.apply` in the app (#870)
    register_wui_usage(spec)
    store = WuiUsageStore(spec, WuiUsageRecorder())
    usage = WuiUsage(store, spec=spec, is_deployed=lambda _i, p: p == PAGE)
    client, *_rest = build(
        sandbox=_Slow(ExecResult(exit_code=exit_code, stdout=b"{}", stderr=b"")), usage=usage
    )
    return client, store


def _tool_url() -> str:
    from .test_wui_routes import URL as TOOL_URL

    return TOOL_URL


def test_calling_a_tool_from_the_deployed_link_counts_one_tool_use():
    client, store = _tool_client()

    r = client.post(_tool_url(), json={"args": {}}, headers=_hdr())

    assert r.status_code == 200, r.text
    (users,) = store.recorder.drain().values()
    assert users["default-user"][TOOL] == 1


def test_a_tool_s_time_is_how_long_it_ran():
    client, store = _tool_client(takes_s=0.3)

    client.post(_tool_url(), json={"args": {}}, headers=_hdr())

    (users,) = store.recorder.drain().values()
    assert 300 <= users["default-user"][TOOL + 1] < 5_000


def test_a_page_named_without_its_leading_slash_is_the_same_page():
    """The header is normalised the way the deploy route normalises a path, so
    one page cannot be split in two by its spelling."""
    client, store = _tool_client()

    client.post(_tool_url(), json={"args": {}}, headers=_hdr(PAGE.lstrip("/")))

    ((key, _users),) = store.recorder.drain().items()
    assert key[1] == PAGE


def test_a_header_that_is_no_path_is_no_page_and_the_tool_still_runs():
    client, store = _tool_client()

    r = client.post(_tool_url(), json={"args": {}}, headers=_hdr("/p/../../etc/passwd"))

    assert r.status_code == 200, r.text
    assert store.recorder.drain() == {}


def test_a_tool_that_exits_non_zero_still_counts():
    client, store = _tool_client(exit_code=3)

    client.post(_tool_url(), json={"args": {}}, headers=_hdr())

    (users,) = store.recorder.drain().values()
    assert users["default-user"][TOOL] == 1


def test_calling_a_tool_inside_the_item_is_not_counted():
    client, store = _tool_client()

    client.post(_tool_url(), json={"args": {}})
    client.post(_tool_url(), json={"args": {}}, headers=_hdr("/not/deployed.ai.yaml"))

    assert store.recorder.drain() == {}


def test_a_refused_tool_is_not_a_use():
    """A tool that never ran (unknown here) spent no time on anyone's behalf."""
    from .test_wui_routes import URL as TOOL_URL

    client, store = _tool_client()

    r = client.post(
        TOOL_URL.replace("/tools/", "/tools/no-such-"), json={"args": {}}, headers=_hdr()
    )

    assert r.status_code >= 400
    assert store.recorder.drain() == {}


def test_the_page_lookup_is_awaited_from_a_running_loop():
    """Sanity: the facade's async lookup is awaitable from a running loop."""

    async def go() -> str | None:
        usage, _store = _usage()

        class _Req:
            headers = _hdr()

        return await usage.page_of(_Req(), "item")

    assert asyncio.run(go()) == PAGE


# ── a run that ends before it is tied to the page ────────────────────────


class _QuickOrchestrator:
    """A run that is already over by the time the route asks — or not."""

    def __init__(self, status):  # noqa: ANN001 — a RunStatus
        self.status = status

    async def start(self, **_kw) -> str:
        return "run-1"

    def run_status(self, _run_id: str):  # noqa: ANN202 — a RunStatus
        return self.status


def _run_client(status):  # noqa: ANN001, ANN202
    from .test_wui_routes import _Engine, build

    spec = make_spec()
    register_workflow_run(spec)  # registered post-`spec.apply` in the app (#870)
    register_wui_usage(spec)
    store = WuiUsageStore(spec, WuiUsageRecorder())
    usage = WuiUsage(store, spec=spec, is_deployed=lambda _i, p: p == PAGE)
    client, *_rest = build(
        orchestrator=_QuickOrchestrator(status),
        turn_engine=_Engine(),
        workflows=["judge"],
        usage=usage,
    )
    return client, store


def test_a_run_that_already_ended_when_it_was_tied_is_counted_by_the_route():
    """The hook fired before the tie existed and found nothing: the route sees
    the run over and counts it itself."""
    from workspace_app.workflow.run import RunStatus

    from .test_wui_routes import RUN_URL

    client, store = _run_client(RunStatus.DONE)

    r = client.post(RUN_URL, json={"workflow": "judge"}, headers=_hdr())

    assert r.status_code == 200, r.text
    (users,) = store.recorder.drain().values()
    assert users["default-user"][WF] == 1


@pytest.mark.parametrize("status", ["pending", "running", "awaiting_human"])
def test_a_run_still_going_when_it_was_tied_is_left_for_its_end(status):
    """Waiting at a gate for a person is not over: its end counts it."""
    from workspace_app.workflow.run import RunStatus

    from .test_wui_routes import RUN_URL

    client, store = _run_client(RunStatus(status))

    client.post(RUN_URL, json={"workflow": "judge"}, headers=_hdr())

    assert store.recorder.drain() == {}


# ── startTool, a job with the real orchestrator ──────────────────────────


def test_starting_a_tool_as_a_job_counts_one_tool_use_when_it_ends():
    from .test_wui_routes import build
    from .test_wui_tool_jobs import _Engine, _JobSandbox, _orchestrator, _wait

    spec = make_spec(default_user="default-user")
    register_workflow_run(spec)
    register_wui_usage(spec)
    store = WuiUsageStore(spec, WuiUsageRecorder())
    usage = WuiUsage(store, spec=spec, is_deployed=lambda _i, p: p == PAGE)
    engine = _Engine()
    orch = _orchestrator(spec, engine)
    orch.on_settled = usage.settled
    client, *_rest = build(
        sandbox=_JobSandbox(), orchestrator=orch, turn_engine=engine, usage=usage
    )

    r = client.post(
        "/a/rca/items/i1/wui/jobs",
        json={"kind": "tool", "name": "lot-status", "input": {}},
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    _wait(client, r.json()["job_id"], "done")

    (users,) = store.recorder.drain().values()
    assert users["default-user"][TOOL] == 1
