"""#714 — environment variables derived from the REQUEST that triggered a turn.

The item's ``env_vars`` (#673) are one shared copy: stored on the item, plainly
readable by every participant, identical no matter who pressed send. This seam
carries the other kind — what the *person pressing send* is carrying on their
request (an SSO session cookie, a gateway header) — which is per-requester and
therefore may never be written back to the item.

The platform knows no cookie name. A deploy plugs in its own ``IRequestEnv``
through ``server.request_env``, the same dotted-path convention ``kb.parsers``
and ``health.checks`` use.
"""

from __future__ import annotations

from typing import cast
from unittest import mock

import pytest
from fastapi import FastAPI, Request
from specstar import SpecStar

import workspace_app.api.app as app_mod
from workspace_app.agent import AgentToolContext
from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.events import RunDone
from workspace_app.api.request_env import IRequestEnv
from workspace_app.api.schemas import _MessageBody
from workspace_app.api.turn_context import TurnContextBuilder
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.config.schema import Settings
from workspace_app.factories import get_request_env
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Conversation, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


class StubRequestEnv(IRequestEnv):
    """Module-level so the dotted-path resolver can import it."""

    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        return {"CALLER": user_id}


def test_unconfigured_deploy_has_no_request_env_source():
    """No ``server.request_env`` ⇒ the behaviour does not exist at all, and a
    turn's env keeps coming from the item alone."""
    assert get_request_env(Settings().server.request_env) is None


def test_configured_dotted_path_is_resolved_to_an_instance():
    source = get_request_env("tests.api.test_request_env.StubRequestEnv")
    assert isinstance(source, StubRequestEnv)


def test_a_class_that_is_not_a_request_env_is_refused_at_startup():
    """Loud at boot, not at the first send: a deploy that mis-names its impl
    should never reach the point where a turn silently runs without the
    caller's identity."""
    with pytest.raises(TypeError, match="not an IRequestEnv subclass"):
        get_request_env("workspace_app.config.schema.Settings")


# ─── the merge: what a turn's tools end up seeing ──────────────────────────


async def _dummy_subagent(*_a, **_k):
    return "", []


def _app_with_item(env_vars: dict[str, str]) -> tuple[TurnContextBuilder, str]:
    """The real composition root, plus one item carrying ``env_vars``."""
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
            filestore=MemoryFileStore(),
            runner=ScriptedAgentRunner([]),
        )
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo", env_vars=env_vars))
        .resource_id
    )
    return captured["b"], item_id


async def _chat_turn(builder: TurnContextBuilder, item_id: str, **kw) -> AgentToolContext:
    return await builder.build_chat_turn(
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
        **kw,
    )


async def test_request_values_reach_the_turn_alongside_the_items_own():
    builder, item_id = _app_with_item({"FROM_ITEM": "i"})

    ctx = await _chat_turn(builder, item_id, request_env={"FROM_REQUEST": "r"})

    assert ctx.user_env == {"FROM_REQUEST": "r", "FROM_ITEM": "i"}


async def test_the_items_value_wins_a_name_collision():
    """Decided in #714: the item's panel overrides the request-derived value,
    with no notice — so an operator can pin a value for testing."""
    builder, item_id = _app_with_item({"TOKEN": "from-item"})

    ctx = await _chat_turn(builder, item_id, request_env={"TOKEN": "from-request"})

    assert ctx.user_env == {"TOKEN": "from-item"}


async def test_a_turn_with_no_request_behind_it_sees_the_item_alone():
    """Every background re-entry (the goal driver, a workflow step, a scheduled
    job) lands here: nothing is stored, so there is nothing to inherit."""
    builder, item_id = _app_with_item({"FROM_ITEM": "i"})

    ctx = await _chat_turn(builder, item_id)

    assert ctx.user_env == {"FROM_ITEM": "i"}


async def test_a_workflow_turn_never_carries_request_values():
    """#714: workflow is not wired at all — it re-runs on a schedule and on
    uploads, so "the first step has them, the rest do not" would be a difference
    nothing in the UI could show."""
    builder, item_id = _app_with_item({"FROM_ITEM": "i"})

    ctx = await builder.build_workflow_turn(
        item_id, agent_config=None, run_subagent=_dummy_subagent, history_messages=[]
    )

    assert ctx.user_env == {"FROM_ITEM": "i"}


# ─── the send path: whose request, and what a failure does ─────────────────


class CookieEnv(IRequestEnv):
    """A deploy-shaped impl: names one cookie, and stamps who was asking."""

    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        return {"SSO": request.cookies.get("sso", ""), "CALLER": user_id}


class BrokenEnv(IRequestEnv):
    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        raise RuntimeError("the identity gateway said no")


