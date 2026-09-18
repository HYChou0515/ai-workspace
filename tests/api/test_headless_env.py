"""A turn with NO request behind it gets what the deploy's ``IRequestEnv`` says
it gets (``docs/plan-headless-env.md``).

#714 wired the seam only where a request exists — a chat send, a WUI
``callTool`` — and left every request-less turn (a scheduled workflow node, a
goal-driver continuation, an event-triggered run) with the item's ``env_vars``
alone. That made the item's panel the only home for a service-account
credential: one hand-copied, expiring copy per item.

The seam now has a second method, ``env_without_request``, asked by exactly
those turns with the user the run was captured as. The platform still learns no
word for "service account": what comes back is the impl's policy.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast
from unittest import mock

import msgspec
import pytest
from agents import RunContextWrapper
from fastapi import FastAPI, HTTPException, Request
from specstar import SpecStar

import workspace_app.api.app as app_mod
from workspace_app.agent import AgentToolContext, exec_impl
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.api.request_env import IRequestEnv
from workspace_app.api.schemas import _MessageBody
from workspace_app.api.workflow_exec import WorkflowExecutor
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.llm import ILlm
from workspace_app.resources import Conversation, make_spec
from workspace_app.resources.conversation_goal import read_goal
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


class OnlyEnvFor(IRequestEnv):
    """A deploy written against #714's interface: ``env_for`` and nothing else."""

    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        return {"CALLER": user_id}


async def test_an_impl_written_before_this_method_existed_still_works_and_gives_nothing():
    """The default is today's behaviour, so a deploy that never heard of headless
    turns is neither broken (the class must still instantiate) nor surprised
    (its scheduled turns still carry nothing)."""
    seam = OnlyEnvFor()

    assert await seam.env_without_request(user_id="alice", item_id="item-1") == {}


# ─── the goal driver: a send with no request, through the real service ──────


class ServiceAccountEnv(IRequestEnv):
    """A deploy-shaped impl: a person's send carries their own cookie, a turn
    with nobody behind it carries the service account — and records who it
    was asked for, since that is the one thing the platform decides."""

    def __init__(self) -> None:
        self.asked_for: list[tuple[str, str]] = []

    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        return {"SSO": request.cookies.get("sso", ""), "CALLER": user_id}

    async def env_without_request(self, *, user_id: str, item_id: str) -> dict[str, str]:
        self.asked_for.append((user_id, item_id))
        return {"SA_TOKEN": f"sa-for-{user_id}"}


class RequestOnlyEnv(ServiceAccountEnv):
    """The control: a seam whose request half must NOT be reached. A headless
    path that reached for `env_for` would have no request to hand it, and a
    caller that passed a fake one would land here."""

    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        raise AssertionError("env_for was asked on a path with no request")


class CapturingRunner(ScriptedAgentRunner):
    """Records the env every turn actually ran with."""

    def __init__(self) -> None:
        # A one-word reply, so a DSL agent step's `out:` file is non-empty and
        # its gate passes on the first try.
        super().__init__([MessageDelta(text="ack"), RunDone()])
        self.envs: list[dict[str, str]] = []

    async def run(self, prompt: str, ctx):  # noqa: ANN001, ANN201 — mirrors the protocol
        self.envs.append(dict(ctx.user_env))
        async for event in super().run(prompt, ctx):
            yield event


def _send_app(
    source: IRequestEnv | None,
    *,
    env_vars: dict[str, str] | None = None,
    user: str = "u",
    owner: str = "owner-o",
) -> tuple[TestClient, CapturingRunner, str, SpecStar]:
    """One item whose OWNER is a different person from the user the app
    resolves, so a test that names a third identity really has three."""
    spec = make_spec(default_user=user)
    runner = CapturingRunner()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        request_env=source,
        get_user_id=lambda: user,
    )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner=owner, profile="echo", env_vars=env_vars or {}))
        .resource_id
    )
    return TestClient(app), runner, item_id, spec


def _default_chat(spec: SpecStar, item_id: str) -> tuple[str, Conversation]:
    rm = spec.get_resource_manager(Conversation)
    conv = Conversation(item_id=item_id, messages=[])
    return rm.create(conv).resource_id, conv


async def test_a_goal_driven_send_runs_on_what_the_seam_gives_a_turn_with_no_request():
    """The goal driver (#615) re-enters `send` with no request in hand, as the
    goal's setter. Three identities are in play — the item's owner (owner-o),
    the person the service resolves by default (admin), the goal's setter — and
    only the setter may be the one the seam is asked for."""
    seam = RequestOnlyEnv()
    client, runner, item_id, spec = _send_app(seam, env_vars={"FROM_ITEM": "i"}, user="admin")
    service = cast(FastAPI, client.app).state.chat_send  # what the sweeper holds
    rid, conv = _default_chat(spec, item_id)

    with client:
        await service.send(
            item_id,
            rid,
            conv,
            item_id,
            _MessageBody(content="driven"),
            author="goal-setter",
            driven_by="goal-driver",
        )

    assert runner.envs == [{"SA_TOKEN": "sa-for-goal-setter", "FROM_ITEM": "i"}]
    assert seam.asked_for == [("goal-setter", item_id)]


