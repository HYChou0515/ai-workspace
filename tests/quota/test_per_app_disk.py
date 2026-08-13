"""P4 — an item's disk quota comes from ITS App, not from one deploy-wide number.

The acceptance condition from the plan: two Apps given different `disk`, and the
SAME write passes in one and is refused in the other. Everything else here
guards the rule that must survive the change — the gate is on GROWTH, so an
over-quota workspace can always be tidied back under.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar

from workspace_app.api import create_app
from workspace_app.apps.pm.model import PmProject
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.files import WorkspaceFiles, WorkspaceFull
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.limits import ResourceLimits
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ..api._client import TestClient as ApiTestClient

SMALL = ResourceLimits(cpu_cores=1.0, memory_bytes=0, disk_bytes=100)
ROOMY = ResourceLimits(cpu_cores=1.0, memory_bytes=0, disk_bytes=300)


def _two_app_client(**kwargs) -> tuple[ApiTestClient, str, str]:
    """One app serving BOTH an rca item (disk 100) and a pm item (disk 300)."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_no_runner(),
        app_resources={"rca": SMALL, "pm": ROOMY},
        **kwargs,
    )
    rca = _mk(spec, RcaInvestigation)
    pm = _mk(spec, PmProject)
    return ApiTestClient(app), rca, pm


def _no_runner():
    from workspace_app.api import ScriptedAgentRunner

    return ScriptedAgentRunner([])


def _mk(spec: SpecStar, model) -> str:
    return spec.get_resource_manager(model).create(model(title="t", owner="u")).resource_id


def test_the_same_write_is_refused_in_one_app_and_allowed_in_the_other():
    """The headline condition: identical bytes, different verdicts, decided only
    by which App the item belongs to."""
    client, rca, pm = _two_app_client()
    body = b"x" * 150

    refused = client.put(f"/a/rca/items/{rca}/files/big.bin", content=body)
    assert refused.status_code == 507
    assert refused.json()["detail"]["quota"] == 100

    assert client.put(f"/a/pm/items/{pm}/files/big.bin", content=body).status_code == 204


def test_an_app_without_a_declared_limit_falls_back_to_the_deploy_default():
    """`app_resources` is a mapping — a slug absent from it (an App that declares
    nothing, in a deploy that configures nothing) must keep using the deploy-wide
    number rather than silently becoming unlimited."""
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=_no_runner(),
        workspace_quota=50,
        app_resources={"pm": ROOMY},  # rca deliberately absent
    )
    rca = _mk(spec, RcaInvestigation)
    client = ApiTestClient(app)
    over = client.put(f"/a/rca/items/{rca}/files/big.bin", content=b"x" * 80)
    assert over.status_code == 507
    assert over.json()["detail"]["quota"] == 50


def test_over_quota_workspace_can_still_be_tidied():
    """The growth rule, re-pinned at the per-App layer: once over, shrinking and
    deleting must still work, or the user is told to free space with tools that
    refuse to run."""
    client, rca, _pm = _two_app_client()
    assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"x" * 100).status_code == 204
    # same size — a replace, not growth
    assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"y" * 100).status_code == 204
    # smaller — always allowed
    assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"z" * 40).status_code == 204
    # and now there is room again
    assert client.put(f"/a/rca/items/{rca}/files/b.bin", content=b"w" * 60).status_code == 204


def test_deleting_is_never_gated():
    client, rca, _pm = _two_app_client()
    assert client.put(f"/a/rca/items/{rca}/files/a.bin", content=b"x" * 100).status_code == 204
    assert client.delete(f"/a/rca/items/{rca}/files/a.bin").status_code in (200, 204)
    assert client.put(f"/a/rca/items/{rca}/files/b.bin", content=b"y" * 100).status_code == 204


# ─── the facade seam itself ────────────────────────────────────────────


async def test_ensure_room_for_uses_the_items_own_quota():
    """The whole-operation gate (a folder copy, search/replace, staging a run's
    inputs) must read the same per-item number as the per-write gate — a batch
    checked against a different limit is how half a copy lands."""
    files = WorkspaceFiles(MemoryFileStore(), quota={"small": 100, "roomy": 300}.__getitem__)
    await files.ensure_room_for("roomy", 150)  # fits
    with pytest.raises(WorkspaceFull):
        await files.ensure_room_for("small", 150)


async def test_quota_accepts_a_flat_number_for_a_single_tenant_deploy():
    """A plain int is still a legal spelling — it just normalises to "the same
    number for every workspace" internally, so there is one rule inside."""
    files = WorkspaceFiles(MemoryFileStore(), quota=100)
    await files.ensure_room_for("ws", 80)
    with pytest.raises(WorkspaceFull):
        await files.ensure_room_for("ws", 150)


async def test_remaining_quota_reads_the_items_own_limit():
    """`remaining_quota` backs the upload route's mid-stream cutoff. It used to
    take the limit as an argument, which meant the route carried its own copy of
    the rule — exactly the drift that let a stale route refuse shrinks. It now
    asks the facade, so there is one answer per item."""
    files = WorkspaceFiles(MemoryFileStore(), quota={"a": 1000, "b": 100}.__getitem__)
    assert await files.remaining_quota("a", "/f") == 1000
    assert await files.remaining_quota("b", "/f") == 100


def test_every_507_body_comes_from_one_builder():
    """Four entry points answer 507. The wording drifted across them once
    already, so the body is built in ONE place — including the streamed upload,
    which cannot use the app-wide handler (it must stop mid-transfer) and so is
    the one most likely to grow a fifth spelling.

    Asserted on the KEYS: a hand-written copy that happens to agree today is not
    the same thing as one that cannot disagree tomorrow."""
    import inspect

    from workspace_app.api import file_routes
    from workspace_app.api.turn_gate import quota_body
    from workspace_app.files.facade import WorkspaceFull

    src = inspect.getsource(file_routes)
    assert "quota_body(" in src, "the streamed upload must not build its own body"
    assert '"error": "workspace_quota_exceeded"' not in src, "a hand-written body came back"
    assert set(quota_body(WorkspaceFull(used=1, quota=2, attempted=3))) == {
        "error",
        "used",
        "quota",
        "attempted",
    }
