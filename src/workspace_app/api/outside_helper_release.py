"""The published outside helper (docs/plan-outside-helper.md §3.7.2).

The operator packs it on the dev server (`scripts/pack_outside_helper.py`) and
drops `outside-helper.json` + the zip it names into `server.outside_helper_dir`.
Both are read on every request — publishing is copying two files, no redeploy.
Only the file the manifest names is ever served, and only when that name is a
bare file name: the folder is the operator's, not a file server.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

MANIFEST = "outside-helper.json"

#: A release file is a plain name in the folder: no separators, no `..`.
_PLAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ReleaseOut(BaseModel):
    version: str
    min_version: str
    file: str
    sha256: str
    size: int


def read_release(folder: str | None) -> tuple[ReleaseOut, Path] | None:
    """The release the folder publishes, and its file — or None when there is
    none (unset, empty, unreadable, or naming something it may not)."""
    if not folder:
        return None
    root = Path(folder)
    try:
        data: Any = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        name = data["file"]
        if not isinstance(name, str) or not _PLAIN_NAME.match(name) or ".." in name:
            return None
        path = root / name
        release = ReleaseOut(
            version=str(data["version"]),
            min_version=str(data.get("min_version") or "0.0.0"),
            file=name,
            sha256=str(data["sha256"]),
            size=path.stat().st_size,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return release, path


def register_outside_helper_release_routes(app: FastAPI | APIRouter, *, folder: str | None) -> None:
    @app.get("/outside-helper/manifest", response_model=ReleaseOut)
    async def manifest() -> ReleaseOut:
        found = read_release(folder)
        if found is None:
            raise HTTPException(status_code=404, detail="the outside helper is not published")
        return found[0]

    @app.get("/outside-helper/download/{file}")
    async def download(file: str) -> FileResponse:
        found = read_release(folder)
        if found is None or found[0].file != file:
            raise HTTPException(status_code=404, detail="no such release file")
        return FileResponse(found[1], media_type="application/zip", filename=file)


__all__ = ["MANIFEST", "ReleaseOut", "read_release", "register_outside_helper_release_routes"]