async def test_a_send_with_neither_request_nor_driver_gets_nothing_and_asks_nobody():
    """Absence of a request is not, by itself, the signal for "hand this turn
    the service account". The turn that gets it is one the platform STARTED
    on its own — the goal driver says so with `driven_by`. A caller that
    merely forgot to pass the request (the next route, a test helper) falls to
    the safe side: the item's copy alone, the same default `call_lane` keeps
    for the quota."""
    seam = ServiceAccountEnv()
    client, runner, item_id, spec = _send_app(seam, env_vars={"FROM_ITEM": "i"}, user="admin")
    service = cast(FastAPI, client.app).state.chat_send
    rid, conv = _default_chat(spec, item_id)

    with client:
        await service.send(item_id, rid, conv, item_id, _MessageBody(content="hi"))

    assert runner.envs == [{"FROM_ITEM": "i"}]
    assert seam.asked_for == []


def test_a_persons_send_carries_their_request_and_never_the_service_account():
    """The two methods are alternatives, not layers. A send with a request
    behind it is that person's turn; merging the service account underneath
    would hand every person who can chat the credential meant for the clock."""
    seam = ServiceAccountEnv()
    client, runner, item_id, _spec = _send_app(seam, user="hua")
    client.cookies.set("sso", "hua-cookie")

    with client:
        resp = client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert resp.status_code == 202
    assert runner.envs == [{"SSO": "hua-cookie", "CALLER": "hua"}]
    assert seam.asked_for == []


# ─── a workflow's agent node: the run's captured user, through the executor ──


def _executor_app(
    source: IRequestEnv | None,
    *,
    env_vars: dict[str, str] | None = None,
    owner: str = "owner-o",
    user: str = "admin",
) -> tuple[WorkflowExecutor, str, SpecStar, CapturingRunner, TestClient]:
    """The real composition root with the executor it built captured, and one
    item whose OWNER is not the user the app resolves — so a node attributed to
    a third person can only carry that person's name if the executor passed
    the captured user through."""
    spec = make_spec(default_user=user)
    runner = CapturingRunner()
    captured: dict[str, WorkflowExecutor] = {}
    real = app_mod.WorkflowExecutor
    with mock.patch.object(
        app_mod, "WorkflowExecutor", lambda **kw: captured.setdefault("ex", real(**kw))
    ):
        app = create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            request_env=source,
            get_user_id=lambda: user,
        )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner=owner, profile="echo", env_vars=env_vars or {}))
        .resource_id
    )
    return captured["ex"], item_id, spec, runner, TestClient(app)


async def test_a_workflow_node_runs_as_the_user_the_run_was_captured_for():
    seam = RequestOnlyEnv()
    executor, item_id, spec, runner, client = _executor_app(seam, env_vars={"FROM_ITEM": "i"})
    rid, _conv = _default_chat(spec, item_id)

    with client:
        await executor.drive_turn(item_id, rid, "alice", "do the thing", None)

    assert runner.envs == [{"SA_TOKEN": "sa-for-alice", "FROM_ITEM": "i"}]
    assert seam.asked_for == [("alice", item_id)]
    # "Never written back anywhere": not the run's chat, not the item, not any
    # other table the turn touched.
    assert "sa-for-alice" not in _everything_stored(spec)


def _everything_stored(spec: SpecStar) -> str:
    """Every resource in every table, as JSON, for a value scan."""
    out: list[str] = []
    for rm in spec.resource_managers.values():
        out.extend(msgspec.json.encode(r.data).decode() for r in rm.list_resources())
    return "\n".join(out)


def _poll_until_terminal(client: TestClient, item_id: str, run_id: str) -> str:
    status = ""
    for _ in range(400):
        status = client.get(f"/a/playground/items/{item_id}/runs/{run_id}").json()["status"]
        if status in ("done", "error", "cancelled"):
            return status
        time.sleep(0.02)
    return status


