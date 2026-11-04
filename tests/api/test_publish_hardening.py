"""Round 1 of review on `docs/plan-wui-publish-and-agent-hub.md`: what a copy
and a publication must hold against.

Each case reddened on the code before its fix: a copy that ignored its
opener's disk quota (C8), two copies for one person from two tabs (W3), a
shared publication pointing at a deleted copy, a conversation copy that grew
more conversations (A2), the old Deploy route as a way round the publish
permission (C9), a publication's settings that a re-publish could not read
back (C9/C10), and the half-made things a failure left behind.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.apps.resolve import find_work_item
from workspace_app.config.schema import PerUserResources
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileNotFound
from workspace_app.perm import Permission
from workspace_app.quota.disk_ledger import DiskLedger
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .test_publish_copy import AGENT, PAGE, _open, _publish, _source, _workspace


def _app(holder, **kw):
    spec = make_spec(default_user=lambda: holder["id"])
    store = kw.pop("store", None) or MemoryFileStore()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=store,
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: holder["id"],
        **kw,
    )
    return TestClient(app), spec, store, app


def test_opening_is_refused_when_the_copy_does_not_fit_the_openers_quota():
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder, per_user_resources=PerUserResources(disk="1K"))
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="fresh")
    holder["id"] = "amy"

    codes = [_open(client, src).status_code for _ in range(40)]

    assert 507 in codes
    assert asyncio.run(DiskLedger(spec).total_for("amy")) <= 1024


def test_publishing_is_refused_when_the_snapshot_does_not_fit_the_publishers_quota():
    holder = {"id": "bob"}
    client, spec, store, _a = _app(holder, per_user_resources=PerUserResources(disk="100"))
    src = _source(spec)
    _workspace(client, src)

    r = client.post(
        f"/a/rca/items/{src}/publish", json={"path": PAGE, "sharing": "fresh", "reach": "everyone"}
    )

    assert r.status_code == 507, r.text
    assert not [ns for ns in store._files if ns.startswith("published:")]


class _Yielding(MemoryFileStore):
    async def ls(self, *a, **k):
        await asyncio.sleep(0.01)
        return await super().ls(*a, **k)


def test_two_opens_at_once_give_one_person_one_copy():
    holder = {"id": "bob"}
    client, spec, _s, app = _app(holder, store=_Yielding())
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            body = {"item_id": src, "path": PAGE}
            return await asyncio.gather(
                c.post("/api/published/open", json=body), c.post("/api/published/open", json=body)
            )

    a, b = asyncio.run(go())

    assert a.status_code == b.status_code == 200
    assert json.loads(a.text)["item_id"] == json.loads(b.text)["item_id"]
    # And a later open finds the same one.
    assert _open(client, src).json()["item_id"] == json.loads(a.text)["item_id"]


def test_a_person_who_deleted_their_copy_gets_a_new_one():
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    first = _open(client, src).json()["item_id"]
    assert client.delete(f"/a/rca/items/{first}").status_code in (200, 204)

    again = _open(client, src)

    assert again.status_code == 200
    assert find_work_item(spec, again.json()["item_id"]) is not None


def test_a_soft_deleted_copy_is_never_written_over():
    """A copy in the trash still holds its derived id and its files, for a
    restore: the next open makes a NEW copy rather than one over it."""
    holder = {"id": "bob"}
    client, spec, store, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    first = _open(client, src).json()["item_id"]
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("amy"):
        rm.delete(first)
    kept = dict(store._files[first])

    opened = _open(client, src)
    assert opened.status_code == 200, opened.text
    again = opened.json()["item_id"]

    assert again != first
    assert find_work_item(spec, again) is not None
    assert find_work_item(spec, first, include_deleted=True) is not None
    assert store._files[first] == kept


def test_a_shared_publication_whose_copy_was_deleted_is_gone_not_a_dead_link():
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="shared")
    shared = _open(client, src).json()["item_id"]
    assert client.delete(f"/a/rca/items/{shared}").status_code in (200, 204)
    holder["id"] = "amy"

    assert _open(client, src).status_code == 410


def test_a_conversation_copy_cannot_grow_a_second_conversation_through_a_test_chat():
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, AGENT, sharing="per_conversation")
    holder["id"] = "amy"
    copy = _open(client, src, AGENT).json()["item_id"]

    codes = [
        client.post(
            f"/a/rca/items/{copy}/chats", json={"title": t, "agent_path": AGENT}
        ).status_code
        for t in "abc"
    ]

    assert codes == [201, 409, 409], codes


def test_the_old_deploy_route_needs_what_publishing_needs():
    """C9: listing a page for others is a sharing decision — `change_permission`,
    the same as Publish, not `edit_content`."""
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        src = rm.create(
            RcaInvestigation(
                title="Line 3",
                owner="bob",
                permission=Permission(
                    visibility="restricted",
                    read_meta=["user:amy"],
                    read_content=["user:amy"],
                    edit_content=["user:amy"],
                ),
            )
        ).resource_id
    _workspace(client, src)
    holder["id"] = "amy"

    r = client.post(f"/a/rca/items/{src}/wui/deploy", json={"path": PAGE})

    assert r.status_code == 403, r.text


def test_a_publication_says_back_every_choice_a_re_publish_must_start_from():
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(
        client,
        src,
        sharing="per_person",
        reach="subjects",
        subjects=["user:amy"],
        cpu_cores=2,
        memory_bytes=4 * 1024**3,
    )

    r = client.get(f"/a/rca/items/{src}/publish", params={"path": PAGE})
    missing = client.get(f"/a/rca/items/{src}/publish", params={"path": "/nope/page.ai.yaml"})

    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["sharing"], got["reach"], got["subjects"]) == (
        "per_person",
        "subjects",
        ["user:amy"],
    )
    assert (got["cpu_cores"], got["memory_bytes"]) == (2, 4 * 1024**3)
    assert missing.status_code == 404
    holder["id"] = "amy"
    assert client.get(f"/a/rca/items/{src}/publish", params={"path": PAGE}).status_code in (
        403,
        404,
    )


def test_a_shared_publish_whose_copy_fails_leaves_no_snapshot_behind(monkeypatch):
    holder = {"id": "bob"}
    client, spec, store, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)

    async def boom(*_a, **_k):
        raise LookupError("the source is gone")

    monkeypatch.setattr("workspace_app.api.publish_copy.make_copy", boom)
    r = client.post(
        f"/a/rca/items/{src}/publish", json={"path": PAGE, "sharing": "shared", "reach": "everyone"}
    )

    assert r.status_code == 410, r.text
    assert not [ns for ns in store._files if ns.startswith("published:")]


class _Vanishing(MemoryFileStore):
    """The snapshot disappears under a copy that is reading it (unpublished
    meanwhile)."""

    vanish = False
    reads = 0

    async def read_to_file(self, workspace_id, path, dest):
        if self.vanish and workspace_id.startswith("published:"):
            # After the first file: the copy is half made when it notices.
            self.reads += 1
            if self.reads > 1:
                raise FileNotFound(path)
        return await super().read_to_file(workspace_id, path, dest)


def test_an_open_whose_snapshot_vanished_leaves_no_half_copy():
    holder = {"id": "bob"}
    store = _Vanishing()
    client, spec, _s, _a = _app(holder, store=store)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="fresh")
    before = set(store._files)
    store.vanish = True
    holder["id"] = "amy"

    r = _open(client, src)

    assert r.status_code == 410, r.text
    assert set(store._files) <= before


def test_a_copy_does_not_claim_the_sources_legacy_records():
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        src = rm.create(
            RcaInvestigation(title="Line 3", owner="bob", external_refs=["legacy:42"])
        ).resource_id
    _workspace(client, src)
    _publish(client, src, sharing="fresh")
    holder["id"] = "amy"

    copy = _open(client, src).json()["item_id"]

    found = find_work_item(spec, copy)
    assert found is not None
    assert found[1].external_refs == []


# ── review round 2 ─────────────────────────────────────────────────────────

CAP = 10_000_000


def _estimate(client, src: str, path: str = PAGE) -> int:
    r = client.get(f"/a/rca/items/{src}/publish/estimate", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()["bytes"]


def _fill(spec, who: str, to: int) -> None:
    """Charge ``who`` a filler row so their total is ``to``."""
    ledger = DiskLedger(spec)
    have = asyncio.run(ledger.total_for(who))
    asyncio.run(ledger.record(f"filler-{who}", who, to - have))


def test_a_shared_publish_counts_the_snapshot_and_the_shared_copy_together():
    """Both are the publisher's: checked one at a time, each fitted while the
    two together did not (review round 2)."""
    holder = {"id": "bob"}
    client, spec, store, _a = _app(holder, per_user_resources=PerUserResources(disk=str(CAP)))
    src = _source(spec)
    _workspace(client, src)
    size = _estimate(client, src)
    _fill(spec, "bob", CAP - size - size // 2)

    r = client.post(
        f"/a/rca/items/{src}/publish", json={"path": PAGE, "sharing": "shared", "reach": "everyone"}
    )

    assert r.status_code == 507, r.text
    assert asyncio.run(DiskLedger(spec).total_for("bob")) <= CAP
    assert not [ns for ns in store._files if ns.startswith("published:")]


def test_an_agent_copy_is_checked_for_what_it_really_writes():
    """A copy of an agent writes its folder's skills twice (their own path and
    the root's, A5): the check must count both."""
    holder = {"id": "bob"}
    client, spec, _store, _a = _app(holder, per_user_resources=PerUserResources(disk=str(CAP)))
    src = _source(spec)
    _workspace(client, src)
    skill = "/agents/sales/.skill/big/SKILL.md"
    body = b"---\nname: big\ndescription: d\n---\n" + b"x" * 4000
    assert client.put(f"/a/rca/items/{src}/files{skill}", content=body).status_code == 204
    _publish(client, src, AGENT, sharing="per_conversation")
    size = _estimate(client, src, AGENT)
    holder["id"] = "amy"
    _fill(spec, "amy", CAP - size - 100)

    r = _open(client, src, AGENT)

    assert r.status_code == 507, r.text
    assert asyncio.run(DiskLedger(spec).total_for("amy")) <= CAP


