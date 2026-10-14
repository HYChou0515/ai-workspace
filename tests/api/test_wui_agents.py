"""A page asks a sub-agent (`docs/plan-wui-call-agent.md`, #942).

Driven through `create_app`, so what is under test is the wiring a page
actually reaches: the route, the run record, the turn the sub-agent runs as,
and the tools it holds.

- `callAgent`  → `POST …/wui/agents/{name}/call` `{input}` → `{output, failed}`
- `startAgent` → `POST …/wui/jobs` `{kind: "agent", name, input}` → `{job_id}`,
  then `GET …/wui/jobs/{id}` and `POST …/wui/jobs/{id}/stop`.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from specstar import SpecStar

import workspace_app.api.wui_routes as wui_routes
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.events import MessageDelta, RunDone, RunError
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.workflow.run import RunStatus, WorkflowRun

from ._client import TestClient

_DEF = (
    "---\n"
    "name: log-digger\n"
    "description: Digs through logs\n"
    # delete_file is outside the playground App's ceiling; run_agent is a tool
    # no sub-agent may hold. Neither may reach the turn.
    "tools: [read_file, delete_file, run_agent]\n"
    "---\n"
    "\n"
    "You dig logs.\n"
)


class SubRunner(ScriptedAgentRunner):
    """Records what every turn ran with; optionally never finishes."""

    def __init__(self, events: list[Any] | None = None, *, block: bool = False) -> None:
        super().__init__(events or [MessageDelta(text="found it"), RunDone()])
        self.seen: list[SimpleNamespace] = []
        self.block = block

    async def run(self, prompt: str, ctx):  # noqa: ANN001, ANN201 — mirrors the protocol
        cfg = ctx.agent_config
        self.seen.append(
            SimpleNamespace(
                prompt=prompt,
                system=cfg.system_prompt,
                tools=list(cfg.allowed_tools or []),
                history=list(ctx.history),
                run_agent=ctx.run_agent,
            )
        )
        if self.block:
            await asyncio.Event().wait()
        async for ev in super().run(prompt, ctx):
            yield ev


def _app(
    runner: SubRunner, *, user: str = "admin", creator: str | None = None
) -> tuple[TestClient, str, SpecStar]:
    spec = make_spec(default_user=user)
    store = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=store,
        runner=runner,
        get_user_id=lambda: user,
    )
    rm = spec.get_resource_manager(PlaygroundItem)
    with rm.using(creator or user):
        item_id = rm.create(PlaygroundItem(title="t", owner="owner-o", profile="echo")).resource_id
    asyncio.run(store.write(item_id, "/.agent/log-digger/AGENT.md", _DEF.encode()))
    return TestClient(app), item_id, spec


def _url(item_id: str, tail: str) -> str:
    return f"/a/playground/items/{item_id}/wui/{tail}"


def _runs(spec: SpecStar) -> list[WorkflowRun]:
    out = []
    for r in spec.get_resource_manager(WorkflowRun).list_resources():
        assert isinstance(r.data, WorkflowRun)
        out.append(r.data)
    return out


def _wait_state(client: TestClient, item_id: str, job_id: str, want: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        got = client.get(_url(item_id, f"jobs/{job_id}")).json()
        if got["state"] == want:
            return got
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {want}: {got}")


# ── callAgent ─────────────────────────────────────────────────────────


def test_a_page_asks_a_sub_agent_and_gets_its_answer_back():
    runner = SubRunner()
    client, item_id, spec = _app(runner)
    with client:
        resp = client.post(_url(item_id, "agents/log-digger/call"), json={"input": "why 500s?"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"output": "found it", "failed": False}
    (turn,) = runner.seen
    assert turn.prompt == "why 500s?"
    assert turn.system.strip() == "You dig logs."
    # D3: the chat's own narrowing — its file ∩ the App ceiling − the forbidden.
    assert turn.tools == ["read_file"]
    assert turn.history == [] and turn.run_agent is None


def test_the_ask_is_a_run_record_with_its_own_conversation():
    """D5: billed to the item's owner, labelled for the run list, and the
    conversation shows what was asked and what came back."""
    client, item_id, spec = _app(SubRunner())
    with client:
        client.post(_url(item_id, "agents/log-digger/call"), json={"input": "why 500s?"})

    (run,) = _runs(spec)
    assert run.status is RunStatus.DONE and run.captured_user == "owner-o"
    assert run.page_job is not None and (run.page_job.kind, run.page_job.name) == (
        "agent",
        "log-digger",
    )
    chat = spec.get_resource_manager(Conversation).get(run.chat_id).data
    assert isinstance(chat, Conversation)
    assert chat.title == "頁面：subagent log-digger"
    assert chat.run_id is not None
    assert [(m.role, m.content) for m in chat.messages] == [
        ("user", "why 500s?"),
        ("assistant", "found it"),
    ]


def test_a_sub_agent_the_item_does_not_have_is_refused_by_name():
    client, item_id, _ = _app(SubRunner())
    with client:
        resp = client.post(_url(item_id, "agents/nobody/call"), json={"input": "x"})
    assert resp.status_code == 404
    assert "nobody" in resp.json()["detail"] and "log-digger" in resp.json()["detail"]


def test_asking_a_sub_agent_or_starting_a_job_needs_the_right_to_run_things():
    """D3: `execute`, as `startRun` — a person who may edit the item but not run
    work in it is refused at every job entrance, and nothing is recorded."""
    from workspace_app.perm import Permission

    client, item_id, spec = _app(SubRunner(), user="hua", creator="bob")
    rm = spec.get_resource_manager(PlaygroundItem)
    item = rm.get(item_id).data
    assert isinstance(item, PlaygroundItem)
    who = ["user:hua"]
    item.permission = Permission(
        visibility="restricted",
        read_meta=who,
        read_chat=who,
        read_content=who,
        converse=who,
        add_content=who,
        edit_content=who,
    )
    with rm.using("bob"):
        rm.update(item_id, item)
    with client:
        call = client.post(_url(item_id, "agents/log-digger/call"), json={"input": "x"})
        job = client.post(
            _url(item_id, "jobs"), json={"kind": "agent", "name": "log-digger", "input": "x"}
        )
        status = client.get(_url(item_id, "jobs/whatever"))
    assert (call.status_code, job.status_code, status.status_code) == (403, 403, 403)
    assert _runs(spec) == []


def test_a_sub_agent_that_fails_is_an_answer_that_says_so():
    """The same sentence `run_agent` hands the main agent: a result, not an
    HTTP error, so the page can show it."""
    runner = SubRunner([MessageDelta(text="half"), RunError(message="model down")])
    client, item_id, _ = _app(runner)
    with client:
        resp = client.post(_url(item_id, "agents/log-digger/call"), json={"input": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed"] is True and "model down" in body["output"]


def test_a_call_that_runs_too_long_is_stopped_and_says_to_use_start(monkeypatch):
    monkeypatch.setattr(wui_routes, "CALL_TIMEOUT_S", 0.3)
    client, item_id, spec = _app(SubRunner(block=True))
    with client:
        resp = client.post(_url(item_id, "agents/log-digger/call"), json={"input": "x"})
        assert resp.status_code == 504
        assert "startAgent" in resp.json()["detail"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _runs(spec)[0].status is not RunStatus.CANCELLED:
            time.sleep(0.05)
    assert _runs(spec)[0].status is RunStatus.CANCELLED, "no half-finished work left running"


def test_an_answered_ask_leaves_the_items_sandbox_alone(monkeypatch):
    """Defect review round 1: the job's end went through the workflow release,
    which closes the item's sandbox when no other RUN is running — and a page's
    `callTool` or an open chat is not a run. Wired through `create_app`: the
    job frees its own turn session and nothing else."""
    from workspace_app.api.registry import InvestigationRegistry
    from workspace_app.api.turns import ChatTurnEngine

    closed: list[str] = []
    forgot: list[str] = []
    real_forget = ChatTurnEngine.forget

    async def close_session(self, investigation_id: str) -> None:
        closed.append(investigation_id)

    async def forget(self, key: str) -> None:
        forgot.append(key)
        await real_forget(self, key)

    monkeypatch.setattr(InvestigationRegistry, "close_session", close_session)
    monkeypatch.setattr(ChatTurnEngine, "forget", forget)
    client, item_id, spec = _app(SubRunner())
    with client:
        resp = client.post(_url(item_id, "agents/log-digger/call"), json={"input": "x"})
        assert resp.status_code == 200
        (run,) = _runs(spec)
        deadline = time.monotonic() + 5
        while run.chat_id not in forgot and time.monotonic() < deadline:
            time.sleep(0.05)
        assert run.chat_id in forgot
        assert item_id not in closed


# ── startAgent: the job ───────────────────────────────────────────────


def test_a_started_job_is_polled_to_its_answer():
    client, item_id, _ = _app(SubRunner())
    with client:
        resp = client.post(
            _url(item_id, "jobs"), json={"kind": "agent", "name": "log-digger", "input": "x"}
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        got = _wait_state(client, item_id, job_id, "done")
    assert got["output"] == {"output": "found it", "failed": False}


def test_a_started_job_can_be_stopped():
    client, item_id, _ = _app(SubRunner(block=True))
    with client:
        job_id = client.post(
            _url(item_id, "jobs"), json={"kind": "agent", "name": "log-digger", "input": "x"}
        ).json()["job_id"]
        _wait_state(client, item_id, job_id, "running")
        assert client.post(_url(item_id, f"jobs/{job_id}/stop")).status_code == 200
        _wait_state(client, item_id, job_id, "stopped")


def test_the_fifth_job_at_once_is_refused_with_a_sentence():
    client, item_id, _ = _app(SubRunner(block=True))
    with client:
        ids = [
            client.post(
                _url(item_id, "jobs"), json={"kind": "agent", "name": "log-digger", "input": "x"}
            ).json()["job_id"]
            for _ in range(4)
        ]
        resp = client.post(
            _url(item_id, "jobs"), json={"kind": "agent", "name": "log-digger", "input": "x"}
        )
        assert resp.status_code == 429
        assert "4" in resp.json()["detail"]
        for job_id in ids:
            client.post(_url(item_id, f"jobs/{job_id}/stop"))


def test_a_job_of_another_item_is_not_found():
    client, item_id, spec = _app(SubRunner())
    other = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="u", owner="owner-o", profile="echo"))
        .resource_id
    )
    with client:
        job_id = client.post(
            _url(item_id, "jobs"), json={"kind": "agent", "name": "log-digger", "input": "x"}
        ).json()["job_id"]
        _wait_state(client, item_id, job_id, "done")
        assert client.get(_url(other, f"jobs/{job_id}")).status_code == 404
        assert client.post(_url(other, f"jobs/{job_id}/stop")).status_code == 404


@pytest.mark.parametrize("body", [{"kind": "nope", "name": "x", "input": ""}, {"kind": "agent"}])
def test_a_malformed_job_is_refused(body):
    client, item_id, _ = _app(SubRunner())
    with client:
        assert client.post(_url(item_id, "jobs"), json=body).status_code == 422
