"""P3 of `docs/plan-wui-publish-and-agent-hub.md`: reach and lifecycle.

Who may open a publication is the publisher's to say (C9) — themselves,
named people or groups, or everyone signed in — from any App (C14). A
publication follows its source: deleting the source unpublishes it (copies
stay theirs, C13), and whoever holds change_permission on the source manages
it, publisher or not. A copy published again says what it is based on (C14).
"""

from __future__ import annotations

import asyncio

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.publish import Publications
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.resolve import find_work_item
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.quota.disk_ledger import DiskLedger
from workspace_app.resources import make_spec
from workspace_app.resources.groups import Group
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

PAGE = "/pages/report/page.ai.yaml"


def _app(holder: dict[str, str], *, superusers=frozenset()):
    spec = make_spec(default_user=lambda: holder["id"], superusers=superusers)
    store = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=store,
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        superusers=superusers,
    )
    return TestClient(app), spec, store


def _source(spec, *, by: str = "bob", permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(
            RcaInvestigation(title="Line 3", owner=by, permission=permission)
        ).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _page(client, iid: str) -> None:
    r = client.put(_wp(iid, f"/files{PAGE}"), content=b"view: wui\ntitle: Shipping board\n")
    assert r.status_code == 204


def _publish(client, iid: str, **body):
    return client.post(_wp(iid, "/publish"), json={"path": PAGE, **body})


def _open(client, iid: str):
    return client.post("/published/open", json={"item_id": iid, "path": PAGE})


def _published_by(holder, client, spec, *, permission=None, **body) -> str:
    holder["id"] = "bob"
    src = _source(spec, permission=permission)
    _page(client, src)
    r = _publish(client, src, **body)
    assert r.status_code == 200, r.text
    return src


@pytest.mark.parametrize("who", ["amy", "carl"])
def test_for_the_publisher_alone_nobody_else_may_open_it(who):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _published_by(holder, client, spec, sharing="per_person", reach="me")
    holder["id"] = who

    assert _open(client, src).status_code == 404


def test_the_publisher_may_open_their_own():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _published_by(holder, client, spec, sharing="per_person", reach="me")

    assert _open(client, src).status_code == 200


def test_named_people_may_open_it_and_nobody_else():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _published_by(
        holder, client, spec, sharing="per_person", reach="subjects", subjects=["user:amy"]
    )

    holder["id"] = "amy"
    assert _open(client, src).status_code == 200
    holder["id"] = "carl"
    assert _open(client, src).status_code == 404


def test_a_named_group_lets_its_members_open_it():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    grm = spec.get_resource_manager(Group)
    with grm.using("bob"):
        gid = grm.create(Group(name="sales", members=["amy"])).resource_id
    src = _published_by(
        holder, client, spec, sharing="per_person", reach="subjects", subjects=[f"group:{gid}"]
    )

    holder["id"] = "amy"
    assert _open(client, src).status_code == 200
    holder["id"] = "carl"
    assert _open(client, src).status_code == 404


def test_everyone_signed_in_may_open_it_from_any_app():
    """C14: opening asks nothing of the opener's own Apps or items."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _published_by(holder, client, spec, sharing="per_person", reach="everyone")
    holder["id"] = "zoe"

    assert _open(client, src).status_code == 200


def test_a_superuser_may_open_any():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder, superusers=frozenset({"root"}))
    src = _published_by(holder, client, spec, sharing="per_person", reach="me")
    holder["id"] = "root"

    assert _open(client, src).status_code == 200


def test_use_my_workspace_directly_opens_only_for_who_may_read_the_source():
    """W2's "direct" lands people IN the source, so the source's own
    permission is the gate — reach cannot open what the item does not."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _published_by(
        holder,
        client,
        spec,
        permission=Permission(visibility="private"),
        sharing="direct",
        reach="everyone",
    )
    holder["id"] = "amy"

    assert _open(client, src).status_code == 404


def test_deleting_the_source_unpublishes_and_leaves_the_copies():
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    src = _published_by(holder, client, spec, sharing="per_person", reach="everyone")
    row = Publications(spec).get(src, PAGE)
    assert row is not None
    holder["id"] = "amy"
    copy_id = _open(client, src).json()["item_id"]
    holder["id"] = "bob"

    assert client.delete(_wp(src)).status_code in (200, 204)

    assert Publications(spec).get(src, PAGE) is None
    assert not store._files.get(row.snapshot)
    assert row.snapshot not in dict(asyncio.run(DiskLedger(spec).per_item_for("bob")))
    assert find_work_item(spec, copy_id) is not None


def test_whoever_holds_change_permission_manages_it_not_only_the_publisher():
    """C13: the publisher losing access does not take it down; the people who
    now decide who gets what the item holds manage it."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    perm = Permission(
        visibility="restricted",
        read_meta=["user:dana"],
        read_content=["user:dana"],
        change_permission=["user:dana"],
    )
    src = _published_by(holder, client, spec, permission=perm, sharing="per_person", reach="me")
    holder["id"] = "dana"

    assert client.delete(_wp(src, "/publish"), params={"path": PAGE}).status_code == 204
    assert Publications(spec).get(src, PAGE) is None


def test_a_copy_published_again_says_what_it_is_based_on():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _published_by(holder, client, spec, sharing="per_person", reach="everyone")
    holder["id"] = "amy"
    copy_id = _open(client, src).json()["item_id"]

    r = client.post(
        _wp(copy_id, "/publish"), json={"path": PAGE, "sharing": "per_person", "reach": "me"}
    )

    assert r.status_code == 200, r.text
    assert (r.json()["based_on_title"], r.json()["based_on_by"]) == ("Shipping board", "bob")
