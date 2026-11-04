"""P2 of `docs/plan-wui-publish-and-agent-hub.md`: making a copy.

Opening a published WUI or agent gives the opener their own workspace — a new
item in the source's App, seeded from the snapshot, private, on their quota
(C2/C6) — with the source's settings (C3), the Shared values the source holds
NOW (C4) and the publisher's sandbox size (C10). How many copies there are is
the way of sharing (W2/W3/A2).
"""

from __future__ import annotations

import asyncio

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.publish import Publications
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.resolve import find_work_item
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.quota.disk_ledger import DiskLedger
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

PAGE = "/pages/report/page.ai.yaml"
AGENT = "/agents/sales/agent.ai.yaml"


def _app(holder: dict[str, str]):
    spec = make_spec(default_user=lambda: holder["id"])
    store = MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=store,
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
    )
    return TestClient(app), spec, store


def _source(spec, *, by: str = "bob") -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        item = RcaInvestigation(
            title="Line 3",
            owner=by,
            attached_preset="qwen3-local",
            attached_tool_prefs={"exec": False},
            attached_skill_prefs={"grill-me": False},
            env_vars={"HOST": "erp-a"},
            env_policy={"HOST": "shared_first"},
        )
        return rm.create(item).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


def _put(client, iid: str, path: str, body: bytes) -> None:
    assert client.put(_wp(iid, f"/files{path}"), content=body).status_code == 204, path


def _workspace(client, iid: str) -> None:
    _put(client, iid, PAGE, b"view: wui\ntitle: Shipping board\n")
    _put(client, iid, AGENT, b"view: agent\ntitle: Sales helper\ninstructions: Help.\n")
    _put(client, iid, "/data/orders.csv", b"id,qty\n1,2\n")


def _publish(client, iid: str, path: str = PAGE, **body):
    body.setdefault("reach", "everyone")
    r = client.post(_wp(iid, "/publish"), json={"path": path, **body})
    assert r.status_code == 200, r.text
    return r.json()


def _open(client, iid: str, path: str = PAGE):
    return client.post("/published/open", json={"item_id": iid, "path": path})


def _item(spec, item_id: str):
    found = find_work_item(spec, item_id)
    assert found is not None
    return found[1]


def _files(store: MemoryFileStore, ns: str) -> list[str]:
    return sorted(store._files.get(ns, {}))


def test_opening_gives_the_opener_a_private_copy_seeded_from_the_snapshot():
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    r = _open(client, src)

    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["slug"], out["created"]) == ("rca", True)
    copy_id = out["item_id"]
    assert copy_id != src
    copy = _item(spec, copy_id)
    assert copy.owner == "amy"
    assert copy.permission is not None and copy.permission.visibility == "private"
    rm = spec.get_resource_manager(RcaInvestigation)
    assert rm.get_meta(copy_id).created_by == "amy"
    assert _files(store, copy_id) == ["/agents/sales/agent.ai.yaml", "/data/orders.csv", PAGE]


def test_a_copy_carries_the_sources_settings_and_its_origin():
    """C3: preset, tool and skill prefs, env policy and profile come along —
    "how it works" — and the copy says where it came from."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    pub = _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    copy = _item(spec, _open(client, src).json()["item_id"])

    source = _item(spec, src)
    assert copy.attached_preset == source.attached_preset
    assert copy.attached_tool_prefs == source.attached_tool_prefs
    assert copy.attached_skill_prefs == source.attached_skill_prefs
    assert copy.env_policy == source.env_policy
    assert copy.profile == source.profile
    assert (
        copy.published_from,
        copy.published_kind,
        copy.published_path,
        copy.published_title,
    ) == (
        pub["id"],
        "wui",
        PAGE,
        "Shipping board",
    )


def test_shared_values_are_read_from_the_source_when_the_copy_is_made():
    """C4: not frozen at publish — a value refreshed (or replaced) in the source
    since is the one the copy is given."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        item = rm.get(src).data
        rm.update(
            src,
            type(item)(
                **{
                    **{f: getattr(item, f) for f in item.__struct_fields__},
                    "env_vars": {"HOST": "erp-b"},
                }
            ),
        )
    holder["id"] = "amy"

    copy = _item(spec, _open(client, src).json()["item_id"])

    assert copy.env_vars == {"HOST": "erp-b"}


def test_the_copy_takes_the_publishers_sandbox_size():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person", cpu_cores=2.0, memory_bytes=2 * 1024**3)
    holder["id"] = "amy"

    copy = _item(spec, _open(client, src).json()["item_id"])

    assert (copy.sandbox_cpu_cores, copy.sandbox_memory_bytes) == (2.0, 2 * 1024**3)


def test_the_copy_is_charged_to_the_opener():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    pub = _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    copy_id = _open(client, src).json()["item_id"]

    charged = dict(asyncio.run(DiskLedger(spec).per_item_for("amy")))
    assert charged[copy_id] == pub["bytes"]


