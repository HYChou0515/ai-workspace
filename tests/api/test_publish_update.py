"""A WUI copy can take the new version (`docs/plan-wui-copy-update.md`).

The question a copy asks — is there a newer publish than the one I was made
from (P1) — and the taking of it (P2).
"""

from __future__ import annotations

import asyncio
import itertools

import msgspec
import pytest

from workspace_app.api import publish as publish_mod
from workspace_app.apps.rca.model import RcaInvestigation

from .test_publish_copy import AGENT, PAGE, _app, _item, _open, _publish, _source, _workspace


@pytest.fixture
def clock(monkeypatch):
    """Publishes a second apart: two in one millisecond would be one version."""
    ticks = itertools.count(1_700_000_000_000, 1000)
    monkeypatch.setattr(publish_mod, "now_ms", lambda: next(ticks))


def _update(client, item_id: str):
    r = client.get(f"/a/rca/items/{item_id}/published-update")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_persons_copy_is_behind_once_the_page_is_published_again(clock):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    assert _update(client, mine)["due"] is False

    holder["id"] = "bob"
    again = _publish(client, src, sharing="per_person", note="Totals per week.")
    holder["id"] = "amy"

    got = _update(client, mine)
    assert got["due"] is True
    assert got["published_at"] == again["published_at"]
    assert [n["text"] for n in got["notes"]] == ["Totals per week."]


def test_the_old_shared_copy_points_on_and_the_new_one_is_current(clock):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    old = _publish(client, src, sharing="shared")
    holder["id"] = "amy"
    old_copy = _open(client, src).json()["item_id"]

    holder["id"] = "bob"
    _publish(client, src, sharing="shared")
    holder["id"] = "amy"
    new_copy = _open(client, src).json()["item_id"]

    assert new_copy != old_copy
    got = _update(client, old_copy)
    assert got["due"] is True and got["sharing"] == "shared"
    # Where 前往新版 goes: the publication itself, which lands in the current copy.
    assert (got["source_item_id"], got["path"]) == (src, PAGE)
    assert _update(client, new_copy)["due"] is False
    assert old["sharing"] == "shared"


def test_only_the_person_a_copy_was_made_for_is_told(clock):
    from .test_publish_hardening import _app as _app_with

    holder = {"id": "bob"}
    client, spec, _s, _a = _app_with(holder, superusers=frozenset({"root"}))
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    holder["id"] = "bob"
    _publish(client, src, sharing="per_person")

    holder["id"] = "root"  # may read it — but it is not theirs to update
    assert _update(client, mine)["due"] is False
    holder["id"] = "amy"
    assert _update(client, mine)["due"] is True


def test_nothing_is_said_once_the_page_is_taken_down(clock):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    holder["id"] = "bob"
    _publish(client, src, sharing="per_person")
    assert client.delete(f"/a/rca/items/{src}/publish", params={"path": PAGE}).status_code == 204

    holder["id"] = "amy"
    assert _update(client, mine)["due"] is False


def test_a_conversation_with_an_agent_is_not_told(clock):
    # D2: 「新對話」 already starts from the latest; an old conversation stays.
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, AGENT, sharing="per_conversation")
    holder["id"] = "amy"
    chat = _open(client, src, AGENT).json()["item_id"]
    holder["id"] = "bob"
    _publish(client, src, AGENT, sharing="per_conversation")

    holder["id"] = "amy"
    assert _update(client, chat)["due"] is False


def test_it_says_whether_a_new_copy_fits_and_whether_it_would_without_this_one(clock):
    # D5: asked before anything is made — the banner can say so up front.
    from workspace_app.config.schema import PerUserResources
    from workspace_app.quota.disk_ledger import DiskLedger

    from .test_publish_hardening import _app as _app_with

    holder = {"id": "bob"}
    client, spec, _s, _a = _app_with(holder, per_user_resources=PerUserResources(disk="250"))
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    # Her own work in her copy: now a second copy does not fit beside it.
    r = client.put(f"/a/rca/items/{mine}/files/data/notes.txt", content=b"x" * 100)
    assert r.status_code == 204, r.text
    holder["id"] = "bob"
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    got = _update(client, mine)
    used = asyncio.run(DiskLedger(spec).total_for("amy"))
    held = dict(asyncio.run(DiskLedger(spec).per_item_for("amy")))[mine]
    assert got["needs"] > 0
    assert got["free"] == 250 - used
    assert got["fits"] is False
    assert got["free_if_replaced"] == 250 - used + held
    assert got["needs"] <= got["free_if_replaced"]


def test_without_a_cap_it_always_fits(clock):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    holder["id"] = "bob"
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    got = _update(client, mine)
    assert got["fits"] is True and got["free"] is None


def _unstamp(spec, item_id: str) -> None:
    """A copy made before the stamp was recorded."""
    rm = spec.get_resource_manager(RcaInvestigation)
    item = rm.get(item_id).data
    with rm.using(rm.get_meta(item_id).created_by):
        rm.update(item_id, msgspec.structs.replace(item, published_version=0))


