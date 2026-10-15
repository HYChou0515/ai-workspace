"""The capacity dashboard's API (docs/plan-capacity-usage.md §11, D4): superusers
only; one analysis per period at a time, kept in memory and rebuilt on demand;
an analysis that would load too much stops and says so."""

from __future__ import annotations

import asyncio
from functools import cache

import pytest
from httpx import ASGITransport

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.capacity.service import CapacityService
from workspace_app.capacity.sources import CapacitySources
from workspace_app.config.schema import CapacitySettings
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ..api._client import AsyncClient
from .world import World, WorldGpus, WorldLlm, WorldPods, WorldRecords

WHO = {"id": "root"}


@cache
def _world() -> World:
    return World(days=28, users=20)


class _Counting(WorldRecords):
    """The world's records, counting how many analyses asked for them."""

    builds = 0

    def records(self, kind, start_ms, end_ms):
        if kind == "turn":
            type(self).builds += 1
        return super().records(kind, start_ms, end_ms)


def _service(settings: CapacitySettings | None = None) -> CapacityService:
    w = _world()
    _Counting.builds = 0
    sources = CapacitySources(_Counting(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    settings = settings or CapacitySettings(timezone=w.tz, lag_s=0)
    return CapacityService(sources, settings, clock=lambda: w.end_ms / 1000)


def _app(service: CapacityService):  # noqa: ANN202
    WHO["id"] = "root"
    spec = make_spec(default_user=lambda: WHO["id"])
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        get_user_id=lambda: WHO["id"],
        superusers=frozenset({"root"}),
        capacity=service,
    )


def _client(app) -> AsyncClient:  # noqa: ANN001
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/admin/capacity"),
        ("GET", "/api/admin/capacity/person?user=user00"),
        ("GET", "/api/admin/capacity/whatif?n=10"),
        ("POST", "/api/admin/capacity/refresh"),
    ],
)
async def test_only_a_superuser_may_look(method, path):
    app = _app(_service())
    WHO["id"] = "alice"
    async with _client(app) as c:
        r = await c.request(method, path)

    assert r.status_code == 403


async def test_the_summary_for_a_period():
    async with _client(_app(_service())) as c:
        r = await c.get("/api/admin/capacity?days=28")

    assert r.status_code == 200, r.text
    body = r.json()
    assert {line["id"] for line in body["lines"]} >= {"api.cpu", "sandbox-host.cpu", "gpu.qwen3"}
    assert body["people"]["n_now"] == 20
    assert body["readiness"]["whatif"]["ready"]


async def test_a_period_the_page_does_not_offer_is_refused():
    async with _client(_app(_service())) as c:
        r = await c.get("/api/admin/capacity?days=30")

    assert r.status_code == 422


async def test_one_person_and_an_unknown_one():
    async with _client(_app(_service())) as c:
        known = await c.get("/api/admin/capacity/person?user=user00&days=28")
        unknown = await c.get("/api/admin/capacity/person?user=nobody&days=28")

    assert known.status_code == 200 and known.json()["user"] == "user00"
    assert unknown.status_code == 404


async def test_what_if_by_profile():
    async with _client(_app(_service())) as c:
        everyone = await c.get("/api/admin/capacity/whatif?n=40&days=28")
        one_app = await c.get("/api/admin/capacity/whatif?n=40&days=28&profile=app&app=pm")
        bad = await c.get("/api/admin/capacity/whatif?n=40&profile=everyone")

    assert everyone.status_code == 200 and everyone.json()["n"] == 40
    assert one_app.json()["app"] == "pm"
    assert bad.status_code == 422


async def test_the_analysis_is_kept_until_refreshed():
    service = _service()
    async with _client(_app(service)) as c:
        await c.get("/api/admin/capacity?days=28")
        await c.get("/api/admin/capacity/whatif?n=5&days=28")
        assert _Counting.builds == 1
        assert (await c.post("/api/admin/capacity/refresh?days=28")).status_code == 204
        await c.get("/api/admin/capacity?days=28")

    assert _Counting.builds == 2


async def test_two_requests_at_once_build_one_analysis():
    service = _service()

    await asyncio.gather(service.analysis(28), service.analysis(28))

    assert _Counting.builds == 1


async def test_an_analysis_that_would_load_too_much_says_so():
    w = _world()
    service = _service(CapacitySettings(timezone=w.tz, lag_s=0, max_rows=100))
    async with _client(_app(service)) as c:
        r = await c.get("/api/admin/capacity?days=28")

    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "too_many_rows"
    assert r.json()["detail"]["max_rows"] == 100


async def test_nothing_connected_is_a_page_that_says_so():
    service = CapacityService(CapacitySources(), CapacitySettings())
    async with _client(_app(service)) as c:
        r = await c.get("/api/admin/capacity?days=7")

    assert r.status_code == 200
    body = r.json()
    assert body["lines"] == []
    assert body["readiness"]["allocation"]["missing"] == ["usage_records"]