class CapturingRunner(ScriptedAgentRunner):
    """Records the env every turn actually ran with."""

    def __init__(self) -> None:
        super().__init__([RunDone()])
        self.envs: list[dict[str, str]] = []

    async def run(self, prompt: str, ctx):  # noqa: ANN001, ANN201 — mirrors the protocol
        self.envs.append(dict(ctx.user_env))
        async for event in super().run(prompt, ctx):
            yield event


def _send_app(
    source: IRequestEnv | None, *, env_vars: dict[str, str] | None = None, user: str = "u"
) -> tuple[TestClient, CapturingRunner, str, SpecStar]:
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
        .create(PlaygroundItem(title="t", owner=user, profile="echo", env_vars=env_vars or {}))
        .resource_id
    )
    return TestClient(app), runner, item_id, spec


def _messages(spec: SpecStar) -> list[str]:
    rm = spec.get_resource_manager(Conversation)
    return [m.content for r in rm.list_resources() for m in _conv(r).messages]


def _conv(resource) -> Conversation:  # noqa: ANN001 — specstar resource envelope
    data = resource.data
    assert isinstance(data, Conversation)
    return data


def test_the_senders_own_cookie_reaches_the_turn_it_started():
    client, runner, item_id, _spec = _send_app(CookieEnv(), env_vars={"FROM_ITEM": "i"})
    client.cookies.set("sso", "abc")

    with client:
        resp = client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert resp.status_code == 202
    assert runner.envs == [{"SSO": "abc", "CALLER": "u", "FROM_ITEM": "i"}]


def test_a_failing_source_refuses_the_send_before_the_message_is_persisted():
    """#714 decision: identity is not something to guess at. A turn that ran
    anyway would act as nobody in particular and return an answer that looks
    right — and a message persisted without a turn locks the composer on a reply
    nobody is going to write."""
    client, runner, item_id, spec = _send_app(BrokenEnv())

    with client:
        resp = client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert resp.status_code == 500
    assert resp.json()["detail"] == {"error": "request_env_failed"}
    assert runner.envs == []  # no turn ran
    assert _messages(spec) == []  # and nothing was written down
    # The impl's own words stay server-side: only it knows whether it built that
    # string out of the very cookie it was reading.
    assert "the identity gateway said no" not in resp.text


def test_two_people_sending_into_one_item_get_their_own_values():
    """The reason this seam exists at all: an item is shared, but the identity
    on the turn is whoever pressed send."""
    client, runner, item_id, _spec = _send_app(CookieEnv())

    with client:
        client.cookies.set("sso", "ming")
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "one"})
        client.cookies.set("sso", "hua")
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "two"})

    assert [env["SSO"] for env in runner.envs] == ["ming", "hua"]


def test_the_chat_scoped_send_carries_the_request_too():
    """The second entry point into the same send. It is listed separately here
    because the two routes are where "wired in one place, forgotten in the
    other" would show up as a difference nobody can see."""
    client, runner, item_id, _spec = _send_app(CookieEnv())
    client.cookies.set("sso", "abc")

    with client:
        chat_id = client.post(
            f"/a/playground/items/{item_id}/chats", json={"title": "side"}
        ).json()["chat_id"]
        resp = client.post(
            f"/a/playground/items/{item_id}/chats/{chat_id}/messages", json={"content": "hi"}
        )

    assert resp.status_code == 202
    assert [env["SSO"] for env in runner.envs] == ["abc"]


async def test_a_send_with_no_request_behind_it_gets_nothing_even_with_a_seam():
    """The goal driver (#615) continues a chat by re-entering this very method
    with nobody watching and no request in hand. It is the same `send` the
    routes call, so it is worth pinning that a configured seam does not somehow
    produce values for it out of a stored copy — there is none."""
    client, runner, item_id, spec = _send_app(CookieEnv())
    service = cast(FastAPI, client.app).state.chat_send  # what the sweeper holds
    rid, conv = _default_chat(spec, item_id)

    with client:
        await service.send(
            item_id, rid, conv, item_id, _MessageBody(content="driven"), driven_by="goal-driver"
        )

    assert runner.envs == [{}]


def _default_chat(spec: SpecStar, item_id: str) -> tuple[str, Conversation]:
    rm = spec.get_resource_manager(Conversation)
    conv = Conversation(item_id=item_id, messages=[])
    return rm.create(conv).resource_id, conv


def test_a_deploy_with_no_seam_behaves_exactly_as_before():
    client, runner, item_id, _spec = _send_app(None, env_vars={"FROM_ITEM": "i"})

    with client:
        resp = client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert resp.status_code == 202
    assert runner.envs == [{"FROM_ITEM": "i"}]
