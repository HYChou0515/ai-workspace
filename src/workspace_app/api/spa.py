"""SPA static-file serving with an HTML5 history fallback.

Extracted from ``api/app.py`` (#54: split the monolithic ``create_app`` module
into focused units). ``SpaStaticFiles`` is mounted at ``/`` after every API
route so that client-side routes resolve to ``index.html`` on refresh.
"""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles


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
        return response
