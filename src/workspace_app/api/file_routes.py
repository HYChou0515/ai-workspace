"""Workspace file / notebook / shell routes (#54).

The IDE's file tree + editor, the VSCode-style search/replace, raw folder download,
notebook cell execution, and the Terminal pane's direct ``exec`` — every route under
``/a/{slug}/items/{item_id}`` that reads or mutates a workspace's files or drives its
sandbox. They go through the ``WorkspaceFiles`` facade (warm→sandbox / cold→snapshot)
and the kernel + sandbox services, so they lift out of ``create_app`` as one group.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from ..files import WorkspaceFiles
from ..files.zip_download import (
    DownloadPrepared,
    prepare_zip,
    prepared_path,
    safe_zip_filename,
    stream_prepared_zip,
    subtree_arcname,
    write_zip_members,
)
from ..filestore.protocol import FileExists, FileNotFound
from ..kernels import KernelService
from ..sandbox.protocol import Sandbox
from .activity import ActivityLog
from .events import CellEvent, FileChanged, to_sse
from .locator import ItemLocator
from .registry import InvestigationRegistry
from .schemas import (
    _CellExecuteBody,
    _ExecBody,
    _FileEntry,
    _ItemSkills,
    _ItemSkillState,
    _MkdirBody,
    _MoveBody,
    _ReplaceBody,
    _SearchBody,
    _WorkspaceUsage,
)
from .search import InvalidQuery, compile_query, path_selected, search_text
from .turns import ChatTurnEngine

_READONLY_DIR = ".readonly"

logger = logging.getLogger(__name__)


def _skill_pref_state(value: bool | None) -> Literal["follow", "on", "off"]:
    """#380: a raw ``attached_skill_prefs`` value → the picker's tri-state label
    (absent key ⇒ ``follow``), mirroring the tool picker's ``_pref_state``."""
    if value is None:
        return "follow"
    return "on" if value else "off"


def _is_readonly_path(path: str) -> bool:
    """#205: files under the reserved ``.readonly/`` directory are server-enforced
    read-only — the IDE renders them non-editable and a PUT is refused. A computed
    convention (like the ``/.workflow/`` journal folder, #136), so no per-file
    metadata or migration is needed; any segment named ``.readonly`` qualifies."""
    return _READONLY_DIR in path.strip("/").split("/")


async def _stream_upload_to_store(
    workspace_id: str,
    path: str,
    request: Request,
    files: WorkspaceFiles,
    max_file_size: int,
    workspace_quota: int = 0,
) -> None:
    """Stream the request body to a staging file on disk, enforcing the
    single-file cap (#219) and the per-workspace quota (#245) as bytes arrive,
    then hand the file to the store. The staging file means a multi-GB upload
    never sits whole in RAM; both caps are checked mid-stream so an over-limit
    upload is rejected without buffering it all. ``max_file_size`` of 0 disables
    the single-file cap; ``workspace_quota`` of 0 disables the total quota.

    The quota credits back the bytes of the file being overwritten (a replace,
    not an add), so re-uploading a same-size file never trips it."""
    # Headroom for this path, fetched once up front; an overwrite is a replace.
    # None ⇒ quota disabled. Computed against the durable store, so the sandbox
    # mirror (which writes the store directly, not via this endpoint) isn't gated.
    remaining = await files.remaining_quota(workspace_id, path, workspace_quota)
    fd, name = tempfile.mkstemp(prefix="ws-upload-")
    tmp = Path(name)
    try:
        size = 0
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if max_file_size and size > max_file_size:
                    logger.warning(
                        "file_routes: upload to %s exceeds size limit (%d > %d)",
                        path,
                        size,
                        max_file_size,
                    )
                    raise HTTPException(status_code=413, detail="file exceeds the size limit")
                if remaining is not None and size > remaining:
                    used = await files.workspace_usage(workspace_id)
                    logger.warning(
                        "file_routes: upload to %s exceeds quota (used=%s quota=%s attempted=%d)",
                        path,
                        used,
                        workspace_quota,
                        size,
                    )
                    raise HTTPException(
                        status_code=507,
                        detail={
                            "error": "workspace_quota_exceeded",
                            "used": used,
                            "quota": workspace_quota,
                            "attempted": size,
                        },
                    )
                f.write(chunk)
        await files.write_from_path(workspace_id, path, tmp, request.headers.get("content-type"))
    finally:
        tmp.unlink(missing_ok=True)


