"""Viewer login (`docs/plan-wui-viewer-login.md`) — the PRIVATE layer's storage.

A person's own values for one item: stored on the server (a turn re-run on
another pod after this one died has no browser to ask), keyed by (person, item)
so a token logged in for one item never reaches another item's tools, and
readable by that person alone. The routes address the CALLER's row only — there
is no user parameter — so nobody, a superuser included, can name someone
else's.
"""

from __future__ import annotations

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


def _world(**kw):
    """A client acting as whoever `holder["id"]` names, and an item bob owns that
    alice may open (read_meta) — the lowest grant that shows her the item."""
    holder = {"id": "bob"}
    spec = make_spec(default_user=lambda: holder["id"])
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        **kw,
    )
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        rid = rm.create(
            RcaInvestigation(
                title="t",
                owner="bob",
                permission=Permission(visibility="restricted", read_meta=["user:alice"]),
            )
        ).resource_id
    return TestClient(app), holder, rid, spec


def _url(rid: str) -> str:
    return f"/a/rca/items/{rid}/env/private"


def test_a_person_reads_back_what_they_stored():
    client, holder, rid, _ = _world()
    holder["id"] = "alice"

    assert client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}}).status_code == 200

    assert client.get(_url(rid)).json() == {"values": {"ERP_TOKEN": "a-1"}, "auto": {}}


def test_nobody_else_reads_it_not_even_the_items_owner():
    """The row is addressed as "mine": bob asking the same URL gets HIS row
    (empty), not alice's. There is no parameter by which to ask for hers."""
    client, holder, rid, _ = _world()
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})

    holder["id"] = "bob"

    assert client.get(_url(rid)).json() == {"values": {}, "auto": {}}


def test_a_superuser_cannot_read_it_either():
    client, holder, rid, _ = _world(superusers=frozenset({"root"}))
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})

    holder["id"] = "root"

    assert client.get(_url(rid)).json() == {"values": {}, "auto": {}}


def test_an_item_the_caller_cannot_open_stores_nothing():
    client, holder, rid, spec = _world()
    holder["id"] = "mallory"

    r = client.put(_url(rid), json={"values": {"X": "1"}})

    assert r.status_code in (403, 404)
    from workspace_app.api.private_env import PrivateEnvStore

    assert PrivateEnvStore(spec).get("mallory", rid) == {}


def test_there_is_no_raw_resource_route_to_list_everyones_rows():
    """Registered after `spec.apply`, so specstar emitted no auto-CRUD for it —
    the caller-scoped routes above are its only door."""
    client, holder, rid, _ = _world(superusers=frozenset({"root"}))
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})
    holder["id"] = "root"

    paths = {getattr(r, "path", "") for r in client.app.routes}

    assert not any("private-env" in p for p in paths)


def test_logging_out_forgets_the_row():
    client, holder, rid, spec = _world()
    holder["id"] = "alice"
    client.put(_url(rid), json={"values": {"ERP_TOKEN": "a-1"}})

    assert client.delete(_url(rid)).status_code == 204

    assert client.get(_url(rid)).json() == {"values": {}, "auto": {}}


def test_logging_out_needs_no_access_to_the_item():
    """Deleting your own row is never gated: someone who has lost access to the
    item must still be able to take their credential back out."""
    client, holder, rid, spec = _world()
    from workspace_app.api.private_env import PrivateEnvStore

    PrivateEnvStore(spec).replace("mallory", rid, {"X": "1"})
    holder["id"] = "mallory"

    assert client.delete(_url(rid)).status_code == 204
    assert PrivateEnvStore(spec).get("mallory", rid) == {}


# ─── what writes it: the deploy's IRequestEnv, on every request (P4) ───────


def test_a_senders_request_env_is_kept_in_their_private_row():
    """`plan-wui-viewer-login` Q4(b): what the deploy's `env_for` says about a
    person is written to THEIR private row, so a turn with no request behind it
    later (a re-run on another pod) still has it."""
    from workspace_app.api.private_env import PrivateEnvStore

    from .test_request_env import CookieEnv, _send_app

    client, _runner, item_id, spec = _send_app(CookieEnv())
    client.cookies.set("sso", "abc")

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    # Kept as the seam's LAST answer (its own field, replaced whole each time).
    assert PrivateEnvStore(spec).seam("u", item_id) == {"SSO": "abc", "CALLER": "u"}


def test_a_value_the_person_typed_reaches_their_turn_with_no_seam_configured():
    """The private layer is not a feature of `IRequestEnv`: a deploy with no seam
    still has people who typed their own value for an item."""
    from workspace_app.api.private_env import PrivateEnvStore

    from .test_request_env import _send_app

    client, runner, item_id, spec = _send_app(None, env_vars={"FROM_ITEM": "i"})
    PrivateEnvStore(spec).replace("u", item_id, {"MINE": "x"})

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert runner.envs == [{"MINE": "x", "FROM_ITEM": "i"}]


def test_the_seams_answer_wins_over_a_typed_value_of_the_same_name():
    """The automatic value is always current (Q5): a name the seam provides is
    the seam's, whatever was typed; a name it does not provide is the typed one."""
    from workspace_app.api.private_env import PrivateEnvStore

    from .test_request_env import CookieEnv, _send_app

    client, runner, item_id, spec = _send_app(CookieEnv())
    PrivateEnvStore(spec).replace("u", item_id, {"SSO": "typed", "TYPED": "t"})
    client.cookies.set("sso", "fresh")

    with client:
        client.post(f"/a/playground/items/{item_id}/messages", json={"content": "hi"})

    assert runner.envs[-1]["SSO"] == "fresh"
    assert runner.envs[-1]["TYPED"] == "t"


