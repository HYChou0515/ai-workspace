"""P5 of `docs/plan-env-token-refresh.md`: every entry point that runs a tool
for a person refreshes a near-expiry value first — a chat send, a goal round, a
re-run on a peer, a page's tool call, a workflow node — and the page build,
which runs as nobody, never does.

Each test drives the real composition root (or the route the page calls) with
a sign-in that refreshes, and reads the env the tool was handed.
"""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI

from workspace_app.api.private_env import PrivateEnvStore
from workspace_app.api.schemas import _MessageBody

from .test_env_refresh_flow import OLD, _Erp
from .test_headless_env import _ONE_AGENT_STEP, _executor_app, _poll_until_terminal
from .test_request_env import _default_chat, _send_app

NEW_ENV = {"ERP_TOKEN": "new", "ERP_REFRESH": "r2"}


def _sign_ins(client, *providers) -> None:  # noqa: ANN001 — TestClient
    cast(FastAPI, client.app).state.env_providers = list(providers)


def test_a_chat_send_hands_the_tool_the_refreshed_value():
    client, runner, item_id, spec = _send_app(None, user="alice")
    _sign_ins(client, _Erp())
    PrivateEnvStore(spec).replace_personal("alice", OLD)

    with client:
        resp = client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert resp.status_code == 202
    assert runner.envs == [NEW_ENV]
    assert PrivateEnvStore(spec).personal("alice") == NEW_ENV


async def test_a_goal_round_refreshes_for_the_person_it_runs_for():
    client, runner, item_id, spec = _send_app(None, user="admin")
    _sign_ins(client, _Erp())
    PrivateEnvStore(spec).replace_personal("alice", OLD)
    service = cast(FastAPI, client.app).state.chat_send
    rid, conv = _default_chat(spec, item_id)

    with client:
        await service.send(
            item_id,
            rid,
            conv,
            item_id,
            _MessageBody(content="driven"),
            author="alice",
            driven_by="goal-driver",
        )

    assert runner.envs == [NEW_ENV]


def test_a_workflow_node_refreshes_for_the_presser():
    _executor, item_id, spec, runner, client = _executor_app(None, user="presser-p")
    _sign_ins(client, _Erp())
    PrivateEnvStore(spec).replace_personal("presser-p", OLD)

    with client:
        base = f"/a/playground/items/{item_id}"
        client.put(f"{base}/files/.workflows/nightly.json", content=_ONE_AGENT_STEP)
        assert client.post(f"{base}/wui/run", json={"workflow": "nightly"}).status_code == 200
        runs = client.get(f"{base}/runs").json()
        assert _poll_until_terminal(client, item_id, runs[0]["run_id"]) == "done"

    assert runner.envs == [NEW_ENV]


def test_a_deploy_with_no_refreshing_sign_in_changes_nothing():
    """The fast path: nothing to ask, nothing read, the old value handed on."""
    client, runner, item_id, spec = _send_app(None, user="alice")
    PrivateEnvStore(spec).replace_personal("alice", OLD)

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert runner.envs == [OLD]


def test_a_refreshed_shared_copy_is_written_as_the_platform():
    """D3: the platform writes the shared copy for whoever the run is for, and
    the revision says so."""
    from workspace_app.apps.playground.model import PlaygroundItem

    client, runner, item_id, spec = _send_app(None, user="alice", env_vars=dict(OLD))
    _sign_ins(client, _Erp())

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert runner.envs == [NEW_ENV]
    rm = spec.get_resource_manager(PlaygroundItem)
    item = rm.get(item_id).data
    assert isinstance(item, PlaygroundItem)
    assert item.env_vars == NEW_ENV
    assert rm.get_meta(item_id).updated_by == "system:env-refresh"


# ─── P6: telling the person (D4, D6) ─────────────────────────────────────────


def _dead() -> _Erp:
    from workspace_app.api.env_provider import RefreshRejected

    return _Erp(outcome=RefreshRejected({"ERP_TOKEN", "ERP_REFRESH"}))


def _notifications(spec) -> list:  # noqa: ANN001
    from workspace_app.resources.notification import Notification

    out = []
    for res in spec.get_resource_manager(Notification).list_resources():
        out.append(res.data)
    return out


def _thread(spec) -> list:  # noqa: ANN001
    from workspace_app.resources import Conversation

    (res,) = list(spec.get_resource_manager(Conversation).list_resources())
    conv = res.data
    assert isinstance(conv, Conversation)
    return conv.messages


def test_a_person_in_the_chat_sees_a_sign_in_message_after_their_question():
    """D6: a platform message, always shown, carrying which sign-in to open —
    under the question it came with, not above it — and no notification."""
    client, runner, item_id, spec = _send_app(None, user="alice")
    _sign_ins(client, _dead())
    PrivateEnvStore(spec).replace_personal("alice", OLD)

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    roles = [m.role for m in _thread(spec)]
    assert roles[:2] == ["user", "sign_in"]
    notice = _thread(spec)[1]
    assert notice.sign_in_provider == "erp"
    # Where the dead token lived: the button signs in back into that layer.
    assert notice.sign_in_layer == "personal"
    assert "ERP" in notice.content
    assert "old" not in notice.content and "r1" not in notice.content
    assert _notifications(spec) == []
    # The tool still ran, with the dead names gone (D4: run anyway).
    assert runner.envs == [{}]


