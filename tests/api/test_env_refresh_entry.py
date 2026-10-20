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


# ─── a page's tool call, and its build ───────────────────────────────────────


def _page_refresher(store, loc, *providers):  # noqa: ANN001, ANN202 — test doubles
    from workspace_app.api.env_refresh import EnvRefresher
    from workspace_app.api.refresh_claims import RefreshClaims, register_refresh_claims

    spec = store._spec
    register_refresh_claims(spec)
    return EnvRefresher(
        providers=lambda: list(providers), store=store, claims=RefreshClaims(spec), locator=loc
    )


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


async def test_a_rerun_refreshes_for_its_author():
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
    _sign_ins(client, _Erp())
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
    assert envs == [NEW_ENV]
