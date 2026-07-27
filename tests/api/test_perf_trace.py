"""The cost accounting has to be right about the costs, or it just moves the
guessing somewhere harder to see. These pin the claims its output makes: the
per-request line attributes the calls a request really issued, it reports the
overlap that turns four parallel requests into one queue, and the watchdog
reports a blocked loop as blocked."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterator

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app, perf_trace
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient as ApiTestClient


@pytest.fixture
def traced() -> ApiTestClient:
    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    return ApiTestClient(app)


@pytest.fixture
def perf_log() -> Iterator[list[str]]:
    """Collect from perf_trace's OWN logger. It deliberately does not propagate
    to the root — that is what makes the lines survive an app which configures
    logging nowhere — so `caplog`, which listens on the root, would see nothing."""
    captured: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = _Collect(logging.INFO)
    perf_trace.logger.addHandler(handler)
    perf_trace.logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        perf_trace.logger.removeHandler(handler)


def _perf_lines(captured: list[str]) -> list[str]:
    return [m for m in captured if m.startswith("perf: ")]


def _field(line: str, name: str) -> int:
    match = re.search(rf"{name}=(\d+)", line)
    assert match is not None, f"no {name}= in {line}"
    return int(match.group(1))


def test_traces_without_being_asked_but_can_be_silenced(
    monkeypatch: pytest.MonkeyPatch, perf_log: list[str]
) -> None:
    """On by default, opt-OUT. This module exists to be deployed once and read;
    a diagnostic that must be remembered is one that ships switched off and
    costs a second deploy to turn on."""
    assert perf_trace.enabled()
    monkeypatch.setenv("WORKSPACE_PERF_TRACE", "0")
    assert not perf_trace.enabled()


def test_request_line_counts_the_database_round_trips_it_made(
    traced: ApiTestClient, perf_log: list[str]
) -> None:
    """The number that matters: how many BLOCKING specstar calls one read of the
    file tree costs. specstar is a synchronous API, so on an `async def` route
    each of these holds the event loop — which is why concurrent requests
    serialize rather than overlap, and why a count is worth logging at all."""
    item = traced.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    traced.get(f"/a/pm/items/{item['resource_id']}/files")

    line = next(m for m in _perf_lines(perf_log) if "/files" in m)
    # Was six: four were `find_work_item` guessing which App owns the id (a
    # miss costs a get AND a get_meta), one a get_meta the guess had already
    # done, one the caller's groups. The id names its own table, so the guessing
    # is gone. Pinned as a CEILING — at ~268ms a round-trip in production, a
    # regression here is a second of latency on every route, not a tidiness
    # issue.
    assert _field(line, "db") <= 4, line
    assert "other=" in line  # the residual — wall clock spent outside its own calls


def test_request_line_separates_sandbox_work_from_database_work(
    traced: ApiTestClient, perf_log: list[str]
) -> None:
    """Splitting the two is the whole point: a slow file tree is either the
    workspace traversal or the blocking metadata lookups in front of it, and the
    fix is completely different depending on which."""
    item = traced.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    iid = item["resource_id"]
    traced.put(f"/a/pm/items/{iid}/files/notes.md", json={"content": "hi"})
    traced.get(f"/a/pm/items/{iid}/files")

    line = next(m for m in _perf_lines(perf_log) if "/files" in m)
    assert re.search(r"db=\d+/\d+ms", line), line
    assert re.search(r"sandbox=\d+/\d+ms", line), line


def test_request_line_reports_overlap_and_response_size(
    traced: ApiTestClient, perf_log: list[str]
) -> None:
    """Four parallel requests that each take as long as the whole batch are one
    queue, not four slow requests — but only if you can see they overlapped.
    `inflight` is that evidence; `bytes` separates a slow handler from a large
    payload."""
    item = traced.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    traced.get(f"/a/pm/items/{item['resource_id']}/files")

    line = next(m for m in _perf_lines(perf_log) if "/files" in m)
    assert _field(line, "inflight") >= 1, line
    assert _field(line, "bytes") > 0, line  # the listing really was sent
    assert "walk=" in line, line


def test_slow_requests_explain_themselves_without_another_deploy(
    monkeypatch: pytest.MonkeyPatch, traced: ApiTestClient, perf_log: list[str]
) -> None:
    """Per-call detail is gated on the request being slow: a quiet endpoint stays
    one line, and the one that is actually slow arrives already itemised — the
    deploy that costs the most is the second one."""
    monkeypatch.setenv("WORKSPACE_PERF_TRACE_SLOW_MS", "0")  # everything counts as slow
    item = traced.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    traced.get(f"/a/pm/items/{item['resource_id']}/files")

    detail = [m for m in _perf_lines(perf_log) if m.startswith("perf:   ")]
    assert any("find_work_item" in m or "get" in m for m in detail), detail


async def test_watchdog_reports_a_loop_that_could_not_run(
    perf_log: list[str],
) -> None:
    """The independent witness. It only ever sleeps, so a delay beyond its own
    sleep is time nothing else could run either — the signature of blocking work
    on an async route, reported without trusting any request's self-accounting."""
    task = perf_trace.start_loop_watchdog()
    try:
        if True:
            await asyncio.sleep(0)
            # Block the loop the way a synchronous database call does.
            import time as _t

            _t.sleep(0.5)
            await asyncio.sleep(0.2)
    finally:
        task.cancel()
    assert any("event loop BLOCKED" in m for m in perf_log)


def test_emits_even_though_the_app_configures_no_logging(
    perf_log: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """The failure that cost a deploy.

    This app configures Python logging NOWHERE — `uvicorn.run` gets no
    `log_config` and no `log_level`, and uvicorn's defaults only touch its own
    loggers. So `workspace_app.*` records reach the stdlib's handler of last
    resort, which drops everything below WARNING, and an INFO-level diagnostic
    emits nothing at all. The only symptom is an empty log after a deploy.

    So perf_trace owns its handler. Pinning it here means the next person to
    change the log level cannot silently take the instrument away again.
    """
    for handler in [h for h in perf_trace.logger.handlers if getattr(h, "_perf_trace", False)]:
        perf_trace.logger.removeHandler(handler)  # rebind to this test's stdout
    logging.getLogger().setLevel(logging.WARNING)  # what production actually has

    app = create_app(
        spec=make_spec(default_user="u"),
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
    )
    client = ApiTestClient(app)
    item = client.post("/a/pm/items", json={"title": "P", "profile": "default"}).json()
    client.get(f"/a/pm/items/{item['resource_id']}/files")

    out = capsys.readouterr().out
    assert "perf: GET" in out, out[-2000:]
    assert perf_trace.logger.level == logging.INFO
