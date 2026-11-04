"""P1 of `docs/plan-wui-publish-and-agent-hub.md`: the published record and its
snapshot.

Publishing a WUI or an agent records one `Published` row and, for every kind
that hands out copies, archives the whole workspace as it stands — minus what a
copy must never carry (C3) — into a namespace of its own, charged to the
publisher (C10). Real `create_app` + a real spec + a real in-memory file store,
because what is being pinned is what the snapshot HOLDS.
"""

from __future__ import annotations

import asyncio

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.publish import Publications
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.quota.disk_ledger import DiskLedger
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient


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


def _item(spec, *, by: str, permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(by):
        return rm.create(
            RcaInvestigation(title="Line 3", owner=by, permission=permission)
        ).resource_id


def _wp(iid: str, suffix: str = "") -> str:
    return f"/a/rca/items/{iid}{suffix}"


PAGE = "/pages/report/page.ai.yaml"
AGENT = "/agents/sales/agent.ai.yaml"


def _put(client, iid: str, path: str, body: bytes) -> None:
    assert client.put(_wp(iid, f"/files{path}"), content=body).status_code == 204, path


def _workspace(client, iid: str) -> None:
    _put(client, iid, PAGE, b"view: wui\ntitle: Shipping board\n")
    _put(client, iid, AGENT, b"view: agent\ntitle: Sales helper\ninstructions: Help with orders.\n")
    _put(client, iid, "/data/orders.csv", b"id,qty\n1,2\n")
    _put(client, iid, "/.skill/report/SKILL.md", b"---\nname: report\ndescription: d\n---\nbody\n")
    # What a copy must never carry (C3): run journals and every schedules file.
    _put(client, iid, "/.workflow/run-1/journal.json", b"{}")
    _put(client, iid, "/.workflows/schedules.json", b"[]")
    _put(client, iid, "/pages/report/schedules.json", b"[]")
    _put(client, iid, "/.workflows/nightly.json", b"{}")


def _publish(client, iid: str, **body):
    return client.post(_wp(iid, "/publish"), json=body)


def _files_in(store: MemoryFileStore, namespace: str) -> list[str]:
    """What the in-memory store holds in one namespace — read directly: its
    lock belongs to the app's event loop, not this test's."""
    return sorted(store._files.get(namespace, {}))


def _published_namespaces(store: MemoryFileStore) -> list[str]:
    return sorted(ns for ns, files in store._files.items() if files and ns.startswith("published:"))


def _snapshot_files(spec, store, iid: str, path: str) -> list[str]:
    row = Publications(spec).get(iid, path)
    assert row is not None
    return _files_in(store, row.snapshot)


def test_publishing_a_wui_snapshots_the_whole_workspace_minus_journals_and_schedules():
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)

    r = _publish(client, iid, path=PAGE, sharing="per_person", reach="me")

    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "wui"
    assert _snapshot_files(spec, store, iid, PAGE) == [
        "/.skill/report/SKILL.md",
        "/.workflows/nightly.json",
        AGENT,
        "/data/orders.csv",
        PAGE,
    ]


def test_an_agent_ai_yaml_publishes_an_agent_and_snapshots_the_same_whole_workspace():
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)

    r = _publish(client, iid, path=AGENT, sharing="per_conversation", reach="me")

    assert r.status_code == 200, r.text
    assert (r.json()["kind"], r.json()["title"]) == ("agent", "Sales helper")
    assert "/data/orders.csv" in _snapshot_files(spec, store, iid, AGENT)


@pytest.mark.parametrize(
    ("path", "sharing"),
    [
        # A2: an agent is one copy per conversation, fixed.
        (AGENT, "per_person"),
        (AGENT, "direct"),
        # A WUI has no "per conversation": that is an agent's.
        (PAGE, "per_conversation"),
    ],
)
def test_each_kind_takes_only_its_own_ways_of_sharing_copies(path, sharing):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)

    r = _publish(client, iid, path=path, sharing=sharing, reach="me")

    assert r.status_code == 400, r.text
    assert Publications(spec).get(iid, path) is None


