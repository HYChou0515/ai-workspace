"""#177 test clients — auto-prefix ``/api`` onto backend paths.

After the backend moved entirely under ``/api`` (#177), the hundreds of existing
test call sites still use bare paths like ``client.get("/kb/chats")``. Rather than
rewrite every one, these drop-in clients rewrite a request path to ``/api`` + path
**only when** that prefixed path actually matches a backend route on the app under
test. Consequences, all desirable:

* a real API path (``/kb/chats`` → ``/api/kb/chats``) is rewritten → JSON, as before;
* an SPA client route (``/a/rca/items/x``) matches no API route → left bare → the
  history fallback serves index.html, exactly what a browser refresh does;
* a hand-built bare app (no ``/api`` routes at all) is never rewritten.

``TestClient`` is the sync (starlette) client; ``AsyncClient`` is the httpx async
client used by the streaming tests — both swap in by import alone. Tests that must
observe the *raw* browser-refresh behaviour (``test_spa`` and the ``#177``
namespace regression) import the real starlette client instead.
"""

from __future__ import annotations

import httpx
from starlette.routing import Match
from starlette.testclient import TestClient as _RawTestClient


def _api_routes(app):
    return [r for r in getattr(app, "routes", []) if getattr(r, "path", "").startswith("/api")]


def _api_path(api_routes, method: str, url):
    """``/api`` + url when that matches a real API route on this app, else url."""
    if not isinstance(url, str) or not url.startswith("/") or url.startswith("/api"):
        return url
    scope = {
        "type": "http",
        "method": method.upper(),
        "path": "/api" + url.split("?", 1)[0],
        "headers": [],
        "query_string": b"",
        "root_path": "",
    }
    for route in api_routes:
        match, _ = route.matches(scope)
        if match != Match.NONE:
            return "/api" + url
    return url


class TestClient(_RawTestClient):
    def __init__(self, app, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        # snapshot the API routes once; tests don't mutate routing post-construction
        self._api_routes = _api_routes(app)

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        url = _api_path(self._api_routes, method, url)
        return super().request(method, url, *args, **kwargs)

    def stream(self, method, url, *args, **kwargs):  # type: ignore[override]
        url = _api_path(self._api_routes, method, url)
        return super().stream(method, url, *args, **kwargs)


class AsyncClient(httpx.AsyncClient):
    """Drop-in for ``httpx.AsyncClient(transport=ASGITransport(app=app), …)`` — the
    app is read off the transport so paths get the same route-aware /api prefix.
    ``build_request`` is the single chokepoint for get/post/stream/request.

    Pass ``routes_from=<app>`` when the transport's app is opaque for route
    discovery (e.g. wrapped by ``LifespanManager``, which exposes only a bare
    callable) — give it the original FastAPI app to read routes from."""

    def __init__(self, *args, routes_from=None, **kwargs):
        super().__init__(*args, **kwargs)
        app = routes_from if routes_from is not None else getattr(self._transport, "app", None)
        self._api_routes = _api_routes(app) if app is not None else []

    def build_request(self, method, url, *args, **kwargs):
        url = _api_path(self._api_routes, method, url)
        return super().build_request(method, url, *args, **kwargs)
