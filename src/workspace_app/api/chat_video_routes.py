"""``POST /a/{slug}/items/{item_id}/chat-video`` — queue a video of a chat
(plan-chat-video-export, P6).

The body is a COMPLETE transcript in the ``.chat.json`` shape (the export
route's output, sliced by the FE — or three messages a person typed into a
file: this route is how a script makes a video of fixed lines without an
LLM), the render options, and optionally where the video goes. The route
validates, writes the transcript and a ``queued`` progress file beside the
output through the item's ``WorkspaceFiles``, queues the job, and answers
202 with the three paths. From there the FE watches the progress file
through the ordinary file route and deletes it to cancel; nothing here is
polled.

Item-level, not chat-level: the transcript is the input, so the chat it
came from — if any — is not consulted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

from ..chat_video import progress as prog
from ..chat_video.jobs import ChatVideoCoordinator, InFlight
from ..chat_video.options import FORMATS, VideoOptions, check_limits
from ..chat_video.timeline import build_timeline
from ..config.schema import ChatVideoSettings
from ..files import WorkspaceFiles, rel_path
from ..filestore.protocol import FileNotFound
from ..kb.chat_export import parse_chat_export, safe_stem
from .events import FileChanged
from .file_routes import _workspace_path
from .locator import ItemLocator
from .turns import ChatTurnEngine

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "/exports/chat-video"
# The default file name keeps this much of the title's stem: a 300-character
# title made a 900-byte name the store refused (a 500 for a renamed chat).
DEFAULT_STEM_CHARS = 64


class ChatVideoRequest(BaseModel):
    transcript: dict[str, Any]
    """The ``.chat.json`` document: ``{"title", "messages": [{"role",
    "content", …}]}``, validated by the same ``parse_chat_export`` an upload
    goes through."""
    options: dict[str, Any] = {}
    """``VideoOptions`` fields, any subset; the struct's own checks apply.
    ``fmt`` is overridden by ``output_path``'s extension when one is given."""
    output_path: str | None = None
    """Where the video goes, a workspace path ending in ``.gif`` / ``.mp4`` /
    ``.webm`` (the extension picks the format, as the CLI's ``-o`` does).
    Default: ``/exports/chat-video/<title>-<timestamp>.<fmt>``."""


class ChatVideoQueued(BaseModel):
    output_path: str
    source_path: str
    progress_path: str
    expected_seconds: int
    """How long the video will play — the timeline's own figure."""
    stale_after_seconds: int
    """After this long without a heartbeat in the progress file the worker
    is gone (`chat_video.stale_after_seconds`) — the watcher's rule is the
    server's, so it says "no worker" when the server would."""
    token: str
    """The mark on this job's progress file. A file at the path without it
    is another job's (a cancel, then a new request for the same output),
    and to this job's watcher that is the same as no file — the worker's
    own rule (`ChatVideoCoordinator._run`'s `mine()`)."""


class ChatVideoLimitsOut(BaseModel):
    max_pixels: int
    max_seconds: int
    max_output_bytes: int