async def test_a_goal_round_tells_the_person_by_notification():
    client, _runner, item_id, spec = _send_app(None, user="admin")
    _sign_ins(client, _dead())
    PrivateEnvStore(spec).replace_personal("alice", OLD)
    service = cast(FastAPI, client.app).state.chat_send
    rid, conv = _default_chat(spec, item_id)

    with client:
        for _ in range(2):  # the same day twice: one notification
            PrivateEnvStore(spec).replace_personal("alice", OLD)
            await service.send(
                item_id,
                rid,
                conv,
                item_id,
                _MessageBody(content="driven"),
                author="alice",
                driven_by="goal-driver",
            )

    (note,) = _notifications(spec)
    assert note.recipient == "alice"
    assert note.link == "/my-env"
    assert "ERP" in note.title
    assert "old" not in note.body and "r1" not in note.body
    assert "sign_in" not in [m.role for m in _thread(spec)]


async def test_a_notice_for_this_items_sign_in_leads_to_this_item():
    """Review round 1: signing in again belongs where the dead token lived
    (D3) — a token in this item's Private is signed in from this item, not
    from My environment variables."""
    client, _runner, item_id, spec = _send_app(None, user="admin")
    _sign_ins(client, _dead())
    PrivateEnvStore(spec).replace("alice", item_id, OLD)
    service = cast(FastAPI, client.app).state.chat_send
    rid, conv = _default_chat(spec, item_id)

    with client:
        await service.send(
            item_id,
            rid,
            conv,
            item_id,
            _MessageBody(content="driven"),
            author="alice",
            driven_by="goal-driver",
        )

    (note,) = _notifications(spec)
    assert note.link == f"/a/playground/{item_id}"
    assert "My environment variables" not in note.body


def test_a_workflow_node_tells_the_presser_by_notification():
    _executor, item_id, spec, _runner, client = _executor_app(None, user="presser-p")
    _sign_ins(client, _dead())
    PrivateEnvStore(spec).replace_personal("presser-p", OLD)

    with client:
        base = f"/a/playground/items/{item_id}"
        client.put(f"{base}/files/.workflows/nightly.json", content=_ONE_AGENT_STEP)
        assert client.post(f"{base}/wui/run", json={"workflow": "nightly"}).status_code == 200
        runs = client.get(f"{base}/runs").json()
        _poll_until_terminal(client, item_id, runs[0]["run_id"])

    recipients = [n.recipient for n in _notifications(spec) if n.link == "/my-env"]
    assert "presser-p" in recipients


def test_the_sign_in_message_never_reaches_the_model():
    from workspace_app.api.turns import history_items
    from workspace_app.context_budget import _replayed
    from workspace_app.resources.conversation import Message

    msgs = [
        Message(role="user", content="hi"),
        Message(role="sign_in", content="ERP sign-in expired", sign_in_provider="erp"),
    ]
    assert [m.role for m in _replayed(msgs)] == ["user"]
    assert all("ERP sign-in" not in str(item) for item in history_items(msgs))


# ─── a page's tool call, and its build ───────────────────────────────────────


def _page_refresher(store, loc, *providers):  # noqa: ANN001, ANN202 — test doubles
    from workspace_app.api.env_refresh import EnvRefresher
    from workspace_app.api.refresh_claims import RefreshClaims, register_refresh_claims

    spec = store._spec
    register_refresh_claims(spec)
    return EnvRefresher(
        providers=lambda: list(providers),
        store=store,
        claims=RefreshClaims(spec),
        locator=loc,
        spec=spec,
    )


def _shared_locator(env: dict[str, str]):  # noqa: ANN202 — a test double
    """The page tests' locator, holding a shared copy the refresher can write."""
    from .test_wui_routes import _Locator

    class _Writable(_Locator):
        def update_shared_env(self, item_id, patch, *, actor):  # noqa: ANN001, ANN202
            new = patch(dict(self.env))
            if new is not None:
                self.env = dict(new)
            return dict(self.env)

    return _Writable(["mes"], env=dict(env))


def test_a_page_tool_call_hands_the_tool_the_refreshed_value():
    from workspace_app.sandbox.protocol import ExecResult

    from .test_wui_routes import URL, _Locator, _private_store, _Sandbox, build

    store = _private_store()
    store.replace_personal("default-user", OLD)
    loc = _Locator(["mes"])
    sandbox = _Sandbox(ExecResult(exit_code=0, stdout=b"{}"))
    client, _, _, _ = build(
        sandbox=sandbox,
        locator=loc,
        private_env=store,
        env_refresher=_page_refresher(store, loc, _Erp()),
    )

    assert client.post(URL, json={"args": {}}).status_code == 200

    assert sandbox.envs[-1]["ERP_TOKEN"] == "new"
    assert store.personal("default-user") == NEW_ENV


