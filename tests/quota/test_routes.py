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
    limits: PerUserResources,
    *,
    me: str = "alice",
    app_resources: dict[str, ResourceLimits] | None = None,
) -> Iterator[tuple[ApiTestClient, SpecStar]]:
    spec = make_spec()
    app = create_app(
        spec=spec,
        # A backend that enforces ceilings of its own — which is what every
        # production backend does. A mock enforcing nothing cannot show what an
        # App that declares nothing actually costs its owner.
        sandbox=MockSandbox(cpu_cores=2.0, memory_bytes=256 * 1024**2),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
        workspace_quota=0,
        app_resources=app_resources or {"rca": ONE_CORE},
        per_user_resources=limits,
        get_user_id=lambda: me,
        superusers=frozenset({"root"}),
    )
    with ApiTestClient(app) as client:
        yield client, spec


def _mk(spec: SpecStar, owner: str) -> str:
    return (
        spec.get_resource_manager(RcaInvestigation)
        .create(RcaInvestigation(title="my item", owner=owner))
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


def test_the_admin_can_see_who_is_above_the_default_without_knowing_their_id():
    """The by-id read only answers "does THIS person have one", so an operator
    could only find an exception they already knew about."""
    with _app(PerUserResources(count=1, disk="1G"), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        client.put("/admin/user-resources/bob", json={"memory": "8G"})

        body = client.get("/admin/user-resources").json()
        assert [o["user_id"] for o in body["overrides"]] == ["alice", "bob"]
        # RAW, not merged: bob has no count exception, so it reads 0 rather than
        # the deploy's 1 — otherwise every row would look overridden everywhere.
        by_id = {o["user_id"]: o for o in body["overrides"]}
        assert (by_id["alice"]["count"], by_id["alice"]["memory"]) == (9, "")
        assert (by_id["bob"]["count"], by_id["bob"]["memory"]) == (0, "8G")
        # and the baseline they are exceptions TO, so a number means something
        assert body["defaults"] == {
            "count": 1,
            "cpu": 0.0,
            "memory_bytes": 0,
            "disk_bytes": 1024**3,
        }


def test_a_revoked_exception_leaves_the_list():
    """`clear_for` SOFT-deletes and `list_resources` returns soft-deleted rows,
    so without an `is_deleted` filter the page would keep reporting privileges
    nobody holds — the same trap the activity ledger documents."""
    with _app(PerUserResources(count=1), me="root") as (client, _spec):
        client.put("/admin/user-resources/alice", json={"count": 9})
        assert len(client.get("/admin/user-resources").json()["overrides"]) == 1

        client.delete("/admin/user-resources/alice")
        assert client.get("/admin/user-resources").json()["overrides"] == []


def test_only_an_admin_can_list_the_exceptions():
    """404 rather than 403, like the by-id read: whether anyone has an exception
    is not something an ordinary caller should be able to probe."""
    with _app(PerUserResources(count=1), me="alice") as (client, _spec):
        assert client.get("/admin/user-resources").status_code == 404


def test_a_non_admin_cannot_read_or_set_anyones_limits():
    """404 rather than 403 — whether a person has an exception is not something
    to let a non-admin probe for."""
    with _app(PerUserResources(count=1)) as (client, _spec):  # me == alice
        assert client.get("/admin/user-resources/bob").status_code == 404
        assert client.put("/admin/user-resources/bob", json={"count": 9}).status_code == 404
        assert client.delete("/admin/user-resources/bob").status_code == 404


def test_a_live_environment_is_charged_what_the_backend_really_caps_it_at():
    """An App that declares nothing is not free.

    Its `SandboxSpec` carries `None`, which means "backend, use your own
    ceiling" — and the backend does: production caps every sandbox at
    `SANDBOX_HOST_CPU_CORES` / `MEMORY_MAX`, a local deploy at
    `sandbox.isolation.*`. The tally read the SPEC, so it charged 0: the panel
    showed "CPU 0" beside a live environment, and a per-person cpu/memory cap
    summed those zeros and could never bind."""
    undeclared = ResourceLimits(cpu_cores=None, memory_bytes=None, disk_bytes=0)
    with _app(PerUserResources(count=3), app_resources={"rca": undeclared}) as (client, spec):
        item = _mk(spec, "alice")
        client.post(f"/a/rca/items/{item}/exec", json={"cmd": ["echo", "hi"]})

        got = client.get("/me/resources").json()
        assert got["live"][0]["cpu_cores"] == 2.0
        assert got["cpu_in_use"] == 2.0
        assert got["memory_in_use"] == 256 * 1024**2
