"""`X-App-Version` on every response — the version-skew handshake's server half.

A cached OLD web bundle talking to a NEW api broke every chat after the
v2026.07.23 deploy (the #601 event-shape change). The SPA bakes its own build
version and compares it against this header at its one fetch chokepoint; a
mismatch means "this tab runs a stale bundle" and triggers a reload at a safe
moment. The header rides EVERY response — errors included — because the FE may
learn of the skew from whichever call it happens to make first.

A pure ASGI middleware on purpose: FastAPI's ``@app.middleware("http")`` wraps
handlers in Starlette's ``BaseHTTPMiddleware``, which re-streams response
bodies — a needless risk for an app whose turns live on SSE. Injecting the
header at ``http.response.start`` leaves bodies (and streaming) untouched.
"""

from __future__ import annotations

from typing import Any

from starlette.datastructures import MutableHeaders

Scope = dict[str, Any]


class VersionHeaderMiddleware:
    def __init__(self, app: Any, version: str) -> None:
        self.app = app
        self.version = version

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not self.version:
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-App-Version"] = self.version
            await send(message)

        await self.app(scope, receive, send_with_header)
