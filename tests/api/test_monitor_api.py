"""Monitor HTTP surface (issue #11): the snapshot endpoint, plus that the
stream endpoint hands back an SSE response. The stream's generator logic is
unit-tested in tests/monitor — it's an infinite body, which the in-process
ASGITransport buffers, so we invoke the route handler directly rather than
over HTTP."""

from __future__ import annotations

from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.monitor import IMonitor, InMemoryMonitor
from workspace_app.sandbox.mock import MockSandbox


def _route(app, path: str):
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) == path)


def _app(monitor: IMonitor):
    return create_app(
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


async def test_monitor_stream_endpoint_returns_an_sse_response():
    # Invoke the handler directly: it returns a StreamingResponse without the
    # (infinite) body being consumed, so we can assert the wiring + media type.
    resp = await _route(_app(InMemoryMonitor()), "/monitor/stream")(group_id=None)
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