def test_a_second_maker_of_one_copy_leaves_the_first_alone():
    """Two opens at once: the one that loses the create returns the copy that
    exists and writes nothing — not its files over the person's edits."""
    from workspace_app.api.publish import Publications
    from workspace_app.api.publish_copy import make_copy, person_copy_id

    holder = {"id": "bob"}
    client, spec, store, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person", cpu_cores=2)
    row = Publications(spec).get(src, PAGE)
    assert row is not None
    named = person_copy_id(spec, row, "amy")
    ledger = DiskLedger(spec)
    private = Permission(visibility="private")

    first, made = asyncio.run(
        make_copy(spec, store, ledger, row, owner="amy", permission=private, item_id=named)
    )
    store._files[first][PAGE] = b"edited by amy"
    again, made_again = asyncio.run(
        make_copy(spec, store, ledger, row, owner="amy", permission=private, item_id=named)
    )

    assert made is True
    assert again == first and made_again is False
    assert store._files[first][PAGE] == b"edited by amy"


def test_a_copy_that_fails_after_its_item_was_made_leaves_no_item(monkeypatch):
    holder = {"id": "bob"}
    client, spec, store, _a = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="fresh")
    before_items = {
        res.info.resource_id for res in spec.get_resource_manager(RcaInvestigation).list_resources()
    }

    async def broken(*_a, **_k):
        raise FileNotFound("/data/orders.csv")

    monkeypatch.setattr("workspace_app.api.publish_copy._copy_files", broken)
    holder["id"] = "amy"

    assert _open(client, src).status_code == 410
    after = {
        res.info.resource_id for res in spec.get_resource_manager(RcaInvestigation).list_resources()
    }
    assert after == before_items


def test_taking_an_old_deploy_row_down_needs_what_listing_it_needs():
    holder = {"id": "bob"}
    client, spec, _s, _a = _app(holder)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        src = rm.create(
            RcaInvestigation(
                title="Line 3",
                owner="bob",
                permission=Permission(
                    visibility="restricted",
                    read_meta=["user:amy"],
                    read_content=["user:amy"],
                    edit_content=["user:amy"],
                ),
            )
        ).resource_id
    _workspace(client, src)
    assert client.post(f"/a/rca/items/{src}/wui/deploy", json={"path": PAGE}).status_code == 200
    holder["id"] = "amy"

    r = client.delete(f"/a/rca/items/{src}/wui/deploy", params={"path": PAGE})
    listed = client.get("/wui").json()["pages"]

    assert r.status_code == 403, r.text
    assert [p["can_remove"] for p in listed] == [False]