# ─── what a page needs to label a person's values (P8) ─────────────────────


def test_the_items_layers_are_readable_by_whoever_may_read_the_item():
    """The `/w/` page has the item's id but not its record. This hands it the
    two fields `read_meta` already returns on the item — the shared values and
    the per-name policy — and nothing else."""
    client, holder, rid, spec = _world()
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        item = rm.get(rid).data
        assert isinstance(item, RcaInvestigation)
        item.env_vars = {"DB_HOST": "db"}
        item.env_policy = {"ERP_TOKEN": "private_only"}
        rm.update(rid, item)
    holder["id"] = "alice"

    r = client.get(f"/a/rca/items/{rid}/env/layers")

    assert r.json() == {"shared": {"DB_HOST": "db"}, "policy": {"ERP_TOKEN": "private_only"}}


def test_the_items_layers_are_refused_to_someone_who_cannot_open_it():
    client, holder, rid, _ = _world()
    holder["id"] = "mallory"

    assert client.get(f"/a/rca/items/{rid}/env/layers").status_code in (403, 404)


# ─── "may this OTHER person still…" — asked when nobody is at the request ────


def test_user_may_answers_for_someone_other_than_the_caller():
    """Review round 1 (C2/F1): a schedule, a goal round, a re-run act FOR a person
    who is not making the request. Whether they may still use the item has to
    be asked about THEM — and answered from the item's live permission."""
    from workspace_app.api.item_authz import user_may

    _client, _holder, rid, spec = _world()

    assert user_may(spec, rid, "alice", "read_meta") is True
    assert user_may(spec, rid, "alice", "execute") is False
    assert user_may(spec, rid, "mallory", "read_meta") is False
    assert user_may(spec, "no-such-item", "alice", "read_meta") is False
    assert user_may(spec, rid, "root", "execute", superusers=frozenset({"root"})) is True


# ─── round 1 (R2/R3/R4/F8/R6): the seam's answer is its own, replaced whole ───


def _seam_app():
    from fastapi import Request

    from workspace_app.api.request_env import IRequestEnv

    from .test_request_env import _send_app

    class SsoOnly(IRequestEnv):
        async def env_for(self, request: Request, *, user_id: str, item_id: str):
            raw = request.headers.get("x-env", "")
            return dict(p.split("=", 1) for p in raw.split(",") if p)

    return _send_app(SsoOnly())


def _send(client, item_id, env: str):
    client.post(
        f"/a/playground/items/{item_id}/messages",
        json={"content": "hi"},
        headers={"x-env": env},
    )


def test_a_name_the_seam_stops_returning_stops_reaching_the_tools():
    """R2: signed out of SSO, the old session must not keep reaching tools."""
    from workspace_app.api.private_env import PrivateEnvStore

    client, runner, item_id, spec = _seam_app()
    with client:
        _send(client, item_id, "SESSION=abc")
        _send(client, item_id, "")

    assert runner.envs == [{"SESSION": "abc"}, {}]
    # And not kept for their turns with no request behind them either — those
    # read the STORED answer, which the live turn above never consults.
    assert PrivateEnvStore(spec).seam("u", item_id) == {}


def test_the_order_a_tool_sees_is_the_seams_every_time():
    """R3: the names become SANDBOX_USER_ENV_KEYS — order is visible to a tool,
    and a stored row coming back sorted changed it from the second send on."""
    client, runner, item_id, _spec = _seam_app()
    with client:
        _send(client, item_id, "Z=1,B=2")
        _send(client, item_id, "Z=1,B=2")

    assert [list(e) for e in runner.envs] == [["Z", "B"], ["Z", "B"]]


def test_a_rotated_credential_leaves_no_history_behind():
    """R4: every past value of a rotating token was kept as a revision."""
    from workspace_app.api.private_env import PrivateEnvStore, PrivateSeam, private_env_id

    _client, _holder, rid, spec = _world()
    store = PrivateEnvStore(spec)
    for i in range(5):
        store.record_seam("alice", rid, {"TOKEN": f"t{i}"})

    rm = spec.get_resource_manager(PrivateSeam)
    assert len(list(rm.list_revisions(private_env_id("alice", rid)))) == 1
    assert store.seam("alice", rid) == {"TOKEN": "t4"}


def test_writing_the_seams_answer_never_brings_back_cleared_values():
    """F8: a seam write racing a logout used to rewrite the whole pre-logout row.
    It no longer touches the typed values at all."""
    from workspace_app.api.private_env import PrivateEnvStore

    _client, _holder, rid, spec = _world()
    store = PrivateEnvStore(spec)
    store.replace("alice", rid, {"TYPED": "secret"})
    store.clear("alice", rid)

    store.record_seam("alice", rid, {"SSO": "new"})

    assert store.get("alice", rid) == {}
    assert store.seam("alice", rid) == {"SSO": "new"}


def test_logging_out_forgets_the_seams_answer_too():
    from workspace_app.api.private_env import PrivateEnvStore

    _client, _holder, rid, spec = _world()
    store = PrivateEnvStore(spec)
    store.record_seam("alice", rid, {"SSO": "s"})

    store.clear("alice", rid)

    assert store.seam("alice", rid) == {}
