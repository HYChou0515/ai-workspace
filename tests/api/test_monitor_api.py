"""Monitor HTTP surface (issue #11): the snapshot endpoint, plus that the
stream endpoint hands back an SSE response. The stream's generator logic is
unit-tested in tests/monitor — it's an infinite body, which the in-process
ASGITransport buffers, so we invoke the route handler directly rather than
over HTTP."""

from __future__ import annotations

import time

from fastapi.responses import StreamingResponse
from httpx import ASGITransport

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.monitor import IMonitor, InMemoryMonitor
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient


def _route(app, path: str):
    # backend routes now live under /api (#177); accept the bare path callers pass
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) in (path, "/api" + path))


def _app(monitor: IMonitor):
    return create_app(
        spec=make_spec(),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        monitor=monitor,
    )


async def test_monitor_snapshot_filters_by_group_and_limit():
    m = InMemoryMonitor()
    m.record({"kind": "span_end", "group_id": "inv-1", "n": 1})
    m.record({"kind": "span_end", "group_id": "inv-2", "n": 2})
    m.record({"kind": "span_end", "group_id": "inv-1", "n": 3})
    async with AsyncClient(transport=ASGITransport(app=_app(m)), base_url="http://t") as c:
        assert [e["n"] for e in (await c.get("/monitor")).json()] == [1, 2, 3]
        scoped = (await c.get("/monitor", params={"group_id": "inv-1"})).json()
        assert [e["n"] for e in scoped] == [1, 3]
        assert [e["n"] for e in (await c.get("/monitor", params={"limit": 1})).json()] == [3]


async def test_monitor_summary_computes_p95_and_row_trend():
    # #407: the summary distils the durable-store telemetry — p95 files-per-mirror,
    # p95 cold-wake restore latency, and the WorkspaceFile row-count trend.
    m = InMemoryMonitor()
    for n in range(1, 21):  # 20 mirror samples, n_files = 1..20
        m.record({"kind": "mirror", "n_files": n, "elapsed_ms": 0, "t": 1000})
    for ms in range(1, 21):  # 20 restore samples, elapsed = 1..20 ms
        m.record({"kind": "restore", "elapsed_ms": ms, "t": 1000})
    m.record({"kind": "ws_census", "t": 1000, "total_workspacefile_rows": 5})
    m.record({"kind": "ws_census", "t": 2000, "total_workspacefile_rows": 8})
    async with AsyncClient(transport=ASGITransport(app=_app(m)), base_url="http://t") as c:
        body = (await c.get("/monitor/summary")).json()
    assert body["p95_n_files"] == 19  # nearest-rank p95 of 1..20
    assert body["p95_restore_ms"] == 19
    assert body["total_rows_trend"] == [{"t": 1000, "rows": 5}, {"t": 2000, "rows": 8}]
    assert body["n_mirror_samples"] == 20
    assert body["n_restore_samples"] == 20
    assert body["window_days"] is None


async def test_monitor_summary_days_window_filters_by_age():
    m = InMemoryMonitor()
    m.record({"kind": "mirror", "n_files": 5, "elapsed_ms": 0, "t": 1000})  # ancient → dropped
    now_ms = int(time.time() * 1000)
    m.record({"kind": "restore", "elapsed_ms": 7, "t": now_ms})  # recent → kept
    async with AsyncClient(transport=ASGITransport(app=_app(m)), base_url="http://t") as c:
        body = (await c.get("/monitor/summary", params={"days": 1})).json()
    assert body["p95_n_files"] is None  # the only mirror sample aged out → no data
    assert body["n_mirror_samples"] == 0
    assert body["p95_restore_ms"] == 7  # the recent restore survived the 1-day window
    assert body["window_days"] == 1


async def test_monitor_stream_endpoint_returns_an_sse_response():
    # Invoke the handler directly: it returns a StreamingResponse without the
    # (infinite) body being consumed, so we can assert the wiring + media type.
    resp = await _route(_app(InMemoryMonitor()), "/monitor/stream")(group_id=None)
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
