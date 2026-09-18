"""One entry point: a transcript in, the video's bytes out.

``render_chat_video`` is what the CLI calls, and what the job handler a
worker pod will run calls too — it is synchronous (Playwright's sync API and
an ffmpeg subprocess) and the handler wraps it in ``asyncio.to_thread``, the
same convention ``Ingestor.index`` follows. Bytes rather than a path, so the
job can store them as a ``Binary`` without a shared filesystem.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .options import Format, VideoOptions
from .player import render_player_html
from .render import encode, ensure_tools, record
from .timeline import build_timeline


def render_chat_video(
    *,
    title: str,
    messages: list[dict[str, Any]],
    options: VideoOptions,
    workdir: Path,
    assets: Mapping[str, bytes] | None = None,
) -> dict[Format, bytes]:
    """Render ``messages`` (the ``build_chat_export`` shape) to every format
    in ``options.fmt``. ``workdir`` is scratch: whatever is left there — the
    page, the raw recording, each encode — is removed before returning, on
    success and on failure alike, so a worker's disk does not fill with the
    remains of jobs.

    ``assets`` are the workspace files the transcript shows (a tool's
    ``[shown-files]``, an answer's ``![](path)``), by absolute path. The
    caller reads them — the CLI from ``--files``, a job from the item's
    files, for the paths ``Timeline.referenced_paths`` names — because this
    function runs in a thread with no store of its own."""
    ensure_tools(options)  # before a recording that would be thrown away
    timeline = build_timeline(title=title, messages=messages, options=options)
    html = render_player_html(timeline, options, assets=assets or {})
    scratch = workdir / "chat-video"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        recording = record(html, options, scratch, expected_ms=timeline.playback_ms)
        out: dict[Format, bytes] = {}
        for fmt in options.fmt:
            path = encode(recording, fmt, scratch / f"out.{fmt}")
            out[fmt] = path.read_bytes()
        return out
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
