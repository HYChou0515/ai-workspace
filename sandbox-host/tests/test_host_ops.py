"""Sandbox host operations — health, readiness, graceful drain, idle-reaper."""

from __future__ import annotations

import os

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import check_cgroup_ready, make_host_app
from sandbox_host.mock import MockSandbox


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


async def test_healthz_ok():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        r = await c.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


async def test_readyz_ok_by_default():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        assert (await c.get("/readyz")).status_code == 200


async def test_readyz_503_when_readiness_check_fails():
    def not_ready() -> None:
        raise RuntimeError("cgroup v2 not mounted")

    app = make_host_app(MockSandbox(), advertise_url="http://h", readiness=not_ready)
    async with _client(app) as c:
        r = await c.get("/readyz")
        assert r.status_code == 503
        assert r.json()["ready"] is False


async def test_create_rejected_with_503_while_draining():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    app.state.controller.start_draining()
    async with _client(app) as c:
        r = await c.post("/sandboxes", json={})
        assert r.status_code == 503


async def test_drain_endpoint_then_create_rejected():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        assert (await c.post("/sandboxes", json={})).status_code == 200  # ok before
        assert (await c.post("/drain")).status_code == 202
        assert (await c.post("/sandboxes", json={})).status_code == 503  # rejected after


async def test_idle_reaper_kills_only_stale_handles():
    clock = {"t": 0.0}
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", idle_ttl=100.0, clock=lambda: clock["t"]
    )
    ctrl = app.state.controller
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={})).json()["remote_id"]
    clock["t"] = 50.0
    assert await ctrl.reap_idle() == []  # not yet idle
    clock["t"] = 201.0
    assert await ctrl.reap_idle() == [rid]  # idle past the ttl → reaped
    assert await ctrl.reap_idle() == []  # already gone


async def test_activity_on_any_endpoint_resets_the_idle_timer():
    clock = {"t": 0.0}
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", idle_ttl=100.0, clock=lambda: clock["t"]
    )
    ctrl = app.state.controller
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={})).json()["remote_id"]
        clock["t"] = 80.0
        await c.get(f"/sandboxes/{rid}/exists", params={"path": "/x"})  # touch
    clock["t"] = 150.0  # 70s since the touch < ttl
    assert await ctrl.reap_idle() == []


async def test_idle_reaper_disabled_when_ttl_zero():
    clock = {"t": 0.0}
    app = make_host_app(
        MockSandbox(), advertise_url="http://h", idle_ttl=0.0, clock=lambda: clock["t"]
    )
    ctrl = app.state.controller
    async with _client(app) as c:
        await c.post("/sandboxes", json={})
    clock["t"] = 1e9
    assert await ctrl.reap_idle() == []


def test_check_cgroup_ready_passes_on_writable_v2(tmp_path):
    marker = tmp_path / "cgroup.controllers"
    marker.write_text("cpu memory pids")
    root = tmp_path / "delegated"
    root.mkdir()
    check_cgroup_ready(root, controllers_marker=marker)  # no raise


def test_check_cgroup_ready_raises_without_v2(tmp_path):
    with pytest.raises(RuntimeError, match="cgroup v2"):
        check_cgroup_ready(tmp_path / "x", controllers_marker=tmp_path / "absent")


def test_check_cgroup_ready_raises_when_not_writable(tmp_path):
    marker = tmp_path / "m"
    marker.write_text("cpu")
    root = tmp_path / "ro"
    root.mkdir()
    os.chmod(root, 0o500)
    try:
        with pytest.raises(RuntimeError, match="writable"):
            check_cgroup_ready(root, controllers_marker=marker)
    finally:
        os.chmod(root, 0o700)