def test_one_per_person_brings_the_same_person_back_to_the_same_copy():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    first = _open(client, src).json()

    again = _open(client, src).json()
    holder["id"] = "carl"
    other = _open(client, src).json()

    assert (again["item_id"], again["created"]) == (first["item_id"], False)
    assert other["created"] is True and other["item_id"] != first["item_id"]


def test_a_new_copy_every_time_makes_one_on_every_open():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="fresh")
    holder["id"] = "amy"

    ids = {_open(client, src).json()["item_id"] for _ in range(3)}

    assert len(ids) == 3


def test_an_agent_makes_a_copy_for_every_conversation():
    """A2: one copy per conversation — opening it again is a new conversation."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, AGENT, sharing="per_conversation")
    holder["id"] = "amy"

    first, second = _open(client, src, AGENT).json(), _open(client, src, AGENT).json()

    assert first["item_id"] != second["item_id"]
    assert first["kind"] == "agent"
    copy = _item(spec, first["item_id"])
    assert (copy.published_kind, copy.published_path, copy.published_title) == (
        "agent",
        AGENT,
        "Sales helper",
    )


def test_use_my_workspace_directly_opens_the_source_itself():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="direct")
    holder["id"] = "amy"

    out = _open(client, src).json()

    assert (out["item_id"], out["created"]) == (src, False)


def test_one_shared_by_everyone_is_one_copy_in_the_publishers_name():
    """W3: the shared copy is made at publish, owned by the publisher, and
    everyone who opens it lands in that one."""
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="shared")
    row = Publications(spec).get(src, PAGE)
    assert row is not None and row.shared_copy
    shared = _item(spec, row.shared_copy)
    assert shared.owner == "bob"
    assert _files(store, row.shared_copy) == _files(store, row.snapshot)
    holder["id"] = "amy"

    a = _open(client, src).json()
    holder["id"] = "carl"
    c = _open(client, src).json()

    assert a["item_id"] == c["item_id"] == row.shared_copy
    assert a["created"] is False


def test_republishing_one_shared_copy_makes_a_new_one_and_keeps_the_old():
    """W3: what people left in the old shared copy is not deleted — it stays an
    ordinary workspace in the publisher's name."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="shared")
    old = Publications(spec).get(src, PAGE)
    assert old is not None

    _publish(client, src, sharing="shared")

    new = Publications(spec).get(src, PAGE)
    assert new is not None and new.shared_copy != old.shared_copy
    kept = find_work_item(spec, old.shared_copy)
    assert kept is not None and kept[1].owner == "bob"


def test_a_copy_made_before_a_republish_keeps_what_it_was_made_with():
    """C7: re-publishing affects only copies made afterwards."""
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    copy_id = _open(client, src).json()["item_id"]
    holder["id"] = "bob"
    _put(client, src, "/data/new.csv", b"x\n")

    _publish(client, src, sharing="per_person")

    assert "/data/new.csv" not in _files(store, copy_id)


def test_opening_what_is_not_published_is_not_found():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)

    assert _open(client, src).status_code == 404


def test_a_shared_copy_opens_to_the_named_people_with_the_role_picked():
    """W3: reach names who may use it; the role says what they may do — a
    reader may read, not converse."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="shared", reach="subjects", subjects=["user:amy"], role="reader")
    row = Publications(spec).get(src, PAGE)
    assert row is not None

    perm = _item(spec, row.shared_copy).permission

    assert perm is not None and perm.visibility == "restricted"
    assert perm.read_content == ["user:amy"]
    assert perm.converse == [] and perm.change_permission == []


def test_a_shared_copy_for_the_publisher_alone_is_private():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="shared", reach="me")
    row = Publications(spec).get(src, PAGE)
    assert row is not None

    perm = _item(spec, row.shared_copy).permission

    assert perm is not None and perm.visibility == "private"


def test_members_are_not_copied():
    """C3: who works on the source is not who works on a copy."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        src = rm.create(RcaInvestigation(title="L", owner="bob", members=["carl"])).resource_id
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    copy = _item(spec, _open(client, src).json()["item_id"])

    assert copy.members == []


def test_an_app_without_members_copies_too():
    from workspace_app.apps.playground.model import PlaygroundItem

    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    rm = spec.get_resource_manager(PlaygroundItem)
    with rm.using("bob"):
        src = rm.create(PlaygroundItem(title="P", owner="bob")).resource_id
    _put_any = lambda path, body: client.put(f"/a/playground/items/{src}/files{path}", content=body)  # noqa: E731
    assert _put_any(PAGE, b"view: wui\n").status_code == 204
    assert (
        client.post(
            f"/a/playground/items/{src}/publish",
            json={"path": PAGE, "sharing": "fresh", "reach": "everyone"},
        ).status_code
        == 200
    )
    holder["id"] = "amy"

    out = _open(client, src).json()

    assert out["slug"] == "playground" and out["created"] is True


def test_opening_a_publication_whose_source_is_gone_says_so():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        rm.permanently_delete(src)
    holder["id"] = "amy"

    assert _open(client, src).status_code == 410
