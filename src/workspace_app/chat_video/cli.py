"""``python -m workspace_app.chat_video export.chat.json -o demo.gif``

Every flag is a field of ``VideoOptions`` and nothing else: the CLI is one
of three readers of that struct (the job payload and the front-end form are
the other two), and adding a knob means adding a field, not a flag.

``--html`` writes the player page instead of recording — open it in a
browser, watch, edit the JSON, repeat — which is also the way to see what a
worker will render without owning a Chromium.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .options import VideoOptions
from .player import render_player_html
from .render import RendererUnavailable
from .service import render_chat_video
from .timeline import build_timeline

_FORMATS = ("gif", "mp4", "webm")


class Args(argparse.Namespace):
    source: Path
    out: Path
    html: Path | None
    files: Path | None
    options: VideoOptions


def load_assets(files_dir: Path, paths: list[str]) -> dict[str, bytes]:
    """The referenced workspace files, read from ``files_dir`` — the
    workspace as a folder on disk (a downloaded copy, or the item's own
    directory). Only ``paths`` are read, a missing one is left out (the page
    draws a card for it), and one that resolves outside ``files_dir`` is
    refused: the transcript is hand-edited, and ``/../etc/passwd`` in a
    shown-files line must not read the host."""
    root = files_dir.resolve()
    out: dict[str, bytes] = {}
    for path in paths:
        candidate = (root / path.lstrip("/")).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            continue
        out[path] = candidate.read_bytes()
    return out


def parse_args(argv: list[str]) -> Args:
    d = VideoOptions()
    p = argparse.ArgumentParser(
        prog="python -m workspace_app.chat_video",
        description="Render a chat export (.chat.json) as a video, without a model or a server.",
    )
    p.add_argument("source", type=Path, help="the .chat.json the Export button downloads")
    p.add_argument(
        "-o",
        "--out",
        type=Path,
        help="output file; its extension picks the format (default: <source>.gif)",
    )
    p.add_argument(
        "--fmt",
        action="append",
        choices=_FORMATS,
        default=[],
        help="an extra format to write beside --out",
    )
    p.add_argument("--html", type=Path, help="write the player page here and record nothing")
    p.add_argument(
        "--files",
        type=Path,
        help="the workspace folder the transcript's shown files and ![](path) images are read from",
    )
    f = p.add_argument_group("frame")
    f.add_argument("--width", type=int, default=d.width, help="output pixels")
    f.add_argument("--height", type=int, default=d.height)
    f.add_argument("--chat-width", type=int, default=d.chat_width, help="the chat column, in px")
    f.add_argument(
        "--scale",
        type=float,
        default=d.scale,
        help="UI enlargement; 0 = automatic from the frame (1.5 at 1080p, 3 at 4K)",
    )
    c = p.add_argument_group("camera")
    c.add_argument("--zoom", type=float, default=d.zoom, help="push-in on the composer; 1 = none")
    c.add_argument("--zoom-ms", type=int, default=d.zoom_ms)
    t = p.add_argument_group("tempo (ms)")
    t.add_argument("--type-speed", type=int, default=d.type_ms, help="per character typed")
    t.add_argument("--stream-speed", type=int, default=d.stream_ms, help="per character streamed")
    t.add_argument(
        "--tool-pause", type=int, default=d.tool_pause_ms, help="a tool card spins this long"
    )
    t.add_argument(
        "--speed", type=float, default=d.speed, help="uniform multiplier; 2 = twice as fast"
    )
    t.add_argument(
        "--max-seconds", type=int, default=d.max_seconds, help="ceiling; longer is squeezed"
    )
    p.add_argument("--tool-output-chars", type=int, default=d.tool_output_chars)
    p.add_argument(
        "--max-asset-bytes",
        type=int,
        default=d.max_asset_bytes,
        help="an image bigger than this is a file card, not inlined",
    )

    ns = p.parse_args(argv, namespace=Args())
    ns.out = ns.out or ns.source.with_suffix(".gif")
    primary = ns.out.suffix.lstrip(".").lower()
    if primary not in _FORMATS:
        p.error(f"--out must end in one of {', '.join(_FORMATS)} (got {ns.out.name!r})")
    fmt = (primary, *[x for x in ns.fmt if x != primary])
    ns.options = VideoOptions(
        width=ns.width, height=ns.height, chat_width=ns.chat_width, scale=ns.scale,
        zoom=ns.zoom, zoom_ms=ns.zoom_ms,
        type_ms=ns.type_speed, stream_ms=ns.stream_speed, tool_pause_ms=ns.tool_pause,
        speed=ns.speed, max_seconds=ns.max_seconds, tool_output_chars=ns.tool_output_chars,
        max_asset_bytes=ns.max_asset_bytes,
        fmt=fmt,
    )  # fmt: skip
    return ns


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    data = json.loads(ns.source.read_text(encoding="utf-8"))
    title = str(data.get("title") or ns.source.stem)
    messages = data.get("messages")
    if not isinstance(messages, list):
        print(
            f"{ns.source}: expected a 'messages' list (the .chat.json export shape)",
            file=sys.stderr,
        )
        return 2
    timeline = build_timeline(title=title, messages=messages, options=ns.options)
    wanted = timeline.referenced_paths()
    assets = load_assets(ns.files, wanted) if ns.files is not None else {}
    for missing in (p for p in wanted if p not in assets):
        where = f"not found under {ns.files}" if ns.files is not None else "pass --files DIR"
        print(f"note: {missing} shown as a card ({where})", file=sys.stderr)
    if ns.html is not None:
        ns.html.write_text(
            render_player_html(timeline, ns.options, assets=assets), encoding="utf-8"
        )
        print(f"wrote {ns.html} (estimated {timeline.estimated_ms / 1000:.1f}s)")
        return 0
    try:
        with tempfile.TemporaryDirectory(prefix="chat-video-") as tmp:
            videos = render_chat_video(
                title=title,
                messages=messages,
                options=ns.options,
                workdir=Path(tmp),
                assets=assets,
            )
    except RendererUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3
    for fmt, blob in videos.items():
        target = ns.out if fmt == ns.options.fmt[0] else ns.out.with_suffix(f".{fmt}")
        target.write_bytes(blob)
        print(f"wrote {target} ({len(blob) / 1e6:.1f} MB)")
    return 0


__all__ = ["Args", "load_assets", "main", "parse_args"]
