"""P7 + P8's backend — "what am I holding, what may I hold", and the admin
override.

P7's acceptance conditions: changing one person's allowance changes only theirs;
no override means the deploy default; and it takes effect without a restart —
which is what the gate resolving limits per check buys.

P8's panel needs one call that answers both halves at once. A panel that fetched
usage and limits separately could render a pair that never coexisted.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from specstar import SpecStar

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.config.schema import PerUserResources
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.quota.limits import ResourceLimits
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ..api._client import TestClient as ApiTestClient

ONE_CORE = ResourceLimits(cpu_cores=1.0, memory_bytes=512 * 1024**2, disk_bytes=0)


@contextlib.contextmanager
def _app(
    limits: PerUserResources, *, me: str = "alice"
) -> Iterator[tuple[ApiTestClient, SpecStar]]:
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,
        app_resources={"rca": ONE_CORE},
        per_user_resources=limits,
        get_user_id=lambda: me,
        superusers=frozenset({"root"}),
    )
    with ApiTestClient(app) as client:
        yield client, spec


def _mk(spec: SpecStar, owner: str) -> str:
    return (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="my item", owner=owner))  # ty: ignore[invalid-argument-type]
        .resource_id
    )


def test_the_panel_reports_usage_and_limits_together():
    with _app(PerUserResources(count=3, disk="1G")) as (client, spec):
        item = _mk(spec, "alice")
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})
        client.put(f"/a/rca/items/{item}/files/a.bin", content=b"x" * 40)

        got = client.get("/me/resources")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["owner"] == "alice"
        assert body["limits"]["count"] == 3
        assert body["limits"]["disk_bytes"] == 1024**3
        # the live environment is named, not just counted — a list of things to
        # close is useless if you cannot tell which is which
        assert [e["item_id"] for e in body["live"]] == [item]
        assert body["live"][0]["title"] == "my item"
        assert body["live"][0]["slug"] == "rca"
        assert body["disk_in_use"] == 40


def test_closing_an_environment_frees_the_slot():
    """P8's acceptance loop: refused → open the panel → close → the same thing
    now works."""
    with _app(PerUserResources(count=1)) as (client, spec):
        first = _mk(spec, "alice")
        second = _mk(spec, "alice")
        client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo", "hi"]})
        refused = client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]})
        assert refused.status_code == 507

        assert client.delete(f"/me/resources/live/{first}").status_code == 204

        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 200
        )


def test_you_cannot_close_someone_elses_environment():
    with _app(PerUserResources(count=5)) as (client, spec):
        theirs = _mk(spec, "bob")
        client.post(f"/a/rca/items/{theirs}/exec", json={"cmd": ["echo", "hi"]})
        assert client.delete(f"/me/resources/live/{theirs}").status_code == 404


# ─── the admin override ────────────────────────────────────────────────


def test_an_override_changes_only_that_person():
    with _app(PerUserResources(count=1), me="root") as (client, spec):
        assert client.put("/admin/user-resources/alice", json={"count": 9}).status_code == 204
        assert client.get("/admin/user-resources/alice").json()["limits"]["count"] == 9
        assert client.get("/admin/user-resources/bob").json()["limits"]["count"] == 1


def test_an_override_takes_effect_without_a_restart():
    """The gate resolves limits per check, so a raised allowance applies to the
    very next turn."""
    with _app(PerUserResources(count=1), me="root") as (client, spec):
        first = _mk(spec, "root")
        second = _mk(spec, "root")
        client.post(f"/a/rca/items/{first}/exec", json={"cmd": ["echo", "hi"]})
        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 507
        )

        assert client.put("/admin/user-resources/root", json={"count": 5}).status_code == 204

        assert (
            client.post(f"/a/rca/items/{second}/exec", json={"cmd": ["echo", "hi"]}).status_code
            == 200
        )


def test_clearing_an_override_falls_back_to_the_deploy_default():
    with _app(PerUserResources(count=1), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        assert client.delete("/admin/user-resources/alice").status_code == 204
        assert client.get("/admin/user-resources/alice").json()["limits"]["count"] == 1


def test_an_override_is_per_dimension():
    """Setting only `count` must leave the other dimensions on the deploy
    default — an exception grants what it names and nothing else."""
    with _app(PerUserResources(count=1, disk="1G"), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        limits = client.get("/admin/user-resources/alice").json()["limits"]
        assert limits["count"] == 9
        assert limits["disk_bytes"] == 1024**3


def test_a_non_admin_cannot_read_or_set_anyones_limits():
    """404 rather than 403 — whether a person has an exception is not something
    to let a non-admin probe for."""
    with _app(PerUserResources(count=1)) as (client, _spec):  # me == alice
        assert client.get("/admin/user-resources/bob").status_code == 404
        assert client.put("/admin/user-resources/bob", json={"count": 9}).status_code == 404
        assert client.delete("/admin/user-resources/bob").status_code == 404
