"""The request counter (docs/plan-capacity-usage.md §6.1, §6.2): every request
under its route template, its App, and the person its route asked about — the
deploy's reader asked once at most — and an event stream as a stream."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from workspace_app.capacity.middleware import UsageMiddleware
from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.scope import current, recording_reader
from workspace_app.capacity.sink import UsageSink


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


class _Reader:
    def __init__(self, user: str = "alice", fail: bool = False) -> None:
        self.user = user
        self.fail = fail
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("no session cookie")
        return self.user


def _app(recorder: UsageRecorder, reader: _Reader) -> tuple[FastAPI, list]:
    """A small app shaped like the real one: routes under `/api`, asking the
    request's person through the same wrapped reader `create_app` hands them."""
    ask = recording_reader(reader)
    seen_scopes: list = []
    api = APIRouter(prefix="/api")

    @api.get("/a/{slug}/items/{item_id}/files")
    def files(slug: str, item_id: str):
        ask()
        seen_scopes.append(current())
        return {"ok": True}

    @api.get("/public")
    def public():
        return {"ok": True}

    @api.get("/a/{slug}/gone")
    def gone(slug: str):
        ask()
        raise HTTPException(status_code=404, detail="no such App")

    @api.get("/a/{slug}/crash")
    def crash(slug: str):
        ask()
        raise RuntimeError("unhandled")

    @api.get("/boom")
    def boom():
        ask()
        raise HTTPException(status_code=500, detail="boom")

    @api.get("/a/{slug}/items/{item_id}/stream")
    async def stream(slug: str, item_id: str):
        ask()

        async def gen():
            for _ in range(3):
                yield "data: x\n\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(gen(), media_type="text/event-stream")

    app = FastAPI()
    app.include_router(api)
    app.add_middleware(UsageMiddleware, recorder=recorder, get_user_id=reader)
    return app, seen_scopes


def _run(reader: _Reader, *paths: str) -> tuple[list[dict], list]:
    sink = _ListSink()
    recorder = UsageRecorder(sink, component="api", pod="p", window_s=300, queue_max=1000)
    app, scopes = _app(recorder, reader)
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in paths:
            client.get(path)
    recorder.close()
    return [d["usage"] for d in sink.docs if d["usage"]["kind"] == "work"], scopes


def test_a_request_is_counted_under_its_template_app_and_person_asking_once():
    reader = _Reader("alice")

    rows, scopes = _run(reader, "/api/a/pm/items/i1/files", "/api/a/pm/items/i2/files")

    (row,) = rows
    assert (row["work"], row["app"], row["user"], row["count"]) == (
        "req:GET /api/a/{slug}/items/{item_id}/files",
        "pm",
        "alice",
        2,
    )
    assert sum(row.get(f"h{i}", 0) for i in range(10)) == 2
    assert reader.calls == 2, "once per request: the route's own call, nothing extra"
    assert [s.user for s in scopes] == ["alice", "alice"]


def test_a_route_that_never_asks_gets_one_question_from_the_counter():
    reader = _Reader("bob")

    rows, _ = _run(reader, "/api/public")

    assert [(r["work"], r["user"]) for r in rows] == [("req:GET /api/public", "bob")]
    assert reader.calls == 1


def test_a_reader_that_cannot_answer_counts_the_request_as_unknown():
    rows, _ = _run(_Reader(fail=True), "/api/public")

    assert [r["user"] for r in rows] == ["unknown"]


def test_a_failing_request_is_still_counted():
    rows, _ = _run(_Reader("carol"), "/api/boom")

    assert [(r["work"], r["user"], r["count"]) for r in rows] == [("req:GET /api/boom", "carol", 1)]


def test_requests_no_route_matched_are_static_outside_the_api_and_unmatched_inside():
    rows, _ = _run(_Reader("dave"), "/assets/app.js", "/api/nope")

    assert sorted(r["work"] for r in rows) == ["req:GET (static)", "req:GET (unmatched)"]


def test_an_event_stream_is_counted_as_a_stream_with_its_connection_time():
    rows, _ = _run(_Reader("erin"), "/api/a/pm/items/i1/stream")

    (row,) = rows
    assert row["work"] == "stream:/api/a/{slug}/items/{item_id}/stream"
    assert (row["user"], row["app"], row["count"]) == ("erin", "pm", 1)
    assert row["seconds"] >= 0.1
    assert not [k for k in row if k.startswith("h")], "a stream has no latency"


def test_a_url_no_route_answered_names_no_app_and_asks_nobody_who():
    """Anyone, signed in or not, can send /api/a/<anything>/…: a slug read off
    the raw path minted an App per URL — a row per window each, and a series
    in the analysis. And a request no route answered (a static file, a URL
    nothing serves) is nobody's to ask the deploy's reader about."""
    reader = _Reader("dave")

    rows, _ = _run(reader, *(f"/api/a/x{i}/nope" for i in range(5)), "/assets/app.js")

    assert {r["app"] for r in rows} == {""}
    assert {r["user"] for r in rows} == {"unknown"}
    assert reader.calls == 0


def test_an_app_route_that_refuses_its_slug_names_no_app():
    """A route matched, but its slug is no App: the slug is the caller's word."""
    rows, _ = _run(_Reader("erin"), "/api/a/zz1/gone", "/api/a/zz2/gone")

    assert {r["app"] for r in rows} == {""}
    assert {r["user"] for r in rows} == {"erin"}


def test_a_route_that_crashed_names_no_app():
    """An unhandled exception sends no response from inside the counter (the
    500 comes from outside it): no answer is no acceptance of the slug."""
    rows, _ = _run(_Reader("erin"), "/api/a/zz3/crash")

    assert {r["app"] for r in rows} == {""}


def test_a_kubernetes_probe_is_system_work_and_asks_nobody_who():
    """The readiness probe hits every API pod every few seconds: counted as
    an unidentified person it was most of the "unknown" share on a quiet
    deployment. It is work no person asked for."""
    sink = _ListSink()
    recorder = UsageRecorder(sink, component="api", pod="p", window_s=300, queue_max=1000)
    reader = _Reader("dave")
    app, _ = _app(recorder, reader)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/api/public", headers={"user-agent": "kube-probe/1.29"})
    recorder.close()

    rows = [d["usage"] for d in sink.docs if d["usage"]["kind"] == "work"]
    assert [r["user"] for r in rows] == ["system"]
    assert reader.calls == 0
