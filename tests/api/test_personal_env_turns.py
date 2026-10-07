"""My environment variables reach the tools (`docs/plan-personal-env.md`, P3).

Every entry point that runs a tool FOR a person hands it that person's values
for every item — but only for a name the item's policy asks for as personal
(Private first / Private only, D2). Who the person is does not change (D7): the
sender, the presser, the setter of a goal, the binder of a schedule. Their
values for THIS item still win a name (D4), and the deploy's service account
ranks below them.
"""

from __future__ import annotations

from typing import cast

import msgspec
from fastapi import FastAPI

from workspace_app.api.private_env import PrivateEnvStore, unattended_layer
from workspace_app.api.schemas import _MessageBody
from workspace_app.apps.playground.model import PlaygroundItem

from .test_headless_env import (
    _ONE_AGENT_STEP,
    ServiceAccountEnv,
    _executor_app,
    _poll_until_terminal,
)
from .test_request_env import _default_chat, _send_app


def _policy(spec, item_id: str, **rules: str) -> None:
    rm = spec.get_resource_manager(PlaygroundItem)
    item = rm.get(item_id).data
    assert isinstance(item, PlaygroundItem)
    rm.update(item_id, msgspec.structs.replace(item, env_policy=dict(rules)))


def _send(client, item_id: str, text: str = "hi") -> None:
    resp = client.post(f"/a/playground/items/{item_id}/messages", json={"content": text})
    assert resp.status_code == 202


# ─── a chat send: the sender ────────────────────────────────────────────────


def test_my_variable_reaches_my_turn_when_the_item_asks_for_a_personal_value():
    client, runner, item_id, spec = _send_app(None, user="alice")
    _policy(spec, item_id, ERP="private_first", MES="private_only")
    PrivateEnvStore(spec).replace_personal("alice", {"ERP": "a-erp", "MES": "a-mes"})

    with client:
        _send(client, item_id)

    assert runner.envs == [{"ERP": "a-erp", "MES": "a-mes"}]


def test_an_item_that_never_asked_does_not_get_it():
    """D2: no policy, or Shared — not passed, even with nothing else supplying it."""
    client, runner, item_id, spec = _send_app(None, user="alice")
    _policy(spec, item_id, MES="shared_first")
    PrivateEnvStore(spec).replace_personal("alice", {"ERP": "a-erp", "MES": "a-mes"})

    with client:
        _send(client, item_id)

    assert runner.envs == [{}]


def test_this_items_own_value_beats_mine():
    """D4: a value set for this item is the deliberate exception."""
    client, runner, item_id, spec = _send_app(None, user="alice")
    _policy(spec, item_id, ERP="private_first")
    store = PrivateEnvStore(spec)
    store.replace_personal("alice", {"ERP": "everywhere"})
    store.replace("alice", item_id, {"ERP": "just-here"})

    with client:
        _send(client, item_id)

    assert runner.envs == [{"ERP": "just-here"}]


def test_a_new_value_reaches_the_next_turn():
    """The point of the whole plan: sign in again in one place, and the next
    thing that runs uses it."""
    client, runner, item_id, spec = _send_app(None, user="alice")
    _policy(spec, item_id, ERP="private_only")
    store = PrivateEnvStore(spec)

    with client:
        store.replace_personal("alice", {"ERP": "old"})
        _send(client, item_id, "one")
        store.replace_personal("alice", {"ERP": "new"})
        _send(client, item_id, "two")

    assert [e["ERP"] for e in runner.envs] == ["old", "new"]


# ─── a page's tool call: the presser ────────────────────────────────────────


def test_a_page_tool_call_carries_the_pressers_variables():
    from workspace_app.sandbox.protocol import ExecResult

    from .test_wui_routes import URL, _private_store, _Sandbox, build

    store = _private_store()
    store.replace_personal("default-user", {"ERP_TOKEN": "mine", "NOT_ASKED": "x"})
    sandbox = _Sandbox(ExecResult(exit_code=0, stdout=b"{}"))
    client, _, _, _ = build(
        sandbox=sandbox, env_policy={"ERP_TOKEN": "private_only"}, private_env=store
    )

    assert client.post(URL, json={"args": {}}).status_code == 200

    assert sandbox.envs[-1]["ERP_TOKEN"] == "mine"
    assert "NOT_ASKED" not in sandbox.envs[-1]


# ─── nobody at the request: still the same person (D7) ──────────────────────


async def test_a_goal_round_uses_its_setters_variables_and_nobody_elses():
    client, runner, item_id, spec = _send_app(None, user="admin")
    _policy(spec, item_id, ERP="private_only")
    PrivateEnvStore(spec).replace_personal("alice", {"ERP": "alice"})
    service = cast(FastAPI, client.app).state.chat_send
    rid, conv = _default_chat(spec, item_id)

    with client:
        for who in ("alice", "bob"):
            await service.send(
                item_id,
                rid,
                conv,
                item_id,
                _MessageBody(content="driven"),
                author=who,
                driven_by="goal-driver",
            )

    assert runner.envs == [{"ERP": "alice"}, {}]


def test_a_page_button_run_uses_the_pressers_variables_over_the_service_account():
    """The service account (`env_without_request`) is the deploy's answer for a
    turn nobody is at; the person it runs for outranks it with their own
    credential — theirs is what they would have used."""
    seam = ServiceAccountEnv()
    _executor, item_id, spec, runner, client = _executor_app(
        seam, owner="owner-o", user="presser-p"
    )
    _policy(spec, item_id, SA_TOKEN="private_first")
    store = PrivateEnvStore(spec)
    store.replace_personal("presser-p", {"SA_TOKEN": "presser"})
    store.replace_personal("owner-o", {"SA_TOKEN": "owner"})

    with client:
        base = f"/a/playground/items/{item_id}"
        client.put(f"{base}/files/.workflows/nightly.json", content=_ONE_AGENT_STEP)
        assert client.post(f"{base}/wui/run", json={"workflow": "nightly"}).status_code == 200
        runs = client.get(f"{base}/runs").json()
        assert _poll_until_terminal(client, item_id, runs[0]["run_id"]) == "done"

    assert runner.envs == [{"SA_TOKEN": "presser"}]


async def test_someone_who_lost_the_item_lends_no_variables():
    """The gate an unattended path asks before using anyone's values: removed
    from the item, they stop lending them — the per-item ones and these."""
    from workspace_app.api.private_env import register_private_env
    from workspace_app.resources import make_spec

    spec = make_spec(default_user="x")
    register_private_env(spec)
    store = PrivateEnvStore(spec, may=lambda _u, _i, _v: False)
    store.replace_personal("alice", {"ERP": "a"})
    store.replace("alice", "i1", {"OWN": "o"})

    person = await unattended_layer(
        store, headless={"SA": "s"}, acting_for="alice", item_id="i1", verb="execute"
    )

    assert (person.own, person.personal, person.service) == ({}, {}, {"SA": "s"})
