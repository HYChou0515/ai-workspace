"""The capacity dashboard's API (docs/plan-capacity-usage.md §11, D4): superusers
only; one analysis per period at a time, kept in memory and rebuilt on demand;
an analysis that would load too much stops and says so."""

from __future__ import annotations

import asyncio
import gc
import threading
import time
import weakref
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


async def test_a_cancelled_first_request_neither_fails_the_others_nor_builds_twice():
    """The build is nobody's request: a page closed while it ran must not hand
    its cancellation to the other people waiting, nor start a second build."""
    service = _service()
    first = asyncio.ensure_future(service.analysis(28))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(service.analysis(28))
    await asyncio.sleep(0)

    first.cancel()
    got = await asyncio.wait_for(second, 60)
    again = await service.analysis(28)

    assert got is again
    assert _Counting.builds == 1
    with pytest.raises(asyncio.CancelledError):
        await first


class _Watched(CapacityService):
    """Notes how many builds ran at once."""

    active = most = 0

    def _build(self, days: int):  # noqa: ANN202
        type(self).active += 1
        type(self).most = max(type(self).most, type(self).active)
        try:
            time.sleep(0.05)
            return super()._build(days)
        finally:
            type(self).active -= 1


async def test_analyses_are_built_one_at_a_time():
    """Each build holds a period's records in the API process: three at once
    is three times the peak, on the pod that serves everyone."""
    w = _world()
    _Watched.active = _Watched.most = 0
    sources = CapacitySources(_Counting(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    service = _Watched(
        sources, CapacitySettings(timezone=w.tz, lag_s=0), clock=lambda: w.end_ms / 1000
    )

    await asyncio.gather(service.analysis(7), service.analysis(28), service.analysis(90))

    assert _Watched.most == 1


async def test_a_kept_analysis_is_let_go_once_its_time_is_up():
    """Kept for the TTL to serve the page's next questions — not for as long
    as the process lives after one look."""
    w = _world()
    sources = CapacitySources(_Counting(w), WorldPods(w), WorldGpus(w), WorldLlm(w))
    service = CapacityService(
        sources, CapacitySettings(timezone=w.tz, lag_s=0), clock=time.time, ttl_s=0.05
    )

    await service.analysis(28)
    await asyncio.sleep(0.2)

    assert 28 not in service._kept  # noqa: SLF001


class _Instant(CapacityService):
    """Builds a stand-in at once: these tests are about keeping, not analysing."""

    def _build(self, days: int):  # noqa: ANN202
        return _Built()


class _Built:
    pass


async def test_a_dropped_analysis_is_not_held_until_its_time_would_have_been_up():
    """Each analysis holds a period's slots for every line and the people it
    costs. A
    refresh lets it go; the timer that would have let it go at the end of its
    TTL must not hold it until then — ten refreshes would be ten analyses."""
    service = _service()
    first = await service.analysis(28)
    gone = weakref.ref(first)
    del first

    service.drop(28)
    await service.analysis(28)
    gc.collect()

    assert gone() is None


async def test_the_first_analysis_timer_does_not_let_go_of_the_second():
    """Refreshed halfway through the first one's TTL: the second is kept for
    its own whole TTL."""
    service = _Instant(CapacitySources(), CapacitySettings(), clock=time.time, ttl_s=1.0)
    await service.analysis(28)
    await asyncio.sleep(0.5)
    service.drop(28)
    second = await service.analysis(28)

    await asyncio.sleep(0.75)  # past the first one's TTL, within the second's

    assert service._kept[28][1] is second  # noqa: SLF001


async def test_the_answers_are_worked_out_off_the_event_loop():
    """summary(), whatif() and person() walk the whole analysis; on the loop
    they stall every request the pod is serving."""
    service = _service()
    seen: dict[str, str] = {}
    analysis = await service.analysis(28)
    for name in ("summary", "whatif", "person"):
        real = getattr(analysis, name)

        def spy(*a, _real=real, _name=name, **kw):  # noqa: ANN002, ANN003, ANN202
            seen[_name] = threading.current_thread().name
            return _real(*a, **kw)

        setattr(analysis, name, spy)
    async with _client(_app(service)) as c:
        await c.get("/api/admin/capacity?days=28")
        await c.get("/api/admin/capacity/whatif?n=5&days=28")
        await c.get("/api/admin/capacity/person?user=user00&days=28")

    assert set(seen) == {"summary", "whatif", "person"}
    assert threading.main_thread().name not in seen.values()


async def test_the_page_is_told_when_sandbox_use_is_not_measured():
    """The test app runs sandboxes with no cgroup each: nothing measures them."""
    async with _client(_app(_service())) as c:
        body = (await c.get("/api/admin/capacity?days=28")).json()

    assert body["sandbox_measured"] is False


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
