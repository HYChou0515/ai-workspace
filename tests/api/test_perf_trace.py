"""The cost accounting has to be right about the costs, or it just moves the
guessing somewhere harder to see. These pin the two claims its output makes:
the per-request line attributes the calls a request really issued, and the
watchdog reports a blocked loop as blocked."""

from __future__ import annotations

import asyncio
import logging
import re

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app, perf_trace
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient as ApiTestClient


@pytest.fixture
def traced(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WORKSPACE_PERF_TRACE", "1")
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    return ApiTestClient(app)


def _perf_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("perf: ")]


def test_off_by_default_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """It patches hot classes, so it must stay invisible until asked for."""
    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    client = ApiTestClient(app)
    item = client.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    with caplog.at_level(logging.INFO):
        client.get(f"/a/pm/items/{item['resource_id']}/files")
    assert _perf_lines(caplog) == []


def test_request_line_counts_the_database_round_trips_it_made(
    traced: ApiTestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The number that matters: how many BLOCKING specstar calls one read of the
    file tree costs. specstar is a synchronous API, so on an `async def` route
    each of these holds the event loop — which is why concurrent requests
    serialize rather than overlap, and why a count is worth logging at all."""
    item = traced.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    with caplog.at_level(logging.INFO):
        traced.get(f"/a/pm/items/{item['resource_id']}/files")

    line = next(m for m in _perf_lines(caplog) if "/files" in m)
    match = re.search(r"db=(\d+)/", line)
    assert match is not None, line
    db = int(match.group(1))
    # Six today: four are `find_work_item` guessing which App owns the id (the
    # miss costs a get AND a get_meta), one is a get_meta the guess already did,
    # one is the caller's groups. A drop here is the fix landing, not a break.
    assert db >= 5, line
    assert "other=" in line  # the residual — wall clock spent outside its own calls


def test_request_line_separates_sandbox_work_from_database_work(
    traced: ApiTestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Splitting the two is the whole point: a slow file tree is either the
    workspace traversal or the blocking metadata lookups in front of it, and the
    fix is completely different depending on which."""
    item = traced.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    iid = item["resource_id"]
    traced.put(f"/a/pm/items/{iid}/files/notes.md", json={"content": "hi"})
    with caplog.at_level(logging.INFO):
        traced.get(f"/a/pm/items/{iid}/files")

    line = next(m for m in _perf_lines(caplog) if "/files" in m)
    assert re.search(r"db=\d+/\d+ms", line), line
    assert re.search(r"sandbox=\d+/\d+ms", line), line


async def test_watchdog_reports_a_loop_that_could_not_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The independent witness. It only ever sleeps, so a delay beyond its own
    sleep is time nothing else could run either — the signature of blocking work
    on an async route, reported without trusting any request's self-accounting."""
    task = perf_trace.start_loop_watchdog()
    try:
        with caplog.at_level(logging.WARNING):
            await asyncio.sleep(0)
            # Block the loop the way a synchronous database call does.
            import time as _t

            _t.sleep(0.5)
            await asyncio.sleep(0.2)
    finally:
        task.cancel()
    assert any("event loop BLOCKED" in r.getMessage() for r in caplog.records)