def test_a_page_tool_call_gets_the_refreshed_shared_copy_on_this_call():
    """Review round 1: the item's layers are read AFTER the refresh, so the
    call that refreshed a shared token is the one that uses it — not the next."""
    from workspace_app.sandbox.protocol import ExecResult

    from .test_wui_routes import URL, _private_store, _Sandbox, build

    store = _private_store()
    loc = _shared_locator(OLD)
    sandbox = _Sandbox(ExecResult(exit_code=0, stdout=b"{}"))
    client, _, _, _ = build(
        sandbox=sandbox,
        locator=loc,
        private_env=store,
        env_refresher=_page_refresher(store, loc, _Erp()),
    )

    assert client.post(URL, json={"args": {}}).status_code == 200

    assert loc.env == NEW_ENV
    assert sandbox.envs[-1]["ERP_TOKEN"] == "new"


def test_a_dead_sign_in_on_a_page_is_told_by_notification():
    """D4: a page has no thread to show the notice in, so the person who
    pressed is told the way a person not in the chat is."""
    from workspace_app.sandbox.protocol import ExecResult

    from .test_wui_routes import URL, _Locator, _private_store, _Sandbox, build

    store = _private_store()
    store.replace_personal("default-user", OLD)
    loc = _Locator(["mes"])
    client, _, _, _ = build(
        sandbox=_Sandbox(ExecResult(exit_code=0, stdout=b"{}")),
        locator=loc,
        private_env=store,
        env_refresher=_page_refresher(store, loc, _dead()),
    )

    assert client.post(URL, json={"args": {}}).status_code == 200

    assert [n.recipient for n in _notifications(store._spec)] == ["default-user"]


def test_a_page_build_runs_as_nobody_and_refreshes_nothing():
    """plan-wui-viewer-login D11: the build carries nobody's values, so there
    is nothing of anybody's to refresh — and the deploy is not asked."""
    from .test_wui_routes import BUILD_URL, _BuildSandbox, _Locator, _private_store, build

    store = _private_store()
    store.replace_personal("default-user", OLD)
    loc = _Locator(["mes"])
    erp = _Erp()
    sandbox = _BuildSandbox([b"ok\n"])
    client, _, _, _ = build(
        sandbox=sandbox,
        locator=loc,
        private_env=store,
        env_refresher=_page_refresher(store, loc, erp),
    )

    client.post(BUILD_URL, json={"folder": "/page"})

    assert erp.checked == [] and erp.calls == []
    assert "ERP_TOKEN" not in sandbox.envs[0]


# ─── a re-run on a peer (plan-graceful-shutdown P3) ──────────────────────────


async def _rerun(sign_in: _Erp):  # noqa: ANN202
    """A re-run on a peer of alice's turn, with ``sign_in`` deployed: the env
    its tool got, and the spec."""
    from workspace_app.api import create_app
    from workspace_app.api.turn_claims import TurnClaim
    from workspace_app.filestore.memory import MemoryFileStore
    from workspace_app.resources import make_spec
    from workspace_app.sandbox.mock import MockSandbox

    from ._client import TestClient
    from .test_turn_reclaim import _append, _claims, _item_with_chat, _Runner, _service, _settle

    spec = make_spec(default_user="u")
    item_id, rid = _item_with_chat(spec)
    _append(spec, rid, "user", "hi", 1_000)
    runner = _Runner("from the peer")
    envs: list[dict[str, str]] = []
    original = runner.run

    async def spying(prompt, ctx):  # noqa: ANN001, ANN202
        envs.append(dict(ctx.user_env))
        async for ev in original(prompt, ctx):
            yield ev

    runner.run = spying  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    client = TestClient(
        create_app(
            spec=spec,
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            runner=runner,
            run_consumers=False,
            turn_reclaim_interval=None,
        )
    )
    _sign_ins(client, sign_in)
    PrivateEnvStore(spec).replace_personal("alice", OLD)
    with client:
        store = _claims(client)
        store.open(
            TurnClaim(
                key=item_id,
                created_at=1_000,
                investigation_id=item_id,
                rid=rid,
                author="alice",
                body={"content": "hi"},
            )
        )
        (row,) = store.list_open()
        await _service(client).rerun(row)
        await _settle(store)
    return envs, spec


async def test_a_rerun_refreshes_for_its_author():
    envs, _spec = await _rerun(_Erp())
    assert envs == [NEW_ENV]


async def test_a_rerun_tells_its_author_by_notification():
    """Nobody is in the thread a re-run continues on a peer, so a dead sign-in
    is a notification — the same as a goal round."""
    _envs, spec = await _rerun(_dead())
    assert [n.recipient for n in _notifications(spec)] == ["alice"]
    assert "sign_in" not in [m.role for m in _thread(spec)]