def test_a_wui_publish_without_a_way_of_sharing_is_refused_there_is_no_default():
    """W2: publishing always asks; a body that did not answer is not published."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)

    r = _publish(client, iid, path=PAGE, reach="me")

    assert r.status_code == 422, r.text


def test_use_my_workspace_directly_takes_no_snapshot():
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)

    r = _publish(client, iid, path=PAGE, sharing="direct", reach="me")

    assert r.status_code == 200, r.text
    row = Publications(spec).get(iid, PAGE)
    assert row is not None and row.snapshot == ""
    assert _published_namespaces(store) == []


def test_a_file_that_is_neither_a_wui_nor_an_agent_is_not_published():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")
    _put(client, iid, "/board.ai.yaml", b"view: board\n")

    r = _publish(client, iid, path="/board.ai.yaml", sharing="per_person", reach="me")

    assert r.status_code == 400, r.text


def test_publishing_needs_change_permission_edit_content_is_not_enough():
    """C9: publishing hands the workspace — Shared values included — to a group
    of people; deciding who gets what this item holds is change_permission's."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    perm = Permission(
        visibility="restricted",
        read_meta=["user:carol"],
        read_content=["user:carol"],
        edit_content=["user:carol"],
    )
    iid = _item(spec, by="bob", permission=perm)
    _workspace(client, iid)
    holder["id"] = "carol"

    r = _publish(client, iid, path=PAGE, sharing="per_person", reach="me")

    assert r.status_code == 403, r.text
    assert Publications(spec).get(iid, PAGE) is None


def test_republishing_keeps_only_the_latest_snapshot():
    """C7/C10: only the latest version is kept — a re-publish replaces the
    snapshot, and the old one is gone from the store, not left to be paid for."""
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)
    assert _publish(client, iid, path=PAGE, sharing="per_person", reach="me").status_code == 200
    first = Publications(spec).get(iid, PAGE)
    assert first is not None
    _put(client, iid, "/data/new.csv", b"x\n")

    assert _publish(client, iid, path=PAGE, sharing="per_person", reach="me").status_code == 200

    second = Publications(spec).get(iid, PAGE)
    assert second is not None and second.snapshot != first.snapshot
    assert _files_in(store, first.snapshot) == []
    assert "/data/new.csv" in _files_in(store, second.snapshot)
    assert _published_namespaces(store) == [second.snapshot]


def test_the_snapshot_is_charged_to_the_publisher():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)

    assert _publish(client, iid, path=PAGE, sharing="per_person", reach="me").status_code == 200

    row = Publications(spec).get(iid, PAGE)
    assert row is not None and row.bytes > 0
    charged = dict(asyncio.run(DiskLedger(spec).per_item_for("bob")))
    assert charged[row.snapshot] == row.bytes


def test_unpublishing_removes_the_snapshot_and_its_charge():
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)
    assert _publish(client, iid, path=PAGE, sharing="per_person", reach="me").status_code == 200
    row = Publications(spec).get(iid, PAGE)
    assert row is not None

    r = client.delete(_wp(iid, "/publish"), params={"path": PAGE})

    assert r.status_code == 204, r.text
    assert Publications(spec).get(iid, PAGE) is None
    assert _files_in(store, row.snapshot) == []
    assert row.snapshot not in dict(asyncio.run(DiskLedger(spec).per_item_for("bob")))