async def test_healthz_names_the_behaviours_this_build_has():
    """Which code a running host carries is otherwise invisible: `image:
    sandbox-host:latest` is the same string before and after a rebuild, and a
    host that is merely OLD answers every request perfectly well — it just
    behaves like the older code. That cost a full diagnosis once already: a
    sandbox whose `$HOME` was never set looked, from the app side, exactly like
    a sandbox whose HOME was set correctly.

    Capabilities rather than a version number, because they are what the caller
    actually wants to know and they cannot drift from the code that declares
    them — no build-time stamping to remember, no compatibility table to keep
    in sync."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        body = (await c.get("/healthz")).json()
    assert body["status"] == "ok"
    # `per-exec-home` is the one this was built for: it says every exec gets
    # HOME pointed at the sandbox's own `.home`, created and owned on the spot.
    assert "per-exec-home" in body["capabilities"]
    assert isinstance(body["version"], str)


async def test_healthz_publishes_the_ceilings_this_host_will_apply():
    """The app charges a sandbox's owner for what it holds, and a sandbox whose
    spec states nothing is still capped — by THIS host's `SANDBOX_HOST_*`. Those
    live in this service's environment, where the app cannot read them, so it
    could only charge the request: an App that declared nothing held a core for
    free. The number published here is the enforcer's own answer, not a second
    copy of the config, so the two cannot drift."""
    app = make_host_app(
        MockSandbox(cpu_cores=1.0, memory_bytes=512 * 1024**2), advertise_url="http://h"
    )
    async with _client(app) as c:
        body = (await c.get("/healthz")).json()
    assert body["defaults"] == {"cpu_cores": 1.0, "memory_bytes": 512 * 1024**2}
    # Named, so an app talking to a host too old to say can tell "not advertised"
    # from "advertised as nothing" and report a stale image instead of charging 0.
    assert "resource-defaults" in body["capabilities"]


async def test_healthz_survives_a_backend_that_cannot_answer():
    """This endpoint is the deployment's LIVENESS probe. Making it depend on the
    sandbox answering a resource question turns "cannot say" into a crashloop —
    the app charging nothing is a visible under-count, a restarting pod is an
    outage."""

    class _Mute(MockSandbox):
        async def effective_limits(self, spec):  # noqa: ANN001, ANN201
            raise RuntimeError("this backend has no idea")

    app = make_host_app(_Mute(), advertise_url="http://h")
    async with _client(app) as c:
        resp = await c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["defaults"] == {"cpu_cores": None, "memory_bytes": None}


async def test_the_host_can_say_which_items_have_a_live_sandbox():
    """The app cannot otherwise find out what exists.

    Every record it keeps — the heartbeat that bills people, the address that
    routes to a sandbox, the panel that offers a Close button — is a belief
    written down at some past moment. Nothing could check those beliefs against
    the machine, so a stale one was indistinguishable from a true one, and
    clearing a record became the only way to say "gone" even when it was not.

    Keyed by ITEM, because that is the only name the app has. The host already
    tracks both halves for its own idle reaper."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        assert (await c.get("/sandboxes")).json()["sandboxes"] == []

        first = (await c.post("/sandboxes", json={"item_id": "item-a"})).json()["remote_id"]
        second = (await c.post("/sandboxes", json={"item_id": "item-b"})).json()["remote_id"]
        listed = (await c.get("/sandboxes")).json()["sandboxes"]
        assert sorted(s["item_id"] for s in listed) == ["item-a", "item-b"]
        assert {s["remote_id"] for s in listed} == {first, second}, (
            "each entry names the sandbox it is about"
        )

        await c.delete(f"/sandboxes/{first}")
        assert [s["item_id"] for s in (await c.get("/sandboxes")).json()["sandboxes"]] == ["item-b"]


async def test_a_sandbox_created_without_an_archive_still_remembers_its_item():
    """`_item_of` was filled only when an NFS archive was wired, because until
    now its only reader was `persist`. A listing keyed by item is useless on a
    deployment with no archive if the mapping is not there."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")  # no archive
    async with _client(app) as c:
        await c.post("/sandboxes", json={"item_id": "item-a"})
        assert [s["item_id"] for s in (await c.get("/sandboxes")).json()["sandboxes"]] == ["item-a"]


async def test_a_sandbox_created_without_an_item_is_listed_as_anonymous():
    """An older app, or a caller that has no item, still gets a sandbox — and it
    must appear in the listing, or the host would under-report what it is
    running."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        await c.post("/sandboxes", json={})
        listed = (await c.get("/sandboxes")).json()["sandboxes"]
        assert len(listed) == 1
        assert listed[0]["item_id"] is None


async def test_a_listed_sandbox_can_be_addressed_directly():
    """Naming the item is not enough to DO anything about it.

    The service in front of the host load-balances, so a listing answers for the
    one pod that happened to take the request, and every later call has to reach
    THAT pod. `create` already solves this by returning the answering pod's own
    directly-addressable url; a listing that omitted it would let the app see an
    orphaned sandbox and still have no way to kill it."""
    app = make_host_app(MockSandbox(), advertise_url="http://pod-7:8000")
    async with _client(app) as c:
        await c.post("/sandboxes", json={"item_id": "item-a"})
        listed = (await c.get("/sandboxes")).json()["sandboxes"]
        assert [s["pod_url"] for s in listed] == ["http://pod-7:8000"]