def test_a_copy_made_before_the_stamp_is_judged_by_when_it_was_made(monkeypatch):
    # Real time here: a copy's creation time is the store's clock.
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    _unstamp(spec, mine)
    assert _update(client, mine)["due"] is False

    holder["id"] = "bob"
    later = itertools.count(4_000_000_000_000, 1000)  # long after the copy
    monkeypatch.setattr(publish_mod, "now_ms", lambda: next(later))
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"

    assert _update(client, mine)["due"] is True


# ---- P2: taking it --------------------------------------------------------


def _take(client, item_id: str, **body):
    return client.post(f"/a/rca/items/{item_id}/published-update", json=body)


def _behind(holder, client, spec, sharing="per_person"):
    """amy's copy, then bob publishes again (with a page that says v2)."""
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing=sharing)
    holder["id"] = "amy"
    old = _open(client, src).json()["item_id"]
    holder["id"] = "bob"
    r = client.put(f"/a/rca/items/{src}/files{PAGE}", content=b"view: wui\ntitle: Board v2\n")
    assert r.status_code == 204
    _publish(client, src, sharing=sharing)
    holder["id"] = "amy"
    return src, old


def test_taking_the_new_version_makes_a_new_copy_and_keeps_the_old_one(clock):

    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    src, old = _behind(holder, client, spec)

    r = _take(client, old)

    assert r.status_code == 200, r.text
    new = r.json()["item_id"]
    assert new != old
    # The link now opens the new one, and it is current.
    assert _open(client, src).json()["item_id"] == new
    assert _update(client, new)["due"] is False
    assert b"Board v2" in store._files[new][PAGE]
    # The old one stays, an ordinary item: no longer her copy, named as old.
    kept = _item(spec, old)
    assert kept.published_from == ""
    assert kept.title.endswith("（舊版）")
    assert _update(client, old)["due"] is False


def test_without_room_it_is_refused_and_replacing_puts_the_new_one_in_its_place(clock):
    from workspace_app.apps.resolve import find_work_item
    from workspace_app.config.schema import PerUserResources
    from workspace_app.quota.disk_ledger import DiskLedger

    from .test_publish_hardening import _app as _app_with

    holder = {"id": "bob"}
    client, spec, store, _a = _app_with(holder, per_user_resources=PerUserResources(disk="250"))
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    old = _open(client, src).json()["item_id"]
    assert (
        client.put(f"/a/rca/items/{old}/files/data/notes.txt", content=b"x" * 100).status_code
        == 204
    )
    holder["id"] = "bob"
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    assert _update(client, old)["fits"] is False

    refused = _take(client, old)
    assert refused.status_code == 507, refused.text
    # Nothing made, nothing renamed.
    assert _item(spec, old).published_from
    assert _open(client, src).json()["item_id"] == old

    r = _take(client, old, replace=True)

    assert r.status_code == 200, r.text
    new = r.json()["item_id"]
    assert find_work_item(spec, old, include_deleted=True) is None  # gone for good
    assert old not in store._files
    assert _open(client, src).json()["item_id"] == new
    assert asyncio.run(DiskLedger(spec).total_for("amy")) <= 250


def test_a_copy_that_is_current_or_not_yours_is_not_taken(clock):
    from .test_publish_hardening import _app as _app_with

    holder = {"id": "bob"}
    client, spec, _s, _a = _app_with(holder, superusers=frozenset({"root"}))
    src, old = _behind(holder, client, spec)

    holder["id"] = "root"
    assert _take(client, old).status_code == 409
    holder["id"] = "amy"
    new = _take(client, old).json()["item_id"]
    assert _take(client, new).status_code == 409  # already the latest


def test_taking_it_from_two_tabs_at_once_makes_one_new_copy(clock):
    import httpx

    from .test_publish_hardening import _app as _app_with
    from .test_publish_hardening import _Yielding

    holder = {"id": "bob"}
    client, spec, _s, app = _app_with(holder, store=_Yielding())
    _src, old = _behind(holder, client, spec)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            url = f"/api/a/rca/items/{old}/published-update"
            return await asyncio.gather(c.post(url, json={}), c.post(url, json={}))

    a, b = asyncio.run(go())

    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()["item_id"] == b.json()["item_id"]
    assert _item(spec, old).title == "Shipping board（舊版）"  # once


def test_replacing_it_from_two_tabs_at_once_deletes_the_old_one_once(clock):
    import httpx

    from .test_publish_hardening import _app as _app_with
    from .test_publish_hardening import _Yielding

    purged: list[str] = []

    class _Counting(_Yielding):
        # The delete cascade purges the old copy's files: count how often.
        async def purge(self, workspace_id: str) -> None:
            purged.append(workspace_id)
            await super().purge(workspace_id)

    holder = {"id": "bob"}
    client, spec, _s, app = _app_with(holder, store=_Counting())
    _src, old = _behind(holder, client, spec)

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            url = f"/api/a/rca/items/{old}/published-update"
            return await asyncio.gather(
                c.post(url, json={"replace": True}), c.post(url, json={"replace": True})
            )

    a, b = asyncio.run(go())

    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()["item_id"] == b.json()["item_id"]
    assert purged.count(old) == 1


