"""The progress file — one JSON document beside the video, and nothing else.

There is no run model and no status route (plan-chat-video-export, decisions
11–12): the API writes ``<output>.progress.json`` when it queues a job, the
worker rewrites it every heartbeat, the FE polls it through the ordinary
file route, and **deleting it is the cancel** — the worker notices at its
next heartbeat, closes the browser or kills ffmpeg, and writes nothing. A
heartbeat older than ``chat_video.stale_after_seconds`` means the worker
died; the file may then be replaced by a new request. On success the file
is deleted (the tree keeps the video and the transcript it was made from);
on failure it stays, with the sentence, for a person to read and delete.

This module is the pure part: the document, its JSON, the liveness rule and
the three file names. Reading and writing them through ``WorkspaceFiles``
is the coordinator's business.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import msgspec

Stage = Literal["queued", "rendering", "encoding", "writing", "failed"]
RUNNING: frozenset[str] = frozenset({"queued", "rendering", "encoding", "writing"})

SOURCE_SUFFIX = ".chat.json"
PROGRESS_SUFFIX = ".progress.json"


class Progress(msgspec.Struct, frozen=True):
    stage: Stage
    expected_seconds: int
    started_at: datetime
    heartbeat_at: datetime
    output_path: str
    requested_by: str
    elapsed_seconds: int = 0
    error: str = ""

    def dumps(self) -> bytes:
        """Indented, keys in declaration order — a person opens this file
        from the tree."""
        return msgspec.json.format(msgspec.json.encode(self), indent=2)

    @classmethod
    def loads(cls, raw: bytes) -> Progress:
        """``ValueError`` for anything that is not one of ours (not JSON, the
        wrong shape) — the caller treats that as absent."""
        try:
            return msgspec.json.decode(raw, type=cls)
        except msgspec.DecodeError as exc:
            raise ValueError(str(exc)) from exc


def is_alive(progress: Progress, *, now: datetime, stale_after_seconds: int) -> bool:
    """Whether a worker still holds this file: the stage is a running one
    AND the last heartbeat is within ``stale_after_seconds``. A ``failed``
    file holds no claim, whatever its age."""
    if progress.stage not in RUNNING:
        return False
    return now - progress.heartbeat_at <= timedelta(seconds=stale_after_seconds)


def paths_for(output_path: str) -> tuple[str, str]:
    """``(source_path, progress_path)`` for an output: the transcript the
    video was made from and the progress file, both beside it."""
    return output_path + SOURCE_SUFFIX, output_path + PROGRESS_SUFFIX
