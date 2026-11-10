"""docs/plan-item-disk-size.md P1 — a person sets how big ONE item's workspace
may grow, from its environment panel.

Driven through the doors a person uses: the disk route, then a file write (the
gate every write shares, `WorkspaceFiles._ensure_headroom`) — not by asking
`_quota_for` what it returns. The point of the setting is whether a write is
refused, and a test that stopped at the resolver would pass while the number
never reached the gate.
"""

from __future__ import annotations

from workspace_app.config.schema import PerUserResources
from workspace_app.perm.model import Permission
from workspace_app.quota.limits import ResourceLimits

from .test_item_resources import WHO, _app, _mk, _restricted, _wake

#: The App's size is 100 bytes; the deploy lets one item go up to 1000.
SMALL = ResourceLimits(cpu_cores=None, memory_bytes=None, disk_bytes=100, disk_max=1000)


def _write(client, item: str, n: int, name: str = "f.bin"):  # noqa: ANN001, ANN202
    return client.put(f"/a/rca/items/{item}/files/{name}", content=b"x" * n)


def _set(client, item: str, size: int | str | None):  # noqa: ANN001, ANN202
    """A size the way the panel sends it: a string (`"500"` is 500 bytes)."""
    return client.put(
        f"/a/rca/items/{item}/disk", json={"disk": None if size is None else str(size)}
    )


def test_an_item_nobody_sized_keeps_its_apps_disk():
    """D7: nothing changes for an item until someone sets it."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")

        assert _write(client, item, 150).status_code == 507


def test_an_item_may_be_given_more_disk_up_to_the_deploys_max():
    """D3, and D5: the next write already uses it — no environment restart."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")

        assert _set(client, item, 500).status_code == 200
        assert _write(client, item, 150).status_code == 204


