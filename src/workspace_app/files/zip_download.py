"""Issue #247: generic two-step ZIP download infra, shared by the KB folder
export and the workspace folder export (and, after the #101 refactor, the
collection export).

Two-step because building the archive (restore/read every file + compress) is
blocking: ``prepare_zip`` writes it to a temp file under ``downloads_dir()`` off
the event loop and returns a download id; ``stream_prepared_zip`` serves it once
and deletes it. ``sweep_stale_downloads`` reaps temp files a caller never
streamed (an abandoned prepare).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import tempfile
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask


class DownloadPrepared(BaseModel):
    """Result of a two-step download `.../prepare` call — the handle the FE
    anchor-navigates to (`GET .../{download_id}`) to stream the zip. `size` lets
    the FE show the size before/while downloading (#101, generalised in #247)."""

    download_id: str
    filename: str
    size: int


# Abandoned prepares (no stream call) are reaped after this long.
DOWNLOAD_TTL_SECONDS = 3600
# A download id is a uuid4 hex — validated so it can't escape ``downloads_dir()``.
_ID_RE = re.compile(r"[0-9a-f]{32}")


def downloads_dir() -> Path:
    """The temp directory holding prepared (but not-yet-streamed) ZIPs."""
    d = Path(tempfile.gettempdir()) / "workspace_kb_downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sweep_stale_downloads(ttl_seconds: int = DOWNLOAD_TTL_SECONDS) -> None:
    """Delete prepared ZIPs older than ``ttl_seconds`` (callers who never
    streamed their download). Best-effort: races/permission errors are ignored."""
    now = time.time()
    for f in downloads_dir().glob("*.zip"):
        try:
            if now - f.stat().st_mtime > ttl_seconds:
                f.unlink()
        except OSError:  # pragma: no cover - defensive against races
            pass


def safe_zip_filename(name: str, fallback: str = "download") -> str:
    """A filesystem-safe ``{name}.zip`` for the Content-Disposition header."""
    safe = re.sub(r"[^\w.\- ]+", "_", name).strip()
    return f"{safe or fallback}.zip"


def subtree_arcname(path: str, prefix: str) -> str | None:
    """The archive name for ``path`` when downloading the folder ``prefix`` —
    i.e. ``path`` re-rooted at ``prefix`` — or ``None`` when ``path`` is not
    inside ``prefix``.

    Both are matched on their slash-stripped form so ``/img`` and ``img/``
    select the same subtree. ``prefix=""`` is the whole tree (every path maps to
    itself). A path that exactly equals the prefix re-roots to its basename. The
    boundary is a real path segment, so prefix ``img`` does NOT capture a sibling
    ``imgs/...`` (a bare ``startswith`` would).
    """
    p = path.strip("/")
    pfx = prefix.strip("/")
    if not pfx:
        return p or None
    if p == pfx:
        return p.rsplit("/", 1)[-1]
    if p.startswith(pfx + "/"):
        return p[len(pfx) + 1 :]
    return None


def write_zip_members(out_path: Path, members: Iterable[tuple[str, bytes]]) -> None:
    """Write ``(arcname, data)`` members into a deflated ZIP at ``out_path``."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in members:
            zf.writestr(arcname, data)


async def prepare_zip(build: Callable[[Path], None]) -> tuple[str, int]:
    """Mint a download id, build the ZIP off the event loop via ``build(path)``,
    and return ``(download_id, size_bytes)``. Stale prepares are swept first."""
    sweep_stale_downloads()
    download_id = uuid.uuid4().hex
    out_path = downloads_dir() / f"{download_id}.zip"
    await asyncio.to_thread(build, out_path)
    return download_id, out_path.stat().st_size


def prepared_path(download_id: str) -> Path | None:
    """The on-disk path for a prepared download, or ``None`` when the id is
    malformed or the file is gone (already streamed / reaped / never made)."""
    if not _ID_RE.fullmatch(download_id):
        return None
    path = downloads_dir() / f"{download_id}.zip"
    return path if path.exists() else None


def _unlink_quietly(path: Path) -> None:
    """Delete a streamed ZIP after the response is sent. Best-effort — a missing
    file (double-send race) is fine."""
    with contextlib.suppress(OSError):
        path.unlink()


def stream_prepared_zip(path: Path, filename: str) -> FileResponse:
    """Serve a prepared ZIP once, deleting it after the response is sent."""
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(_unlink_quietly, path),
    )
