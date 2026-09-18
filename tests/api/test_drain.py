"""SIGTERM runs our shutdown (plan-graceful-shutdown P2).

uvicorn's shutdown waits for every open connection to finish its response
before it sends the lifespan its shutdown event — with the default
`timeout_graceful_shutdown=None`, forever. An SSE stream heartbeats until the
tab closes, so with one chat open the turn drain and the sandbox teardown never
ran: kubelet SIGKILLed the pod at the grace period. The `Drain` begins at the
signal, on the loop: readiness off, every live stream ended, so uvicorn's wait
ends and the lifespan runs.
"""

from __future__ import annotations

import asyncio
import signal
import threading
import time
from datetime import timedelta
from typing import Any, cast

import uvicorn

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.drain import Drain, DrainingServer
from workspace_app.api.events import MessageDelta, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.monitor import InMemoryMonitor
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


def _on_the_loop(client: TestClient, fn) -> None:  # noqa: ANN001
    """Run `fn` ON the app's loop, from the test thread. The signal handler
    schedules the drain with `call_soon_threadsafe`; the TestClient's portal is
    the same door. Calling a closer directly from here would end the streams
    from a foreign thread — the race P1 removed from the sink."""
    assert client.portal is not None  # inside `with TestClient(...)`
    client.portal.call(fn)


def _begin_on_the_loop(client: TestClient) -> None:
    drain = cast(Any, client.app).state.drain
    _on_the_loop(client, drain.begin)


def _app(**kw):
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([MessageDelta(text="hi"), RunDone()]),
        **kw,
    )
    return app, spec


def test_readiness_is_off_from_the_first_moment_of_the_drain() -> None:
    app, _ = _app()
    with TestClient(app) as client:
        assert client.get("/api/readyz").status_code == 200
        _begin_on_the_loop(client)
        resp = client.get("/api/readyz")
    assert resp.status_code == 503
    assert "drain" in resp.text.lower()


def _hold_stream(client: TestClient, url: str, ended: threading.Event) -> None:
    """Hold an SSE response open on a thread until the server ends it. The
    TestClient delivers a streaming body only once it has ENDED, so the only
    observable here is the end — which is exactly the claim under test."""

    def run() -> None:
        with client.stream("GET", url):
            pass
        ended.set()

    threading.Thread(target=run, daemon=True).start()


def _wait_until(pred, budget_s: float = 5.0) -> bool:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_the_drain_ends_every_open_chat_stream() -> None:
    """The stream must END (EOF) — that is the FE's reconnect signal — not sit
    on its heartbeat until the grace period runs out."""
    app, spec = _app()
    item_id = register_rca_item(spec)
    engine = app.state.turn_engines[0]
    ended = threading.Event()
    with TestClient(app) as client:
        _hold_stream(client, f"/a/rca/items/{item_id}/stream", ended)
        # "The stream is open" = the engine holds its subscriber.
        assert _wait_until(lambda: any(s.subscribers for s in engine._ws_sessions.values())), (
            "the stream never subscribed"
        )
        assert not ended.is_set()
        _begin_on_the_loop(client)
        try:
            assert ended.wait(2), "the drain left the chat stream open"
        finally:
            # A stream the drain did not end would hold the TestClient's exit
            # forever: a hang is the one failure a suite cannot report. End it
            # ourselves so the assertion above is what gets reported.
            if not ended.is_set():
                _on_the_loop(client, engine.close_all_streams)


def test_the_drain_ends_the_monitor_stream_too() -> None:
    mon = InMemoryMonitor()
    app, _ = _app(monitor=mon)
    ended = threading.Event()
    with TestClient(app) as client:
        _hold_stream(client, "/api/monitor/stream", ended)
        assert _wait_until(lambda: bool(mon._subs)), "the stream never subscribed"
        _begin_on_the_loop(client)
        try:
            assert ended.wait(2), "the drain left the monitor stream open"
        finally:
            if not ended.is_set():
                _on_the_loop(client, mon.close_streams)


def test_begin_is_idempotent_and_survives_a_closer_that_raises() -> None:
    """Two signals in a row, and one broken stream source, must not stop the
    other sources from being ended — a drain that half-runs is the old bug."""
    calls: list[str] = []
    drain = Drain()
    drain.on_begin(lambda: calls.append("a"))
    drain.on_begin(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    drain.on_begin(lambda: calls.append("c"))
    drain.begin()
    drain.begin()
    assert drain.draining
    assert calls == ["a", "c"]


async def test_the_server_seam_schedules_the_drain_on_the_loop_and_still_exits() -> None:
    """uvicorn's `handle_exit` runs in the signal handler, on the loop thread
    but outside any task. The subclass hands the drain to the loop and then
    lets uvicorn do what it always did (`should_exit`)."""
    drain = Drain()
    began = asyncio.Event()
    drain.on_begin(began.set)
    server = DrainingServer(uvicorn.Config(app=lambda *_: None), drain=drain)
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit
    await asyncio.wait_for(began.wait(), 1.0)


def test_the_lifespan_drains_turns_against_one_deadline() -> None:
    """One number, one WINDOW: the budget the lifespan gives in-flight turns
    is the `shutdown_budget` handed to `create_app`, and every engine drains
    against the same deadline — the second engine gets what the first left,
    not a fresh budget of its own (round 1: two engines, each with the whole
    budget, then the coordinators with a third)."""
    app, _ = _app(shutdown_budget=timedelta(seconds=7))
    seen: list[float] = []
    with TestClient(app):
        for engine in app.state.turn_engines:
            real = engine.aclose

            async def spy(timeout: float = 10.0, *, _real=real, **kw) -> None:  # noqa: ANN003
                seen.append(timeout)
                await asyncio.sleep(0.2)  # this engine spends some of the window
                await _real(timeout=timeout, **kw)

            engine.aclose = spy  # type: ignore[method-assign]
    assert len(seen) == 2
    assert 6.5 < seen[0] <= 7.0
    assert seen[1] <= seen[0] - 0.2  # what the first one left, not 7 again


def test_the_coordinator_drain_is_bounded_by_the_same_budget() -> None:
    """All-in-one only (`run_consumers=True`; production runs the API as a pure
    producer and skips this block): the lifespan drained the job queues to
    EMPTY before exiting. An index job can take minutes, and the boot itself
    enqueues the Help docs — measured: 16 s on a fresh local boot — so such a
    pod could not leave inside its grace period. The drain shares the one
    budget; a job still running when it runs out is left to specstar's
    stale-job recovery, which is what the queue is durable for."""
    import time as _time

    class _NeverDrains:
        async def aclose(self) -> None:
            await asyncio.sleep(30)

    app, _ = _app(shutdown_budget=timedelta(seconds=0.5))
    with TestClient(app):
        app.state.index_coordinator = _NeverDrains()
        t0 = _time.monotonic()
    elapsed = _time.monotonic() - t0
    assert elapsed < 3.0, f"shutdown waited {elapsed:.1f}s on a coordinator past the budget"
