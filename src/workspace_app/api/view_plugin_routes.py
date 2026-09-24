"""Serving runtime view plugins to the SPA (#847/#848 PR1 P3).

``GET /view-plugins`` lists what the operator installed; the SPA ``import()``s
each ``entry_url`` (relative to the API root — only the SPA knows the deploy's
sub-path) before its first render. ``GET /view-plugins/{name}/{path}`` serves
the plugin's ``web/`` folder read-only, and nothing outside it: the manifest,
the sandbox bundle and the skill are not the browser's business.

Both need a signed-in user. A plugin is operator-installed code that runs in the
SPA origin with the user's rights, so it is not secret — but there is no reason
to hand an anonymous caller the list of what is installed.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Callable, Sequence
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..view_plugins import ViewPlugin


class ViewPluginOut(BaseModel):
    name: str
    #: The SDK major the plugin was built against; the SPA refuses a mismatch.
    sdk: str
    kinds: list[str]
    #: Relative to the API root (the SPA prepends its `API_PREFIX`).
    entry_url: str


def _web_file(plugin: ViewPlugin, rel: str) -> Path | None:
    """``rel`` under the plugin's ``web/``, or ``None`` if it names anything
    else — a missing file, a dir, or a path (``..``, absolute, a symlink) that
    resolves outside ``web/``."""
    web = plugin.web_dir.resolve()
    target = (web / rel).resolve()
    if web not in target.parents or not target.is_file():
        return None
    return target


def register_view_plugin_routes(
    app: FastAPI | APIRouter,
    *,
    get_plugins: Callable[[], Sequence[ViewPlugin]],
    get_user_id: Callable[[], str],
) -> None:
    @app.get("/view-plugins")
    async def list_view_plugins() -> list[ViewPluginOut]:
        get_user_id()
        return [
            ViewPluginOut(
                name=p.name,
                sdk=p.manifest.sdk,
                kinds=list(p.manifest.kinds),
                entry_url=f"/view-plugins/{p.name}/index.js",
            )
            for p in get_plugins()
        ]

    @app.get("/view-plugins/{name}/{path:path}")
    async def view_plugin_file(name: str, path: str) -> FileResponse:
        get_user_id()
        plugin = next((p for p in get_plugins() if p.name == name), None)
        target = None if plugin is None else _web_file(plugin, path)
        if target is None:
            raise HTTPException(status_code=404, detail="no such view plugin file")
        media = "text/javascript" if target.suffix in {".js", ".mjs"} else None
        return FileResponse(
            target,
            media_type=media or mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            # Fixed names: an operator's reinstall must reach the next page load.
            headers={"Cache-Control": "no-cache"},
        )