def test_a_run_a_person_starts_by_hand_runs_on_the_headless_source_not_their_request():
    """#714's reason for keeping workflow off the request still stands — a run
    re-runs on the clock and on uploads, when the person is gone — so the run
    they start by hand reads the same source its re-run will. Their cookie is
    on this POST and must not reach the node; the seam is asked for THEM,
    since the run is captured as them, but through the request-less method."""
    seam = ServiceAccountEnv()
    executor, item_id, _spec, runner, client = _executor_app(seam, owner="owner-o", user="hua")
    client.cookies.set("sso", "hua-cookie")

    with client:
        base = f"/a/playground/items/{item_id}"
        r = client.put(f"{base}/files/uploads/input.json", content='{"n": 1}')
        assert r.status_code == 204
        run_id = client.post(f"{base}/run").json()["run_id"]
        assert _poll_until_terminal(client, item_id, run_id) == "done"

    assert runner.envs == [{"SA_TOKEN": "sa-for-hua"}]
    assert seam.asked_for == [("hua", item_id)]


_ONE_AGENT_STEP = json.dumps(
    {
        "id": "ignored",
        "title": "Nightly",
        "phases": [{"id": "p"}],
        # `cache` is required since #810; this fixture predates that rule (#809
        # was opened before #810 merged, so its own CI never saw it) and has
        # failed on master since the two landed on the same day.
        "steps": [{"type": "agent", "prompt": "hi", "phase": "p", "out": "o.md", "cache": True}],
    }
)


def test_a_run_a_page_button_starts_is_captured_as_the_owner_not_the_presser():
    """`wui/run` runs the same engine an item schedule does, and is captured
    the same way: as the item's OWNER (`wui_routes.py`), whoever pressed the
    button. So the seam is asked for the owner — an identity the presser did
    not need to be, and one any `write_meta` holder can PATCH. This is the
    fact the docs' warning rests on: `user_id` says who the turn is
    attributed to, not who is present or consented."""
    seam = ServiceAccountEnv()
    _executor, item_id, _spec, runner, client = _executor_app(
        seam, owner="owner-o", user="presser-p"
    )

    with client:
        base = f"/a/playground/items/{item_id}"
        put = client.put(f"{base}/files/.workflows/nightly.json", content=_ONE_AGENT_STEP)
        assert put.status_code == 204
        resp = client.post(f"{base}/wui/run", json={"workflow": "nightly"})
        assert resp.status_code == 200, resp.text
        runs = client.get(f"{base}/runs").json()
        assert _poll_until_terminal(client, item_id, runs[0]["run_id"]) == "done"

    assert runner.envs == [{"SA_TOKEN": "sa-for-owner-o"}]
    assert seam.asked_for == [("owner-o", item_id)]


# ─── a failing impl: the turn does not run, and its words stay server-side ───


class LeakyBrokenEnv(ServiceAccountEnv):
    """An impl that does the thing the docs warn against: puts the value it
    was handling into the exception. The platform cannot stop it doing that;
    it can stop the message travelling anywhere a participant reads."""

    async def env_without_request(self, *, user_id: str, item_id: str) -> dict[str, str]:
        raise RuntimeError("exchange failed for token=hunter2")


def test_a_run_whose_headless_source_fails_ends_in_error_without_the_impls_words(caplog):
    """`driver.py` records the text of whatever escapes a step as the run's
    `result.error`, and a run record is read by everyone the item is shared
    with. So the executor must not let the impl's exception escape as itself:
    the run fails — visibly, as a run that ended in error, not a node that
    quietly ran as nobody — with fixed text, and the traceback goes to the
    server log alone."""
    seam = LeakyBrokenEnv()
    _executor, item_id, spec, runner, client = _executor_app(seam, user="hua")

    with client:
        base = f"/a/playground/items/{item_id}"
        client.put(f"{base}/files/uploads/input.json", content='{"n": 1}')
        run_id = client.post(f"{base}/run").json()["run_id"]
        assert _poll_until_terminal(client, item_id, run_id) == "error"
        run = client.get(f"{base}/runs/{run_id}").json()

    assert runner.envs == []  # no node ran
    assert "hunter2" not in str(run)  # `result.error` and every step's `reason`
    assert "hunter2" not in _every_message(spec)
    assert "hunter2" in caplog.text  # the operator still gets the traceback


async def test_a_goal_driven_send_whose_source_fails_is_refused_with_fixed_text():
    """The same refusal a person's send gets (#714): nothing persisted, the
    turn does not run, the impl's words stay in the server log. This is the
    SHAPE of the refusal, pinned on `send` directly; what the goal driver does
    with it is the next test, through the driver itself."""
    seam = LeakyBrokenEnv()
    client, runner, item_id, spec = _send_app(seam, user="admin")
    service = cast(FastAPI, client.app).state.chat_send
    rid, conv = _default_chat(spec, item_id)

    with client, pytest.raises(HTTPException) as caught:
        await service.send(
            item_id,
            rid,
            conv,
            item_id,
            _MessageBody(content="driven"),
            author="goal-setter",
            driven_by="goal-driver",
        )

    assert caught.value.status_code == 500
    assert caught.value.detail == {"error": "request_env_failed"}
    assert runner.envs == []
    assert _every_message(spec) == ""
    assert "hunter2" not in repr(caught.value)