def test_the_estimate_counts_what_a_snapshot_would_hold_and_names_the_biggest_folders():
    """C11: file count, size, and the folders that hold most of it — the same
    exclusions as the snapshot, so the warning describes what would be copied."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)
    for n in range(3):
        _put(client, iid, f"/node_modules/pkg/f{n}.js", b"x" * 100)

    r = client.get(_wp(iid, "/publish/estimate"), params={"path": PAGE})

    assert r.status_code == 200, r.text
    est = r.json()
    assert est["files"] == 8
    assert est["biggest"][0] == {"folder": "/node_modules", "files": 3, "bytes": 300}
    assert est["bytes"] == sum(f["bytes"] for f in est["biggest"])


def test_the_disk_reconcile_keeps_a_current_snapshots_charge_and_drops_a_stale_one():
    """The ledger's reconcile forgets every row that is not an item — a
    snapshot is not one, so without being told it would erase the charge C10
    puts on the publisher. A snapshot no row points at any more owes nothing."""
    from workspace_app.quota.disk_reconcile import make_disk_ledger_pass

    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)
    assert _publish(client, iid, path=PAGE, sharing="per_person", reach="me").status_code == 200
    row = Publications(spec).get(iid, PAGE)
    assert row is not None
    stale = "published:gone:1"
    ledger = DiskLedger(spec)
    asyncio.run(ledger.record(stale, "bob", 50))

    run = make_disk_ledger_pass(spec, store, live_window_ms=60_000)
    assert run is not None
    asyncio.run(run())

    charged = dict(asyncio.run(ledger.per_item_for("bob")))
    assert charged.get(row.snapshot) == row.bytes
    assert stale not in charged


def test_a_missing_view_file_is_not_published():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")

    r = _publish(client, iid, path="/nowhere/page.ai.yaml", sharing="per_person", reach="me")

    assert r.status_code == 400, r.text


def test_unpublishing_what_was_never_published_is_not_an_error():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")

    assert client.delete(_wp(iid, "/publish"), params={"path": PAGE}).status_code == 204


def test_release_notes_accumulate_across_republishes():
    """A9: each re-publish may say what changed; the earlier notes stay."""
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    iid = _item(spec, by="bob")
    _workspace(client, iid)
    for note in ("first", "", "  third  "):
        r = _publish(client, iid, path=AGENT, sharing="per_conversation", reach="me", note=note)
        assert r.status_code == 200, r.text

    row = Publications(spec).get(iid, AGENT)
    assert row is not None
    assert [(n.by, n.text) for n in row.notes] == [("bob", "first"), ("bob", "third")]


def test_publications_list_by_kind_and_by_item():
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    one, two = _item(spec, by="bob"), _item(spec, by="bob")
    for iid in (one, two):
        _workspace(client, iid)
        assert _publish(client, iid, path=PAGE, sharing="direct", reach="me").status_code == 200
    assert (
        _publish(client, one, path=AGENT, sharing="per_conversation", reach="me").status_code == 200
    )
    pubs = Publications(spec)

    assert {(r.item_id, r.kind) for r in pubs.newest_first(kind="agent")} == {(one, "agent")}
    assert {r.kind for r in pubs.newest_first(item_id=one)} == {"wui", "agent"}
    assert [r.kind for r in pubs.newest_first(kind="wui", item_id=two)] == ["wui"]


def test_only_a_snapshot_namespace_can_be_a_held_snapshot():
    holder = {"id": "bob"}
    _client, spec, _store = _app(holder)

    assert Publications(spec).holds_snapshot("rca-investigation:x") is False


def test_a_file_that_vanishes_while_the_snapshot_is_taken_is_skipped():
    """The workspace is live: a file listed and then deleted before its read
    is simply not in the snapshot, and the rest still is."""
    from workspace_app.api.publish import take_snapshot
    from workspace_app.filestore.protocol import FileNotFound

    class _Live:
        async def stat_all(self, _ws, _prefix=""):
            return [("/kept.txt", 2), ("/gone.txt", 2)]

        async def read_to_file(self, _ws, path, dest):
            if path == "/gone.txt":
                raise FileNotFound(path)
            dest.write_bytes(b"ok")

    store = MemoryFileStore()
    count, size = asyncio.run(take_snapshot(_Live(), store, "ws", "published:x:1"))  # ty: ignore[invalid-argument-type]

    assert (count, size) == (1, 2)
    assert _files_in(store, "published:x:1") == ["/kept.txt"]


@pytest.mark.parametrize(
    ("path", "excluded"),
    [
        ("/.readonly/skills/x/SKILL.md", True),
        ("/.workflow/run-1/journal.json", True),
        ("/.workflows/schedules.json", True),
        ("/pages/report/schedules.json", True),
        ("/.workflows/nightly.json", False),
        ("/.skill/x/SKILL.md", False),
        ("/node_modules/pkg/index.js", False),
    ],
)
def test_what_a_snapshot_leaves_out(path, excluded):
    """C3, by path: the platform's read-only mount is not the item's, journals
    are a run's own history, and a copied schedule would fire N times."""
    from workspace_app.api.publish import excluded_from_snapshot

    assert excluded_from_snapshot(path) is excluded
