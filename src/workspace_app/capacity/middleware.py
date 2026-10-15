"""The request counter (docs/plan-capacity-usage.md §6.1, §6.2).

A pure ASGI middleware — not `BaseHTTPMiddleware`, which wraps response bodies
and this app streams SSE. For each HTTP request it records one `work` count:

- **What**: the route template FastAPI matched (`req:<METHOD> <template>`),
  read in the `send` wrapper because the router sets `scope["route"]` while
  routing, after this middleware was entered. No route: `(static)` outside
  `/api`, `(unmatched)` inside it.
- **A stream or a request**: a response whose content type is
  `text/event-stream` is a stream, in flight from its first header to its
  end — no list of long-lived routes to keep current.
- **Which App**: the `slug` of the route that matched, and only when the
  response did not refuse it (< 400). Read off the raw path, any URL —
  `/api/a/<anything>/…`, signed in or not — minted an App: a row per window
  each, and a series in the analysis.
- **Who**: the request carries a `RequestIdentity`; the `get_user_id` its
  routes call is `recording_reader`-wrapped, so the first answer a route gets
  is the request's person at no extra cost. An API request whose route never
  asked (auto-CRUD routes, probes) gets one call of the deploy's reader after
  its response; an exception there is `unknown`. A request no route answered
  (a static file, a URL nothing serves) is `unknown` without asking.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from .recorder import Running, UsageRecorder
from .scope import UNKNOWN, RequestIdentity, request_identity

logger = logging.getLogger(__name__)

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Any]
Send = Callable[[Message], Any]

_EVENT_STREAM = b"text/event-stream"


def _slug(scope: Scope) -> str:
    """The App the matched route was called with — never read off the raw path
    (`path_params` exist only once something matched)."""
    slug = scope.get("path_params", {}).get("slug", "")
    return slug if isinstance(slug, str) else ""


def _work(scope: Scope) -> str:
    method = scope.get("method", "GET")
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return f"{method} {template}"
    path = scope.get("path", "")
    under_api = path == "/api" or path.startswith("/api/")
    return f"{method} (unmatched)" if under_api else f"{method} (static)"


def _is_stream(message: Message) -> bool:
    for name, value in message.get("headers", ()):
        if name.lower() == b"content-type":
            return value.split(b";", 1)[0].strip().lower() == _EVENT_STREAM
    return False


class UsageMiddleware:
    def __init__(
        self, app: Any, *, recorder: UsageRecorder, get_user_id: Callable[[], str]
    ) -> None:
        self.app = app
        self._recorder = recorder
        self._get_user_id = get_user_id

    def _who(self, holder: RequestIdentity, scope: Scope) -> str:
        if holder.user is None:
            if scope.get("route") is None:
                return UNKNOWN
            try:
                holder.user = self._get_user_id()
            except Exception:
                holder.user = UNKNOWN
        return holder.user

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        holder = RequestIdentity(tags=self._recorder.llm_tags, app_of=lambda: _slug(scope))
        stream: Running | None = None
        status = 0

        def app() -> str:
            return holder.app if status < 400 else ""

        async def counted_send(message: Message) -> None:
            nonlocal stream, status
            starting = message["type"] == "http.response.start"
            if starting:
                status = int(message.get("status", 0))
            if starting and stream is None and _is_stream(message):
                work = "stream:" + _template(scope)
                stream = self._recorder.begin(self._who(holder, scope), app(), work)
            await send(message)

        try:
            with request_identity(holder):
                await self.app(scope, receive, counted_send)
        finally:
            try:
                if stream is not None:
                    stream.end()
                else:
                    elapsed = time.monotonic() - started
                    self._recorder.add(
                        self._who(holder, scope),
                        app(),
                        "req:" + _work(scope),
                        seconds=elapsed,
                        latency_ms=elapsed * 1000,
                    )
            except Exception:  # counting must never fail a request
                logger.warning("usage: could not count a request", exc_info=True)
            holder.close()


def _template(scope: Scope) -> str:
    """The stream's route template (a stream always has one: something answered it)."""
    return _work(scope).split(" ", 1)[1]
