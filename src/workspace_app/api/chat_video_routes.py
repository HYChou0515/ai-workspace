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
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from ..chat_video.jobs import ChatVideoCoordinator, InFlight
from ..chat_video.options import FORMATS, VideoOptions, check_limits
from ..chat_video.timeline import build_timeline
from ..config.schema import ChatVideoSettings
from ..files import WorkspaceFiles, rel_path
from ..kb.chat_export import parse_chat_export, safe_stem
from .events import FileChanged
from .file_routes import _workspace_path
from .locator import ItemLocator
from .turns import ChatTurnEngine

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "/exports/chat-video"


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
            output_path = f"{DEFAULT_OUTPUT_DIR}/{safe_stem(title)}-{stamp}.{options.fmt[0]}"
        try:
            check_limits(options, max_pixels=limits.max_pixels, max_seconds=limits.max_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if await files.exists(investigation_id, output_path):
            raise HTTPException(status_code=409, detail=f"file exists at {rel_path(output_path)}")
        timeline = build_timeline(title=title, messages=messages, options=options)
        expected_seconds = round(timeline.playback_ms / 1000)
        user = get_user_id()
        try:
            source_path, progress_path = await coordinator.enqueue(
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
        for path in (source_path, progress_path):
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
            source_path=source_path,
            progress_path=progress_path,
            expected_seconds=expected_seconds,
        )