def register_chat_video_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    files: WorkspaceFiles,
    coordinator: ChatVideoCoordinator,
    limits: ChatVideoSettings,
    get_user_id: Callable[[], str],
    turn_engine: ChatTurnEngine,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    @app.get("/a/{slug}/items/{item_id}/chat-video")
    async def chat_video_limits(slug: str, item_id: str) -> ChatVideoLimitsOut:
        """This deployment's ceilings (`chat_video:`), for the form: it hides
        the sizes and lengths a POST would refuse rather than offering them
        and showing the 422. `read_meta` — whoever may see the item may
        know what it allows; the numbers are the deployment's, not the item's."""
        locator.require_access(slug, item_id, "read_meta")
        return ChatVideoLimitsOut(
            max_pixels=limits.max_pixels,
            max_seconds=limits.max_seconds,
            max_output_bytes=limits.max_output_bytes,
        )

    @app.delete("/a/{slug}/items/{item_id}/chat-video", status_code=204)
    async def cancel_chat_video(
        slug: str,
        item_id: str,
        path: str = Query(..., description="The job's `<output>.progress.json`."),
    ) -> Response:
        """Cancel (or dismiss) a video by deleting its progress file — the
        cancel handle (decision 12) — for the person who asked for it, or
        for anyone who may edit the item's content. The file route's DELETE
        asks `edit_content`, which `add_content` — the verb that let the
        person START the video — does not include; without this route the
        requester's own Cancel was a swallowed 403 and the render went on.
        The path must be a progress file of ours (422 otherwise), present
        (404 once it is gone), and the caller must be its requester or an
        editor (403)."""
        investigation_id = locator.require_access(slug, item_id, "read_meta")
        progress_path = _workspace_path(path)
        if not progress_path.endswith(prog.PROGRESS_SUFFIX):
            raise HTTPException(status_code=422, detail="path must be a chat-video progress file")
        try:
            current = prog.Progress.loads(await files.read(investigation_id, progress_path))
        except FileNotFound as exc:
            raise HTTPException(status_code=404, detail="no such progress file") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="not a chat-video progress file") from exc
        user = get_user_id()
        if current.requested_by != user:
            locator.require_access(slug, item_id, "edit_content")
        await files.delete(investigation_id, progress_path)
        turn_engine.publish(
            investigation_id, FileChanged(path=progress_path, by=user, kind="deleted")
        )
        return Response(status_code=204)

    @app.post("/a/{slug}/items/{item_id}/chat-video", status_code=202)
    async def queue_chat_video(slug: str, item_id: str, body: ChatVideoRequest) -> ChatVideoQueued:
        """Queue a video of ``body.transcript``. Both verbs: the job READS the
        item's files (the pictures the transcript shows) and ADDS to them (the
        video, the transcript, the progress file); the worker asks the same
        two again when its turn comes.

        Every refusal is one sentence: 422 for a transcript or an option the
        code would refuse anywhere (the struct's own words, ``check_limits``'
        ceiling), 400 for a path that climbs out, 409 for a path that is
        taken or a video already being made."""
        investigation_id = locator.require_access(slug, item_id, "read_content")
        locator.require_access(slug, item_id, "add_content")
        try:
            title, messages = parse_chat_export(msgspec.json.encode(body.transcript))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            options = msgspec.convert(body.options, VideoOptions)
        except msgspec.ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if body.output_path is not None:
            output_path = _workspace_path(body.output_path)
            stem, dot, ext = output_path.rsplit("/", 1)[-1].rpartition(".")
            fmt = ext.lower() if dot and stem else ""
            if fmt not in FORMATS:
                raise HTTPException(
                    status_code=422,
                    detail="output_path must end in "
                    + ", ".join(f".{f}" for f in FORMATS[:-1])
                    + f" or .{FORMATS[-1]}",
                )
            options = msgspec.structs.replace(options, fmt=(fmt,))
        else:
            stamp = now().strftime("%Y%m%d-%H%M%S")
            stem = safe_stem(title)[:DEFAULT_STEM_CHARS].rstrip("-") or "chat"
            output_path = f"{DEFAULT_OUTPUT_DIR}/{stem}-{stamp}.{options.fmt[0]}"
        try:
            check_limits(
                options,
                max_pixels=limits.max_pixels,
                max_seconds=limits.max_seconds,
                max_output_bytes=limits.max_output_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if await files.exists(investigation_id, output_path):
            raise HTTPException(status_code=409, detail=f"file exists at {rel_path(output_path)}")
        timeline = build_timeline(title=title, messages=messages, options=options)
        expected_seconds = round(timeline.playback_ms / 1000)
        user = get_user_id()
        try:
            queued = await coordinator.enqueue(
                item_id=investigation_id,
                title=title,
                messages=messages,
                options=options,
                output_path=output_path,
                expected_seconds=expected_seconds,
                user=user,
            )
        except InFlight as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # Two files just appeared in the tree; the viewers refetch on this,
        # as they do for a write through the file routes.
        for path in (queued.source_path, queued.progress_path):
            turn_engine.publish(investigation_id, FileChanged(path=path, by=user, kind="written"))
        logger.info(
            "chat-video: queued %s for item %s by %s (%ds)",
            output_path,
            investigation_id,
            user,
            expected_seconds,
        )
        return ChatVideoQueued(
            output_path=output_path,
            source_path=queued.source_path,
            progress_path=queued.progress_path,
            expected_seconds=expected_seconds,
            stale_after_seconds=limits.stale_after_seconds,
            token=queued.token,
        )
