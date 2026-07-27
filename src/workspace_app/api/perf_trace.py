"""perf_trace — opt-in per-request cost accounting, for finding where a slow
request's wall clock actually went.

Reasoning from call counts times an assumed latency is circular: the latency is
itself back-derived from the number being explained. This measures instead. One
line per request:

    perf: GET /a/pm/items/X/files 6821ms db=6/412ms sandbox=3/1204ms other=5205ms

`other` is the residual — wall clock the request did NOT spend inside its own
measured calls. A large residual on a request that issued little I/O is the
signature of **waiting for the event loop**: an ``async def`` route calling
blocking code (specstar is a synchronous API) stalls every other in-flight
request for the duration, so concurrent requests serialize instead of
overlapping and each observes the whole queue. The loop-lag watchdog confirms it
independently, from outside any request.

Off unless ``WORKSPACE_PERF_TRACE=1``. It patches methods on two hot classes, so
it is deliberately opt-in, and meant to be removed once the question is answered.
Set ``WORKSPACE_PERF_TRACE_DETAIL=1`` to log every individual call (noisy — use
it once you know which half to look at).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from starlette.types import Scope

logger = logging.getLogger(__name__)

_ENV = "WORKSPACE_PERF_TRACE"
_ENV_DETAIL = "WORKSPACE_PERF_TRACE_DETAIL"

# Loop stalls shorter than this are normal scheduling noise; longer ones mean
# something ran to completion without yielding, which is what we are hunting.
_LAG_THRESHOLD_S = 0.25
_LAG_POLL_S = 0.1


@dataclass
class _Acc:
    """One request's tally. Lives in a ContextVar so the wrappers can find it
    without threading an argument through every call site."""

    db_calls: int = 0
    db_s: float = 0.0
    sandbox_calls: int = 0
    sandbox_s: float = 0.0
    detail: list[tuple[str, str, float]] = field(default_factory=list)

    def add(self, kind: str, label: str, elapsed: float) -> None:
        if kind == "db":
            self.db_calls += 1
            self.db_s += elapsed
        else:
            self.sandbox_calls += 1
            self.sandbox_s += elapsed
        if _detail_enabled():
            self.detail.append((kind, label, elapsed))


_current: contextvars.ContextVar[_Acc | None] = contextvars.ContextVar("perf_acc", default=None)


def enabled() -> bool:
    return os.environ.get(_ENV, "") == "1"


def _detail_enabled() -> bool:
    return os.environ.get(_ENV_DETAIL, "") == "1"


@contextmanager
def _timed(kind: str, label: str) -> Iterator[None]:
    """Charge one call's wall clock to the in-flight request, if any. Outside a
    request (a sweeper, a background job) there is no accumulator and this is a
    cheap no-op — the point is per-request attribution, not global totals."""
    acc = _current.get()
    if acc is None:
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        acc.add(kind, label, time.monotonic() - started)


# ── patching ────────────────────────────────────────────────────────────────

# specstar's ResourceManager is a SYNCHRONOUS API — every one of these is a
# blocking database round-trip, and on an `async def` route it runs on the event
# loop. That is exactly what we need attributed per request.
_DB_METHODS = ("get", "get_meta", "list_resources", "create", "update", "exp_aggregate_by")

# The sandbox ops a file/entity request actually makes. `walk` and `disk_usage`
# traverse the whole workspace, so they are the ones worth separating from the
# rest by name in the detail log.
_SANDBOX_METHODS = (
    "walk",
    "disk_usage",
    "size_of",
    "exists",
    "download",
    "upload",
    "download_to_file",
    "upload_file",
    "delete",
    "mkdir",
    "is_ready",
    "mark_ready",
    "persist",
)

_patched: set[tuple[type, str]] = set()


def _patch_sync(cls: type, name: str, kind: str) -> None:
    original = getattr(cls, name, None)
    if original is None or (cls, name) in _patched:
        return
    _patched.add((cls, name))

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with _timed(kind, f"{name}:{getattr(self, 'resource_name', cls.__name__)}"):
            return original(self, *args, **kwargs)

    setattr(cls, name, wrapper)


def _patch_async(cls: type, name: str, kind: str) -> None:
    original = getattr(cls, name, None)
    if original is None or (cls, name) in _patched:
        return
    _patched.add((cls, name))

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with _timed(kind, name):
            return await original(self, *args, **kwargs)

    setattr(cls, name, wrapper)


def install(spec: Any, sandbox: Any) -> None:
    """Patch the classes whose calls we want attributed. Patching the CLASS (not
    wrapping the instance in a proxy) is deliberate: the composition root branches
    on `isinstance(sandbox, HttpSandbox)` to decide whether to wire the shared
    address store, and a proxy would silently turn that off."""
    from workspace_app.apps.registry import registered_apps

    models = list(registered_apps().values())
    if models:
        rm_cls = type(spec.get_resource_manager(models[0]))
        for name in _DB_METHODS:
            _patch_sync(rm_cls, name, "db")
    if sandbox is not None:
        for name in _SANDBOX_METHODS:
            _patch_async(type(sandbox), name, "sandbox")
    logger.warning(
        "perf_trace: INSTALLED (%s=1) - per-request cost lines are being logged; "
        "this patches hot classes and is meant to be temporary",
        _ENV,
    )


# ── per-request summary ─────────────────────────────────────────────────────


class PerfTraceMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware, which buffers response bodies — this
    app streams SSE). Brackets the request, then logs one summary line."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        acc = _Acc()
        token = _current.set(acc)
        started = time.monotonic()
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
            total = time.monotonic() - started
            other = total - acc.db_s - acc.sandbox_s
            logger.info(
                "perf: %s %s %dms db=%d/%dms sandbox=%d/%dms other=%dms",
                scope.get("method", "?"),
                scope.get("path", "?"),
                total * 1000,
                acc.db_calls,
                acc.db_s * 1000,
                acc.sandbox_calls,
                acc.sandbox_s * 1000,
                other * 1000,
            )
            for kind, label, elapsed in acc.detail:
                logger.info("perf:   %-8s %-40s %dms", kind, label, elapsed * 1000)


# ── loop-lag watchdog ───────────────────────────────────────────────────────


async def _watch_loop_lag(threshold: float = _LAG_THRESHOLD_S) -> None:
    """Log whenever the event loop was blocked longer than `threshold`.

    Independent of any request: this task only ever sleeps, so the delay it
    observes beyond its own sleep IS time the loop spent unable to run anything.
    It is the direct evidence for (or against) blocking work on an async route —
    the thing that makes concurrent requests serialize.
    """
    while True:
        started = time.monotonic()
        await asyncio.sleep(_LAG_POLL_S)
        lag = time.monotonic() - started - _LAG_POLL_S
        if lag > threshold:
            logger.warning("perf: event loop BLOCKED for %dms", lag * 1000)


def start_loop_watchdog() -> asyncio.Task[None]:
    return asyncio.create_task(_watch_loop_lag())