def test_above_the_max_is_kept_as_typed_and_held_at_the_max():
    """Clamped on the way out, never on the way in — the CPU rule."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")

        assert _set(client, item, 5000).status_code == 200
        env = client.get(f"/a/rca/items/{item}/environment").json()

        assert (env["stated_disk_bytes"], env["effective_disk_bytes"]) == (5000, 1000)
        assert _write(client, item, 1500).status_code == 507


def test_no_max_means_no_raising_above_the_app():
    """D3: a deploy that set no `max.disk` keeps the App's size as the ceiling."""
    no_max = ResourceLimits(cpu_cores=None, memory_bytes=None, disk_bytes=100)
    with _app(PerUserResources(), app_resources={"rca": no_max}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        _set(client, item, 500)

        assert _write(client, item, 150).status_code == 507


def test_below_what_is_already_used_is_refused():
    """D1: setting it under the files already there would lock the workspace
    the moment it is saved. Refused, and nothing is stored."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        assert _write(client, item, 80).status_code == 204

        refused = _set(client, item, 50)

        assert refused.status_code == 422, refused.text
        assert client.get(f"/a/rca/items/{item}/environment").json()["stated_disk_bytes"] is None


def test_exactly_what_is_used_is_refused_too():
    """Review round 1: a workspace AT its limit is full — the turn gate asks for
    room for one more byte — so equal locks it as surely as below."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        _write(client, item, 80)

        assert _set(client, item, 80).status_code == 422
        assert _set(client, item, 81).status_code == 200


def test_the_floor_is_checked_against_what_will_apply_not_what_was_typed():
    """Review round 1: a typed size is held to the ceiling on the way out. With
    more already used than the ceiling, a size above `used` still APPLIES as the
    ceiling — below `used` — and would lock the workspace."""
    unlimited_app = ResourceLimits(cpu_cores=None, memory_bytes=None, disk_bytes=0, disk_max=1000)
    with _app(PerUserResources(), app_resources={"rca": unlimited_app}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        assert _write(client, item, 1500).status_code == 204

        assert _set(client, item, 5000).status_code == 422
        assert _write(client, item, 10, name="g.bin").status_code == 204, "still unlimited"


def test_a_body_without_the_field_is_refused_rather_than_clearing_it():
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        _set(client, item, 500)

        assert client.put(f"/a/rca/items/{item}/disk", json={}).status_code == 422
        env = client.get(f"/a/rca/items/{item}/environment").json()
        assert env["stated_disk_bytes"] == 500


def test_the_environment_still_opens_when_the_workspace_cannot_be_measured(monkeypatch):
    """Review round 1: measuring is extra — a busy or unreachable sandbox must
    not take the cpu/memory half and Close sandbox down with it. The disk half
    is left out (None), and the panel then draws no disk field."""
    from workspace_app.files.facade import WorkspaceFiles

    async def busy(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("sandbox busy")

    # Before the app is built: it holds the bound method.
    monkeypatch.setattr(WorkspaceFiles, "workspace_usage", busy)
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")

        r = client.get(f"/a/rca/items/{item}/environment")

        assert r.status_code == 200, r.text
        assert r.json()["disk_used_bytes"] is None


def test_a_nonsense_size_is_refused():
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")

        for bad in ("0", "max", "-5", "lots"):
            assert _set(client, item, bad).status_code == 422, bad


def test_it_may_change_while_the_environment_runs():
    """D5: disk is not the sandbox's — it is checked on every write, so a
    running environment does not hold it the way it holds cpu and memory."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        _wake(client, item)

        assert _set(client, item, 500).status_code == 200


def test_clearing_it_goes_back_to_the_apps_size():
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        _set(client, item, 500)

        assert _set(client, item, None).status_code == 200
        assert _write(client, item, 150).status_code == 507


def test_setting_it_needs_more_than_editing_the_item():
    """D4: `change_permission`, the verb the AI can never hold."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(
            spec,
            "owner-alice",
            # bob is in the workspace and may edit the item — every verb but
            # change_permission — so only that verb can be what refuses him.
            permission=_restricted(
                read_meta=["user:bob"], read_chat=["user:bob"], write_meta=["user:bob"]
            ),
        )

        WHO["id"] = "bob"
        refused = _set(client, item, 500)

        assert refused.status_code in (403, 404), refused.text


def test_a_delegate_with_change_permission_may_set_it():
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(
            spec,
            "owner-alice",
            permission=_restricted(
                read_meta=["user:bob"], read_chat=["user:bob"], change_permission=["user:bob"]
            ),
        )

        WHO["id"] = "bob"

        assert _set(client, item, 500).status_code == 200


def test_no_other_door_writes_it():
    """The field is privileged like the cpu and memory beside it: the generic
    PATCH, a JSON patch and a create may not state it."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))

        WHO["id"] = "carol"
        smuggled = client.patch(f"/rca-investigation/{item}", json={"sandbox_disk_bytes": 999})
        assert smuggled.status_code == 403, smuggled.text

        WHO["id"] = "alice"
        created = client.post(
            "/rca-investigation", json={"title": "t", "owner": "alice", "sandbox_disk_bytes": 999}
        )
        assert created.status_code == 403, created.text


def test_the_apps_own_create_route_cannot_state_it_either():
    """`POST /a/{slug}/items` merges its body into the model: the field is
    dropped there, as cpu and memory are."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        made = client.post("/a/rca/items", json={"title": "x", "sandbox_disk_bytes": 999})
        assert made.status_code in (200, 201), made.text
        item = made.json().get("item_id") or made.json()["resource_id"]

        assert client.get(f"/a/rca/items/{item}/environment").json()["stated_disk_bytes"] is None


def test_the_environment_says_used_and_how_far_it_may_go():
    """D6: the panel draws the range before anything is typed."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        _write(client, item, 40)

        env = client.get(f"/a/rca/items/{item}/environment").json()

        assert env["disk_used_bytes"] == 40
        assert env["disk_ceiling"] == 1000
        assert env["effective_disk_bytes"] == 100


def test_the_owner_sees_how_much_of_their_own_quota_is_left():
    """D6: a ceiling is not a guarantee (D2), so what is left must be visible."""
    with _app(PerUserResources(disk="1000"), app_resources={"rca": SMALL}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(spec, "alice")
        other = _mk(spec, "alice")
        _write(client, item, 40)
        _write(client, other, 60)

        env = client.get(f"/a/rca/items/{item}/environment").json()

        assert env["owner_disk_left"] == 900


def test_a_collaborator_is_not_told_the_owners_total():
    """The environment view never leaks the owner's other items — what is left
    of their quota is a sum over those items."""
    with _app(PerUserResources(disk="1000"), app_resources={"rca": SMALL}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(
            spec,
            "owner-alice",
            permission=_restricted(read_meta=["user:bob"], read_chat=["user:bob"]),
        )

        WHO["id"] = "bob"
        env = client.get(f"/a/rca/items/{item}/environment").json()

        assert env["owner_disk_left"] is None


def test_a_new_size_reaches_the_very_next_write():
    """The gate reads the item's facts through a few-second cache; the person
    who just saved is the one who would meet the stale copy. A write BEFORE
    the change fills that cache."""
    with _app(PerUserResources(), app_resources={"rca": SMALL}) as (client, spec, _sb):
        item = _mk(spec, "alice")
        assert _write(client, item, 10, name="a.bin").status_code == 204

        _set(client, item, 500)

        assert _write(client, item, 150, name="b.bin").status_code == 204