class _NotMet(ILlm):
    """A goal checker that always asks for another round."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield ("NOT_MET", False)


def test_the_goal_driver_swallows_a_failing_source_refunds_the_round_and_leaks_nothing(caplog):
    """Through the real driver, not `send` alone: a person's turn ends, the
    checker says NOT_MET, `_goal_followup` charges a round and re-enters `send`
    with no request — where the seam fails. The refund and the catch are the
    driver's existing handling; this pins that on THIS failure they run, and
    that the thread the person reopens holds neither a `[goal]` continuation
    (nothing was persisted) nor the impl's words."""
    checker = _NotMet()
    spec = make_spec(default_user="alice")
    iid = register_rca_item(spec, owner="owner-o")
    runner = CapturingRunner()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: "alice",
        goal_checker_llm=checker,
        request_env=LeakyBrokenEnv(),
    )
    with TestClient(app) as client:
        chat = client.post(f"/a/rca/items/{iid}/chats", json={"title": "t"}).json()
        rid = chat["chat_id"]
        base = f"/a/rca/items/{iid}/chats/{rid}"
        client.put(f"{base}/goal", json={"condition": "the report exists"})
        client.cookies.set("sso", "alice-cookie")

        assert client.post(f"{base}/messages", json={"content": "go"}).status_code == 202

        _wait(lambda: "goal follow-up failed" in caplog.text)

    goal = read_goal(spec, rid)
    assert goal is not None and goal.state == "active" and goal.rounds_used == 0
    assert runner.envs == [{"SSO": "alice-cookie", "CALLER": "alice"}]  # the person's turn only
    thread = _every_message(spec)
    assert "[goal]" not in thread
    assert "hunter2" not in thread
    assert "hunter2" in caplog.text


def _wait(fn: Callable[[], object], timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def _every_message(spec: SpecStar) -> str:
    rm = spec.get_resource_manager(Conversation)
    out = []
    for r in rm.list_resources():
        data = r.data
        assert isinstance(data, Conversation)
        out.extend(m.content for m in data.messages)
    return "\n".join(out)


# ─── the docs' example is the thing a deploy pastes ─────────────────────────

_DOCS = Path(__file__).resolve().parents[2] / "docs" / "extending-the-platform.md"


async def test_the_documented_impl_is_a_real_implementation_of_both_halves():
    """The `SsoCookieEnv` block is the first thing a deploy author copies. If
    its headless method were mis-named or mis-signed, the failure would land on
    them — at the first scheduled run, at night. Compiling the doc's own text
    and calling both methods moves that failure here. The one external call
    (the deploy's token broker) is stood in for; the class is the doc's."""
    body = _DOCS.read_text(encoding="utf-8")
    start = body.index("### 隨「按下送出的那個人」而變的變數(#714)")
    block = re.search(r"```python\n(.*?)```", body[start:], re.DOTALL)
    assert block, "the #714 section lost its python example"

    class _Broker:
        async def shared_token(self) -> str:
            return "sa-shared"

    ns: dict = {"my_sa_broker": _Broker()}
    exec(compile(block.group(1), "<docs>", "exec"), ns)  # noqa: S102 — the doc IS the input
    cls = ns["SsoCookieEnv"]
    assert issubclass(cls, IRequestEnv)
    seam = cls()

    scope = {"type": "http", "headers": [(b"cookie", b"SSO_SESSION=abc")]}
    assert await seam.env_for(Request(scope), user_id="u", item_id="i") == {"MYCORP_SESSION": "abc"}
    assert await seam.env_without_request(user_id="owner", item_id="i") == {
        "MYCORP_SA_TOKEN": "sa-shared"
    }


# ─── the injection boundary is the same one #673 drew ────────────────────────


async def test_the_agents_own_exec_hands_the_sandbox_no_env_headless_or_otherwise():
    """#673: the item's variables reach the TOOLS, named per dispatch, and the
    agent's own `exec` — a shell, running as the same uid — gets none of them.
    The headless answer rides the same `user_env` field, so it stops at the
    same line: a scheduled node's shell does not become the place a service
    account leaks."""
    sandbox = MockSandbox()
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            sandbox=sandbox,
            filestore=MemoryFileStore(),
            files=WorkspaceFiles(MemoryFileStore()),
            user_env={"SA_TOKEN": "from-service-account"},
        )
    )

    await exec_impl(ctx, ["env"])

    assert sandbox.exec_envs == [{}]
