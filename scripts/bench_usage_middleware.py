"""What the request counter costs per request (docs/plan-capacity-usage.md E17).

Drives a small FastAPI app straight through ASGI (no sockets, so the number is
the middleware's own work, not network noise): the same route with and without
`UsageMiddleware`, alternating rounds so drift hits both alike. Prints the
median per-request time of each and the difference.

    uv run python scripts/bench_usage_middleware.py [--requests 20000] [--rounds 7]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from fastapi import APIRouter, FastAPI

from workspace_app.capacity.middleware import UsageMiddleware
from workspace_app.capacity.recorder import UsageRecorder
from workspace_app.capacity.scope import recording_reader
from workspace_app.capacity.sink import UsageSink


class _NullSink(UsageSink):
    def write(self, documents):
        pass


def _app(counted: bool) -> FastAPI:
    reader = lambda: "alice"  # noqa: E731
    ask = recording_reader(reader) if counted else reader
    api = APIRouter(prefix="/api")

    @api.get("/a/{slug}/items/{item_id}/files")
    async def files(slug: str, item_id: str):
        ask()
        return {"ok": True}

    app = FastAPI()
    app.include_router(api)
    if counted:
        recorder = UsageRecorder(_NullSink(), component="api", pod="bench", queue_max=100_000)
        app.add_middleware(UsageMiddleware, recorder=recorder, get_user_id=reader)
    return app


async def _drive(app: FastAPI, n: int) -> float:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/a/pm/items/i1/files",
        "raw_path": b"/api/a/pm/items/i1/files",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"bench")],
        "server": ("bench", 80),
        "client": ("127.0.0.1", 1),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        pass

    started = time.perf_counter()
    for _ in range(n):
        await app(dict(scope), receive, send)
    return (time.perf_counter() - started) / n


async def main(requests: int, rounds: int) -> None:
    plain, counted = _app(False), _app(True)
    await _drive(plain, 500)  # warm both: routing caches, first-call imports
    await _drive(counted, 500)
    without, with_ = [], []
    for _ in range(rounds):
        without.append(await _drive(plain, requests))
        with_.append(await _drive(counted, requests))
    a, b = statistics.median(without) * 1e6, statistics.median(with_) * 1e6
    print(f"without the counter: {a:8.1f} µs/request (median of {rounds} rounds × {requests})")
    print(f"with the counter:    {b:8.1f} µs/request")
    print(f"the counter adds:    {b - a:8.1f} µs/request ({(b - a) / a:+.1%})")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="What the request counter costs per request.")
    p.add_argument("--requests", type=int, default=20_000)
    p.add_argument("--rounds", type=int, default=7)
    args = p.parse_args()
    asyncio.run(main(args.requests, args.rounds))
