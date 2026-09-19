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

Stage = Literal["queued", "rendering", "encoding", "failed"]
RUNNING: frozenset[str] = frozenset({"queued", "rendering", "encoding"})

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
    token: str = ""
    """Which job this file belongs to (the job's own, minted at enqueue). A
    worker that finds a file with another token at its path — a cancel
    followed by a new request for the same output, inside one heartbeat —
    treats it exactly as no file: stops, writes nothing, leaves it alone.
    Without it a job identified its file by path only, rendered the OLD
    transcript into the new request's output and deleted the new job's
    file."""

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


def is_alive(
    progress: Progress, *, now: datetime, stale_after_seconds: int, queued_alive: bool
) -> bool:
    """Whether a job still holds this file. A ``failed`` file holds no claim,
    whatever its age. A ``queued`` file has no heartbeat to judge — nobody
    rewrites it while the job waits in line behind another render, which is
    the NORMAL case with one worker and renders of a minute or more — so its
    claim is ``queued_alive``: whether the queue still holds the job (the
    coordinator asks its rows). Judging it by age called every backlog
    "stale", let a second request replace the file, and queued two jobs for
    one output. A running stage IS judged by its heartbeat: within
    ``stale_after_seconds`` of ``now``, or the worker died mid-render."""
    if progress.stage == "queued":
        return queued_alive
    if progress.stage not in RUNNING:
        return False
    return now - progress.heartbeat_at <= timedelta(seconds=stale_after_seconds)


def paths_for(output_path: str) -> tuple[str, str]:
    """``(source_path, progress_path)`` for an output: the transcript the
    video was made from and the progress file, both beside it."""
    return output_path + SOURCE_SUFFIX, output_path + PROGRESS_SUFFIX
