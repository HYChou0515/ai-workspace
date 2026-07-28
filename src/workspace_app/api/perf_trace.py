"""perf_trace — per-request cost accounting, for finding where a slow request's
wall clock actually went.

Reasoning from call counts times an assumed latency is circular: the latency is
itself back-derived from the number being explained. This measures instead.

One line per request::

    perf: GET /a/pm/items/X/files 6821ms db=6/412ms sandbox=3/1204ms other=5205ms
          inflight=4 walk=137 bytes=9210

Reading it:

* ``db`` — blocking specstar round-trips. specstar is a SYNCHRONOUS api, so on
  an ``async def`` route every one of these holds the event loop and stalls
  every other in-flight request for its duration.
* ``sandbox`` — sandbox-host calls; ``walk`` is the count of entries the
  whole-workspace traversal returned, which settles how big the tree really is.
* ``other`` — **the residual**: wall clock the request did NOT spend inside its
  own measured calls. Large residual + small ``db``/``sandbox`` + ``inflight>1``
  is queueing: the request was waiting for the loop, not for itself.

The loop-lag watchdog is the independent witness — it only ever sleeps, so a
delay beyond its own sleep is time the loop could not run anything, reported
without trusting any request's self-accounting.

Off unless ``WORKSPACE_PERF_TRACE=1``. Per-call detail is logged only for
requests slower than ``WORKSPACE_PERF_TRACE_SLOW_MS`` (default 500) so a quiet
endpoint stays one line while a slow one explains itself without a second deploy.

Instrumentation must never be the reason a request fails: every hook here
swallows its own errors and falls through to the real call.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from starlette.types import Scope

logger = logging.getLogger(__name__)

# Bumped by hand whenever this module changes in a way an operator must be
# able to confirm from the logs. "Which build is actually running?" is not a
# question worth spending a deploy on.
_BUILD = 3

_ENV = "WORKSPACE_PERF_TRACE"
_ENV_SLOW_MS = "WORKSPACE_PERF_TRACE_SLOW_MS"

# Loop stalls below this are ordinary scheduling noise; above it, something ran
# to completion without yielding — which is what we are hunting.
_LAG_THRESHOLD_S = 0.25
_LAG_POLL_S = 0.1
_DEFAULT_SLOW_MS = 500.0


def enabled() -> bool:
    """Off unless switched on. It patches methods on two hot classes and logs a
    line per request — fine while it is answering a question, not something to
    leave running by default. The knob lives in the configmap, so turning it on
    costs an edit rather than an image."""
    return os.environ.get(_ENV, "0") == "1"


def _slow_ms() -> float:
    try:
        return float(os.environ.get(_ENV_SLOW_MS, _DEFAULT_SLOW_MS))
    except ValueError:
        return _DEFAULT_SLOW_MS


@dataclass
class _Acc:
    """One request's tally. Lives in a ContextVar so the hooks can find it
    without threading an argument through every call site."""

    db_calls: int = 0
    db_s: float = 0.0
    sandbox_calls: int = 0
    sandbox_s: float = 0.0
    walk_entries: int = 0
    detail: list[tuple[str, str, float]] = field(default_factory=list)

    def add(self, kind: str, label: str, elapsed: float) -> None:
        if kind == "db":
            self.db_calls += 1
            self.db_s += elapsed
        else:
            self.sandbox_calls += 1
            self.sandbox_s += elapsed
        self.detail.append((kind, label, elapsed))


_current: contextvars.ContextVar[_Acc | None] = contextvars.ContextVar("perf_acc", default=None)

# How many requests are in flight when one arrives. Four concurrent requests
# that each take as long as the whole batch are one queue, not four slow
# requests — and that is invisible without knowing they overlapped.
_inflight = 0


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
        # Suppressing here is the opposite of the durability suppression in
        # `turns.py` — losing one accounting entry costs a log field, and the
        # measurement must never be the reason a real call fails.
        with contextlib.suppress(Exception):
            acc.add(kind, label, time.monotonic() - started)


# ── patching ────────────────────────────────────────────────────────────────

_DB_METHODS = ("get", "get_meta", "list_resources", "create", "update", "exp_aggregate_by")

# `walk` and `disk_usage` traverse the WHOLE workspace, so they are the ones a
# slow file tree most likely hides behind.
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


def _patch_sync(cls: type, name: str) -> None:
    original = getattr(cls, name, None)
    if original is None or (cls, name) in _patched:
        return
    _patched.add((cls, name))

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        label = f"{name}:{getattr(self, 'resource_name', cls.__name__)}"
        with _timed("db", label):
            return original(self, *args, **kwargs)

    setattr(cls, name, wrapper)


def _patch_async(cls: type, name: str) -> None:
    original = getattr(cls, name, None)
    if original is None or (cls, name) in _patched:
        return
    _patched.add((cls, name))

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with _timed("sandbox", name):
            result = await original(self, *args, **kwargs)
        if name == "walk":
            # The size of the traversal, not just its duration: "the tree is
            # small" and "the tree is huge" call for opposite fixes, and this is
            # the only place the real answer exists.
            acc = _current.get()
            if acc is not None:
                with contextlib.suppress(Exception):  # an exotic backend may not size
                    acc.walk_entries += len(result)
        return result

    setattr(cls, name, wrapper)


def _install_own_handler() -> None:
    """Emit through our OWN stdout handler instead of the ambient config.

    This app configures Python logging nowhere — ``uvicorn.run`` is called with
    no ``log_config`` and no ``log_level``, and uvicorn's defaults only touch its
    own loggers. So ``workspace_app.*`` records fall through to the stdlib's
    handler of last resort, which drops everything below WARNING: an INFO-level
    diagnostic emits absolutely nothing, and the only way to discover that is to
    deploy and see an empty log.

    Owning the handler (and ``propagate = False``) makes these lines appear
    regardless of what does or doesn't configure logging around them. The
    timestamp is millisecond-resolution on purpose: proving that four requests
    OVERLAPPED needs their start and end times, not just their durations."""
    if any(getattr(h, "_perf_trace", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%H:%M:%S"))
    handler._perf_trace = True  # ty: ignore[unresolved-attribute]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def install(spec: Any, sandbox: Any) -> None:
    """Patch the classes whose calls we want attributed.

    Patching the CLASS rather than wrapping the instance is deliberate: the
    composition root branches on ``isinstance(sandbox, HttpSandbox)`` to decide
    whether to wire the shared address store (#366), and a proxy would silently
    turn that off."""
    _install_own_handler()
    try:
        from workspace_app.apps.registry import registered_apps

        models = list(registered_apps().values())
        if models:
            rm_cls = type(spec.get_resource_manager(models[0]))
            for name in _DB_METHODS:
                _patch_sync(rm_cls, name)
        if sandbox is not None:
            for name in _SANDBOX_METHODS:
                _patch_async(type(sandbox), name)
    except Exception:  # noqa: BLE001 — a diagnostic must not stop the app booting
        logger.exception("perf_trace: install failed - continuing without tracing")
        return
    logger.warning(
        "perf_trace: ACTIVE build=%d - one 'perf:' line per request, per-call detail "
        "above %.0fms. Set %s=0 to silence.",
        _BUILD,
        _slow_ms(),
        _ENV,
    )
    # Emit a specimen through the EXACT path the request lines use. If this line
    # is absent from the logs, the request lines were never going to appear
    # either, and the reason is here (build, level, handler) rather than in the
    # middleware — which is the difference between reading a log and spending
    # another deploy to find out.
    logger.info(
        "perf: SELFTEST - if you can read this, per-request lines will appear too (build=%d)",
        _BUILD,
    )


# ── per-request summary ─────────────────────────────────────────────────────


class PerfTraceMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware, which buffers response bodies — this
    app streams SSE). Brackets the request, then logs one summary line."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        global _inflight
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        acc = _Acc()
        token = _current.set(acc)
        _inflight += 1
        arrived_with = _inflight
        started = time.monotonic()
        sent = 0

        async def counting_send(message: dict[str, Any]) -> None:
            nonlocal sent
            if message.get("type") == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, counting_send)
        finally:
            _inflight -= 1
            _current.reset(token)
            # Never let logging break a response.
            with contextlib.suppress(Exception):
                self._log(scope, acc, time.monotonic() - started, arrived_with, sent)

    @staticmethod
    def _log(scope: Scope, acc: _Acc, total: float, inflight: int, sent: int) -> None:
        other = total - acc.db_s - acc.sandbox_s
        logger.info(
            "perf: %s %s %dms db=%d/%dms sandbox=%d/%dms other=%dms inflight=%d walk=%d bytes=%d",
            scope.get("method", "?"),
            scope.get("path", "?"),
            total * 1000,
            acc.db_calls,
            acc.db_s * 1000,
            acc.sandbox_calls,
            acc.sandbox_s * 1000,
            other * 1000,
            inflight,
            acc.walk_entries,
            sent,
        )
        # Per-call detail only where it is worth the volume — a slow request
        # explains itself without a second deploy, a fast one stays one line.
        if total * 1000 >= _slow_ms():
            for kind, label, elapsed in acc.detail:
                logger.info("perf:   %-8s %-44s %dms", kind, label, elapsed * 1000)


# ── loop-lag watchdog ───────────────────────────────────────────────────────


async def _watch_loop_lag(threshold: float = _LAG_THRESHOLD_S) -> None:
    """Log whenever the event loop was blocked longer than ``threshold``.

    Independent of any request: this task only ever sleeps, so the delay it
    observes beyond its own sleep IS time the loop spent unable to run anything.
    It is the direct evidence for (or against) blocking work on an async route —
    the thing that makes concurrent requests serialize instead of overlap.
    """
    while True:
        started = time.monotonic()
        await asyncio.sleep(_LAG_POLL_S)
        lag = time.monotonic() - started - _LAG_POLL_S
        if lag > threshold:
            logger.warning("perf: event loop BLOCKED for %dms (inflight=%d)", lag * 1000, _inflight)


def start_loop_watchdog() -> asyncio.Task[None]:
    return asyncio.create_task(_watch_loop_lag())