def register_file_routes(
    app: FastAPI | APIRouter,
    *,
    files: WorkspaceFiles,
    registry: InvestigationRegistry,
    kernels: KernelService,
    sandbox: Sandbox,
    locator: ItemLocator,
    get_user_id: Callable[[], str],
    turn_engine: ChatTurnEngine,
    activity: ActivityLog,
    workspace_quota: int,
    max_file_size: int,
) -> None:
    """Mount the workspace file / notebook / shell routes onto ``app``."""

    @app.get("/a/{slug}/items/{item_id}/skills")
    async def list_item_skills(slug: str, item_id: str) -> _ItemSkills:
        """#380: the skills picker state for this item — the App's declared shared
        skills + the profile's package skills + the workspace's co-created ones
        (`.skill/`), each with its source / default_on / tri-state pref / resolved
        effective. Backs the Skills panel (list + toggle + apply). The effective
        state comes from the SAME `effective_item_skills` resolve the turn's prompt
        index uses, so the picker can't drift from what the agent sees."""
        from ..apps.skills import effective_item_skills, workspace_skill_metas

        investigation_id = locator.require_item(slug, item_id)
        profile = locator.profile_of(investigation_id)
        prefs = locator.skill_prefs_of(investigation_id)
        ws_metas = await workspace_skill_metas(files, investigation_id)
        states = effective_item_skills(slug, profile, prefs, ws_metas)
        return _ItemSkills(
            skills=[
                _ItemSkillState(
                    name=s.name,
                    description=s.description,
                    source=s.source,
                    default_on=s.default_on,
                    pref=_skill_pref_state(prefs.get(s.name)),
                    effective=s.effective,
                )
                for s in states
            ]
        )

    @app.get("/a/{slug}/items/{item_id}/files")
    async def list_files(slug: str, item_id: str, prefix: str = "") -> list[_FileEntry]:
        # #362: size comes from cheap metadata (a warm `walk` stat, or the cold
        # snapshot record's inline size) — NEVER by reading each file's bytes, so
        # a 600-file tree costs one listing, not 600 full-content downloads.
        investigation_id = locator.require_item(slug, item_id)
        entries = await files.stat_all(investigation_id, prefix)
        return [
            _FileEntry(path=p, size=size, read_only=_is_readonly_path(p))
            for p, size in sorted(entries)
        ]

    @app.get("/a/{slug}/items/{item_id}/files/usage")
    async def workspace_files_usage(slug: str, item_id: str) -> _WorkspaceUsage:
        """#245: the workspace's durable byte total vs its quota — backs the
        upload usage bar. Registered before the ``/files/{path:path}`` read route
        so the literal ``usage`` segment isn't swallowed as a file path."""
        investigation_id = locator.require_item(slug, item_id)
        return _WorkspaceUsage(
            used=await files.workspace_usage(investigation_id),
            quota=workspace_quota,
        )

    @app.get("/a/{slug}/items/{item_id}/dirs")
    async def list_dirs(slug: str, item_id: str) -> list[str]:
        """Directory paths (incl. empty ones) for the file tree."""
        investigation_id = locator.require_item(slug, item_id)
        return sorted(await files.listdir(investigation_id))

    @app.post("/a/{slug}/items/{item_id}/files/refresh")
    async def refresh_files(slug: str, item_id: str) -> dict:
        """Force-mirror the live sandbox to the snapshot now (don't wait for the
        ≤window throttle sweep) — the explicit 'refresh' action. No-op cold."""
        investigation_id = locator.require_item(slug, item_id)
        logger.info("file_routes: manual mirror flush of item %s", investigation_id)
        await registry.flush(investigation_id)
        return {"ok": True}

    # Issue #247: raw folder/root download (no manifest). Registered before the
    # `{path:path}` routes so `files/download/...` isn't swallowed as a file path.
    async def _collect_download_members(
        investigation_id: str, prefix: str
    ) -> list[tuple[str, bytes]]:
        """Every workspace file under `prefix`, re-rooted at it, as
        `(arcname, bytes)`. Reserved `.readonly/` agent snapshots are skipped —
        they're internal diff state, not user content."""
        members: list[tuple[str, bytes]] = []
        for p in sorted(await files.ls(investigation_id, "")):
            if _is_readonly_path(p):
                continue
            arcname = subtree_arcname(p, prefix)
            if arcname is None:
                continue
            members.append((arcname, await files.read(investigation_id, p)))
        return members

    def _workspace_zip_name(investigation_id: str, prefix: str) -> str:
        folder = prefix.strip("/").rsplit("/", 1)[-1]
        # subfolder → its name; root → the item title; untitled → "workspace".
        name = folder or locator.title_of(investigation_id) or ""
        return safe_zip_filename(name, fallback="workspace")

    @app.post("/a/{slug}/items/{item_id}/files/download/prepare")
    async def prepare_files_download(slug: str, item_id: str, prefix: str = "") -> DownloadPrepared:
        """Build a plain ZIP of the raw bytes of every file under `prefix`
        (`prefix=""` = the whole workspace), entries re-rooted at the folder.
        Reading routes warm→sandbox / cold→snapshot via the facade; only the
        compression runs off the event loop."""
        investigation_id = locator.require_item(slug, item_id)
        members = await _collect_download_members(investigation_id, prefix)
        download_id, size = await prepare_zip(lambda out: write_zip_members(out, members))
        logger.info(
            "file_routes: prepared download %s of %r for item %s (%d bytes)",
            download_id,
            prefix,
            investigation_id,
            size,
        )
        return DownloadPrepared(
            download_id=download_id,
            filename=_workspace_zip_name(investigation_id, prefix),
            size=size,
        )

    @app.get("/a/{slug}/items/{item_id}/files/download/{download_id}")
    async def stream_files_download(
        slug: str, item_id: str, download_id: str, prefix: str = ""
    ) -> FileResponse:
        """Stream a prepared workspace ZIP once, then delete it. 404 when the id
        is malformed / already streamed / reaped."""
        investigation_id = locator.require_item(slug, item_id)
        path = prepared_path(download_id)
        if path is None:
            raise HTTPException(status_code=404, detail="download not found")
        return stream_prepared_zip(path, _workspace_zip_name(investigation_id, prefix))

    @app.put(
        "/a/{slug}/items/{item_id}/files/{path:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def write_file(slug: str, item_id: str, path: str, request: Request) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        norm = "/" + path.lstrip("/")
        if _is_readonly_path(norm):
            # #205: the `.readonly/` snapshot the human diffs against is not hand-editable.
            raise HTTPException(status_code=403, detail="this file is read-only")
        # #219: stream the body to a staging file (never the whole upload in RAM),
        # enforcing the single-file cap as bytes arrive, then stream it into the
        # store. `files.write_from_path` routes warm→sandbox / cold→blob.
        # #245: also gate the per-workspace total quota mid-stream.
        await _stream_upload_to_store(
            investigation_id, norm, request, files, max_file_size, workspace_quota
        )
        activity.record(
            "file_written",
            f"Wrote {norm}",
            {"investigation_id": investigation_id, "path": norm},
        )
        logger.info("file_routes: wrote %s to item %s", norm, investigation_id)
        # #43: tell other viewers of this shared workspace the file changed so
        # they refetch (last-write-wins; this is the "someone else edited" cue).
        turn_engine.publish(
            investigation_id, FileChanged(path=norm, by=get_user_id(), kind="written")
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # POST /files/mkdir and /move and /copy are registered before the
    # {path:path} routes so their literal segments can't be swallowed as a
    # path (distinct methods anyway, but keeping them first documents intent).
    @app.post(
        "/a/{slug}/items/{item_id}/files/mkdir",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def make_dir(slug: str, item_id: str, body: _MkdirBody) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        norm = "/" + body.path.strip("/")
        try:
            await files.mkdir(investigation_id, norm)
        except FileExists as exc:
            raise HTTPException(status_code=409, detail=f"file exists at {norm}") from exc
        activity.record(
            "dir_created",
            f"Created folder {norm}",
            {"investigation_id": investigation_id, "path": norm},
        )
        logger.info("file_routes: created folder %s in item %s", norm, investigation_id)
        turn_engine.publish(
            investigation_id, FileChanged(path=norm, by=get_user_id(), kind="dir_created")
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    async def _transfer(investigation_id: str, src: str, dst: str, *, copy: bool) -> None:
        """Move or copy a file OR a directory subtree. Raises HTTPException
        on missing source / occupied target / moving a dir into itself."""
        if dst == src or dst.startswith(src + "/"):
            raise HTTPException(status_code=400, detail="cannot move a path into itself")
        if await files.is_dir(investigation_id, src):
            occupied = await files.exists(investigation_id, dst) or await files.is_dir(
                investigation_id, dst
            )
            if occupied:
                raise HTTPException(status_code=409, detail=f"target exists: {dst}")
            under = src + "/"
            for p in sorted(await files.ls(investigation_id, under)):
                data = await files.read(investigation_id, p)
                await files.write(investigation_id, dst + p[len(src) :], data)
            await files.mkdir(investigation_id, dst)
            for d in await files.listdir(investigation_id, under):
                await files.mkdir(investigation_id, dst + d[len(src) :])
            if not copy:
                await files.rmdir(investigation_id, src)
            return
        try:
            data = await files.read(investigation_id, src)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if await files.exists(investigation_id, dst) or await files.is_dir(investigation_id, dst):
            raise HTTPException(status_code=409, detail=f"target exists: {dst}")
        await files.write(investigation_id, dst, data)
        if not copy:
            await files.delete(investigation_id, src)

    @app.post(
        "/a/{slug}/items/{item_id}/files/move",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def move_file(slug: str, item_id: str, body: _MoveBody) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        src = "/" + body.from_.strip("/")
        dst = "/" + body.to.strip("/")
        await _transfer(investigation_id, src, dst, copy=False)
        activity.record(
            "file_moved",
            f"Moved {src} → {dst}",
            {"investigation_id": investigation_id, "path": dst},
        )
        logger.info("file_routes: moved %s to %s in item %s", src, dst, investigation_id)
        turn_engine.publish(investigation_id, FileChanged(path=dst, by=get_user_id(), kind="moved"))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/a/{slug}/items/{item_id}/files/copy",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def copy_file(slug: str, item_id: str, body: _MoveBody) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        src = "/" + body.from_.strip("/")
        dst = "/" + body.to.strip("/")
        await _transfer(investigation_id, src, dst, copy=True)
        activity.record(
            "file_copied",
            f"Copied {src} → {dst}",
            {"investigation_id": investigation_id, "path": dst},
        )
        logger.info("file_routes: copied %s to %s in item %s", src, dst, investigation_id)
        turn_engine.publish(
            investigation_id, FileChanged(path=dst, by=get_user_id(), kind="copied")
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Global text search / replace (VSCode search panel) ----

    async def _search_files(investigation_id: str, body: _SearchBody):
        try:
            pattern = compile_query(
                body.query,
                regex=body.regex,
                case_sensitive=body.caseSensitive,
                whole_word=body.wholeWord,
            )
        except InvalidQuery as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        paths = sorted(await files.ls(investigation_id))
        results: list[tuple[str, bytes, list]] = []
        for p in paths:
            if not path_selected(p, body.include, body.exclude):
                continue
            data = await files.read(investigation_id, p)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # skip binary
            matches = search_text(text, pattern)
            if matches:
                results.append((p, data, matches))
        return pattern, results

    @app.post("/a/{slug}/items/{item_id}/search")
    async def search(slug: str, item_id: str, body: _SearchBody) -> list[dict]:
        investigation_id = locator.require_item(slug, item_id)
        if not body.query:
            return []
        _pattern, results = await _search_files(investigation_id, body)
        return [
            {
                "path": p,
                "matches": [{"line": m.line, "col": m.col, "text": m.text} for m in matches],
            }
            for p, _data, matches in results
        ]

    @app.post("/a/{slug}/items/{item_id}/replace")
    async def replace(slug: str, item_id: str, body: _ReplaceBody) -> dict:
        investigation_id = locator.require_item(slug, item_id)
        if not body.query:
            return {"replaced": 0}
        pattern, results = await _search_files(investigation_id, body)
        replaced = 0
        # Every path in `results` matched per-line via search_text, so the
        # same pattern's subn over the full text always replaces ≥1 — no
        # need to guard on n.
        for p, data, _matches in results:
            text = data.decode("utf-8")
            new_text, n = pattern.subn(body.replacement, text)
            await files.write(investigation_id, p, new_text.encode("utf-8"))
            replaced += n
            activity.record(
                "file_written",
                f"Replaced {n} in {p}",
                {"investigation_id": investigation_id, "path": p},
            )
        logger.info(
            "file_routes: replaced %d occurrences across matched files in item %s",
            replaced,
            investigation_id,
        )
        return {"replaced": replaced}

    @app.delete(
        "/a/{slug}/items/{item_id}/files/{path:path}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_file(slug: str, item_id: str, path: str) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        norm = "/" + path.lstrip("/")
        if await files.is_dir(investigation_id, norm):
            await files.rmdir(investigation_id, norm)
            activity.record(
                "dir_deleted",
                f"Deleted folder {norm}",
                {"investigation_id": investigation_id, "path": norm},
            )
            logger.info("file_routes: deleted folder %s in item %s", norm, investigation_id)
            turn_engine.publish(
                investigation_id, FileChanged(path=norm, by=get_user_id(), kind="deleted")
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        try:
            await files.delete(investigation_id, norm)
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        activity.record(
            "file_deleted",
            f"Deleted {norm}",
            {"investigation_id": investigation_id, "path": norm},
        )
        logger.info("file_routes: deleted %s in item %s", norm, investigation_id)
        turn_engine.publish(
            investigation_id, FileChanged(path=norm, by=get_user_id(), kind="deleted")
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/a/{slug}/items/{item_id}/files/{path:path}")
    async def read_file(slug: str, item_id: str, path: str) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        import mimetypes

        try:
            data = await files.read(investigation_id, "/" + path.lstrip("/"))
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Issue #40: extension → MIME first so workspace markdown reports
        # rendering `![foo](./foo.png)` get `Content-Type: image/png`
        # (the browser inlines) instead of `application/octet-stream`
        # (the browser offers a download). Unknown extension → fall back
        # to the previous UTF-8 sniff so text-with-unknown-extension
        # still renders in the file viewer.
        guessed, _ = mimetypes.guess_type(path)
        if guessed:
            media_type = guessed
        else:
            try:
                data.decode("utf-8")
                media_type = "text/plain; charset=utf-8"
            except UnicodeDecodeError:
                media_type = "application/octet-stream"
        return Response(content=data, media_type=media_type)

    # ---- Notebook cell execution (plan-backend §7.3) ----

    @app.post("/a/{slug}/items/{item_id}/notebooks/{notebook_path:path}/cells/{idx}/execute")
    async def execute_cell(
        slug: str,
        item_id: str,
        notebook_path: str,
        idx: int,
        body: _CellExecuteBody,
    ) -> StreamingResponse:
        investigation_id = locator.require_item(slug, item_id)
        handle = await kernels.get_or_start(investigation_id, notebook_path)

        async def gen() -> AsyncIterator[str]:
            ev: CellEvent
            async for ev in kernels.execute_cell(handle, body.code):
                yield to_sse(ev)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.delete(
        "/a/{slug}/items/{item_id}/notebooks/{notebook_path:path}/cells/{idx}/execute",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def interrupt_cell(slug: str, item_id: str, notebook_path: str, idx: int) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        handle = kernels.peek(investigation_id, notebook_path)
        if handle is not None:
            await kernels.interrupt(handle)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/a/{slug}/items/{item_id}/notebooks/{notebook_path:path}/kernel/restart",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def restart_kernel(slug: str, item_id: str, notebook_path: str) -> Response:
        investigation_id = locator.require_item(slug, item_id)
        handle = kernels.peek(investigation_id, notebook_path)
        if handle is not None:
            await kernels.restart(handle)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ---- Direct sandbox shell — backs the FE Terminal pane ----

    @app.post("/a/{slug}/items/{item_id}/exec")
    async def exec_in_sandbox(slug: str, item_id: str, body: _ExecBody) -> dict[str, object]:
        investigation_id = locator.require_item(slug, item_id)
        if not body.cmd:
            raise HTTPException(status_code=422, detail="cmd must be non-empty")
        try:
            session = await registry.session(investigation_id)
            handle = await registry.ensure_handle(session)
            result = await sandbox.exec(handle, body.cmd)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "file_routes: sandbox exec failed for item %s (%s), returning exit -1",
                investigation_id,
                type(exc).__name__,
                exc_info=True,
            )
            # The Terminal pane has nowhere to render an HTTP error and the
            # agent's exec tool expects a structured ExecResult body — any
            # unexpected failure becomes a 200 with a non-zero exit code and
            # the error in stderr (so the consumer sees a normal command
            # failure). In-sandbox "command not found" / "permission denied"
            # are already translated to POSIX exits 127/126 inside the sandbox
            # impls, so we only land here for genuinely unexpected failures.
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"sandbox error: {type(exc).__name__}: {exc}\n",
            }
        # The sandbox is the source of truth, so the file routes already see any
        # files the command created; mirror them to the snapshot now for
        # durability. Stale handle (killed mid-call) is swallowed — re-run.
        with contextlib.suppress(Exception):
            await registry.flush(investigation_id)
        logger.info(
            "file_routes: exec in item %s completed with exit %s",
            investigation_id,
            result.exit_code,
        )
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
        }