def test_a_shared_copy_is_not_copied_again(clock):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    _src, old = _behind(holder, client, spec, sharing="shared")

    assert _take(client, old).status_code == 400


def test_a_copy_made_on_open_takes_the_new_version_the_same_way(clock):
    holder = {"id": "bob"}
    client, spec, store = _app(holder)
    _src, old = _behind(holder, client, spec, sharing="fresh")

    r = _take(client, old)

    assert r.status_code == 200, r.text
    assert b"Board v2" in store._files[r.json()["item_id"]][PAGE]


def test_the_publication_says_its_latest_note(clock):
    # D6: the skill writes the note into `publish:`; the dialog offers it — but
    # not once it has been published, so it reads the latest one back.
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    first = _publish(client, src, sharing="per_person")
    assert first["latest_note"] == ""

    _publish(client, src, sharing="per_person", note="Totals per week.")
    got = client.get(f"/a/rca/items/{src}/publish", params={"path": PAGE}).json()

    assert got["latest_note"] == "Totals per week."


# ---- review round 1 ---------------------------------------------------------


def test_after_taking_it_and_deleting_the_new_copy_the_link_still_opens(clock, monkeypatch):
    # The （舊版） item keeps the derived id of the person's copy; it is no copy
    # in the making, so an open must not wait on it (a 503, for good).
    from workspace_app.api import publish_copy

    monkeypatch.setattr(publish_copy, "READY_WAIT_S", 0.5)
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src, old = _behind(holder, client, spec)
    new = _take(client, old).json()["item_id"]
    assert client.delete(f"/a/rca/items/{new}").status_code in (200, 204)

    r = _open(client, src)

    assert r.status_code == 200, r.text
    assert r.json()["item_id"] not in (old, new)


def test_someone_no_longer_allowed_to_use_it_is_not_offered_it(clock):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    holder["id"] = "bob"
    _publish(client, src, sharing="per_person", reach="me")  # amy is out

    holder["id"] = "amy"
    assert _update(client, mine)["due"] is False
    assert _take(client, mine).status_code == 409


def test_a_page_now_used_directly_offers_no_new_copy(clock):
    # D2: "direct" hands out no copies — taking it would copy an empty snapshot.
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    holder["id"] = "amy"
    mine = _open(client, src).json()["item_id"]
    holder["id"] = "bob"
    _publish(client, src, sharing="direct")

    holder["id"] = "amy"
    assert _update(client, mine)["due"] is False
    assert _take(client, mine).status_code == 409


def test_the_publication_remembers_the_draft_it_was_published_from(clock):
    # The person edited the skill's draft before pressing Publish: the draft
    # still sits in publish.note, and must not be offered again next time.
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src = _source(spec)
    _workspace(client, src)
    _publish(client, src, sharing="per_person")
    r = client.put(
        f"/a/rca/items/{src}/files{PAGE}",
        content=b"view: wui\ntitle: Shipping board\npublish:\n  note: Totals per week.\n",
    )
    assert r.status_code == 204

    got = _publish(client, src, sharing="per_person", note="Totals per week, and a chart.")

    assert got["latest_note"] == "Totals per week, and a chart."
    assert got["note_draft"] == "Totals per week."


def test_nothing_is_offered_once_its_source_is_in_the_trash(clock):
    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src, old = _behind(holder, client, spec)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        rm.delete(src)  # soft — in the trash

    assert _update(client, old)["due"] is False
    assert _take(client, old).status_code == 409


def test_a_replace_whose_delete_fails_leaves_the_link_on_the_new_copy(clock, monkeypatch):
    # The old copy stops being a copy BEFORE the delete: a delete that fails
    # part-way leaves an ordinary （舊版） item to delete by hand — never a
    # gutted copy the link lands in, nor a button that "succeeds" doing nothing.
    from workspace_app.api import publish as publish_api

    holder = {"id": "bob"}
    client, spec, _store = _app(holder)
    src, old = _behind(holder, client, spec)
    real = publish_api.unpublish_item
    calls = {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("storage hiccup")
        return await real(*a, **k)

    monkeypatch.setattr(publish_api, "unpublish_item", flaky)

    failed = _take(client, old, replace=True)

    assert failed.status_code == 500
    # Said as what it is — the new copy made, the old one left — so the banner
    # never guesses it from "no longer due" (review round 2).
    assert failed.json()["detail"]["error"] == "old_copy_left"
    new = _open(client, src).json()["item_id"]
    assert new != old
    assert _update(client, old)["due"] is False
    assert _item(spec, old).title.endswith("（舊版）")
