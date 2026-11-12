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

from .test_publish_copy import AGENT, PAGE, _app, _open, _publish, _source, _workspace


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
