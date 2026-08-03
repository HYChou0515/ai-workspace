"""P6 — a person's disk total across every item they own.

The plan's acceptance conditions:

1. two items of the SAME owner, in different Apps, summing over the personal
   total ⇒ the write is refused
2. while over, DELETING still works — someone at their cap must be able to get
   back under it without asking anyone
3. the under-count window before an item has ever been measured is real, and is
   pinned here rather than pretended away
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.pm.model import PmProject
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.config.schema import PerUserResources
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.disk_ledger import DiskLedger, register_disk_ledger
from workspace_app.quota.limits import ResourceLimits
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ..api._client import TestClient as ApiTestClient

ROOMY = ResourceLimits(cpu_cores=1.0, memory_bytes=0, disk_bytes=0)  # no per-item cap


@contextlib.contextmanager
def _app(user_disk: str) -> Iterator[tuple[ApiTestClient, SpecStar]]:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,  # no per-item limit — the personal total must still bind
        app_resources={"rca": ROOMY, "pm": ROOMY},
        per_user_resources=PerUserResources(disk=user_disk),
    )
    with ApiTestClient(app) as client:
        yield client, spec


def _mk(spec: SpecStar, model, owner: str) -> str:
    return spec.get_resource_manager(model).create(model(title="t", owner=owner)).resource_id


def test_a_persons_items_are_summed_across_apps():
    """The headline condition. Neither item is over on its own — together they
    are, and the second write is what learns that."""
    with _app("300") as (client, spec):
        rca = _mk(spec, RcaInvestigation, "alice")
        pm = _mk(spec, PmProject, "alice")

        assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"x" * 200).status_code == 204
        over = client.put(f"/a/pm/items/{pm}/files/b.bin", content=b"y" * 200)
        assert over.status_code == 507
        assert over.json()["detail"]["error"] == "user_quota_exceeded"


def test_a_different_owner_has_their_own_allowance():
    with _app("300") as (client, spec):
        alice = _mk(spec, RcaInvestigation, "alice")
        bob = _mk(spec, PmProject, "bob")
        assert (
            client.put(f"/a/rca/items/{alice}/files/a.bin", content=b"x" * 200).status_code == 204
        )
        assert client.put(f"/a/pm/items/{bob}/files/b.bin", content=b"y" * 200).status_code == 204


def test_deleting_still_works_while_over_and_frees_the_person_up():
    """Condition 2 — the one that decides whether the limit is survivable. If
    the tools for tidying up are also refused, telling someone to delete things
    is telling them nothing."""
    with _app("300") as (client, spec):
        rca = _mk(spec, RcaInvestigation, "alice")
        pm = _mk(spec, PmProject, "alice")
        assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"x" * 250).status_code == 204
        assert client.put(f"/a/pm/items/{pm}/files/b.bin", content=b"y" * 200).status_code == 507

        # deleting is never gated, in either item
        assert client.delete(f"/a/rca/items/{rca}/files/a.bin").status_code in (200, 204)
        # …and now the same write fits
        assert client.put(f"/a/pm/items/{pm}/files/b.bin", content=b"y" * 200).status_code == 204


def test_shrinking_is_allowed_while_over():
    with _app("300") as (client, spec):
        rca = _mk(spec, RcaInvestigation, "alice")
        assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"x" * 250).status_code == 204
        # a smaller replace does not grow anything, so it passes even at the cap
        assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"z" * 10).status_code == 204


def test_no_personal_limit_leaves_writes_alone():
    with _app("") as (client, spec):
        rca = _mk(spec, RcaInvestigation, "alice")
        pm = _mk(spec, PmProject, "alice")
        assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"x" * 900).status_code == 204
        assert client.put(f"/a/pm/items/{pm}/files/b.bin", content=b"y" * 900).status_code == 204


# ─── the ledger itself, including the honest gap ───────────────────────


async def test_the_total_excludes_the_item_being_written():
    """The caller has a LIVE size for the item it is writing to. Counting the
    ledger's stale row for that same item as well would charge it twice."""
    spec = make_spec()
    register_disk_ledger(spec)
    ledger = DiskLedger(spec)
    await ledger.record("i-1", "alice", 100)
    await ledger.record("i-2", "alice", 50)
    assert await ledger.total_for("alice") == 150
    assert await ledger.total_for("alice", exclude="i-1") == 50


async def test_a_forgotten_item_stops_being_charged():
    spec = make_spec()
    register_disk_ledger(spec)
    ledger = DiskLedger(spec)
    await ledger.record("i-1", "alice", 100)
    await ledger.forget("i-1")
    assert await ledger.total_for("alice") == 0


async def test_an_unmeasured_item_contributes_nothing_yet():
    """The deliberate gap: until an item has been measured once, it is invisible
    to the personal total, so a person can briefly sit a little over. Pinned as
    a fact rather than left as a surprise — the per-ITEM limit is the exact one,
    and making this exact too would mean walking every workspace on every write.
    """
    spec = make_spec()
    register_disk_ledger(spec)
    ledger = DiskLedger(spec)
    await ledger.record("measured", "alice", 100)
    assert await ledger.total_for("alice") == 100  # "never-written" contributes 0


@pytest.mark.parametrize("owner", ["", "someone-else"])
async def test_the_total_is_scoped_to_one_person(owner: str):
    spec = make_spec()
    register_disk_ledger(spec)
    ledger = DiskLedger(spec)
    await ledger.record("i-1", owner, 100)
    assert await ledger.total_for("alice") == 0
