"""``python -m workspace_app.chat_video export.chat.json -o demo.gif``

Every field of ``VideoOptions`` has a flag here, and the flags that are not
fields (``-o``, ``--fmt``, ``--html``, ``--files``) only say where things
come from and go. The CLI is one of three readers of that struct (the job
payload and the front-end form are the other two), so a new knob is a new
field, not a new flag.

Exit codes: 2 the input or an option is wrong (one sentence naming what);
3 a tool is missing (what to install); 4 the render failed (why).

``--html`` writes the player page instead of recording — open it in a
browser, watch, edit the JSON, repeat — which is also the way to see what a
worker will render without owning a Chromium.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from ..kb.chat_export import parse_chat_export
from .options import FORMATS, VideoOptions
from .player import NOT_HANDED, decide_assets, render_player_html
from .render import RendererUnavailable
from .service import render_chat_video
from .timeline import build_timeline


class Args(argparse.Namespace):
    source: Path
    out: Path
    html: Path | None
    files: Path | None
    options: VideoOptions
    chromium: str


def load_assets(files_dir: Path, paths: list[str], *, max_bytes: int) -> dict[str, bytes]:
    """The referenced workspace files, read from ``files_dir`` — the
    workspace as a folder on disk (a downloaded copy, or the item's own
    directory). Only ``paths`` are read, and only when they fit
    ``max_bytes`` (checked before the read: a 300 MB file the page would
    never inline is not pulled into memory first). A missing, unreadable or
    oversized one is left out — the page draws a card, or the alt text — and
    one that resolves outside ``files_dir`` is refused: the transcript is
    hand-edited, and ``/../etc/passwd`` in a shown-files line must not read
    the host. A path the OS itself rejects (a NUL byte, a 5,000-character
    name) is left out the same way, not a traceback."""
    root = files_dir.resolve()  # the caller checked this resolves (main)
    out: dict[str, bytes] = {}
    for path in paths:
        try:
            candidate = (root / path.lstrip("/")).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                continue
            if candidate.stat().st_size > max_bytes:
                continue
            out[path] = candidate.read_bytes()
        except (OSError, ValueError, RuntimeError):  # RuntimeError: a symlink loop, on 3.12
            continue
    return out


def _is_dir(path: Path) -> bool:
    """Whether ``--files`` names a folder that can be opened. A symlink
    loop is a ``RuntimeError`` from ``resolve`` (on 3.12), a missing folder
    an ``OSError``; either is the operator's typo — one line, exit 2, not a
    traceback."""
    try:
        return path.resolve(strict=True).is_dir()
    except (OSError, RuntimeError):
        return False


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
        choices=FORMATS,
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
        help="an image bigger than this is not inlined (a file card, or the alt text)",
    )
    p.add_argument(
        "--max-assets-total-bytes",
        type=int,
        default=d.max_assets_total_bytes,
        help="the page's whole budget for inlined images, first-fit in reading order",
    )
    p.add_argument(
        "--chromium",
        default="",
        metavar="PATH",
        help="a Chromium binary to record with (e.g. /usr/bin/chromium from apt) instead of "
        "the one `playwright install chromium` downloads; the worker's `chat_video.chromium_path`",
    )

    ns = p.parse_args(argv, namespace=Args())
    ns.out = ns.out or ns.source.with_suffix(".gif")
    primary = ns.out.suffix.lstrip(".").lower()
    if primary not in FORMATS:
        p.error(f"--out must end in one of {', '.join(FORMATS)} (got {ns.out.name!r})")
    fmt = (primary, *[x for x in ns.fmt if x != primary])
    try:
        ns.options = _options(ns, fmt)
    except ValueError as exc:
        p.error(str(exc))
    return ns


def _options(ns: Args, fmt: tuple[str, ...]) -> VideoOptions:
    return VideoOptions(
        width=ns.width, height=ns.height, chat_width=ns.chat_width, scale=ns.scale,
        zoom=ns.zoom, zoom_ms=ns.zoom_ms,
        type_ms=ns.type_speed, stream_ms=ns.stream_speed, tool_pause_ms=ns.tool_pause,
        speed=ns.speed, max_seconds=ns.max_seconds, tool_output_chars=ns.tool_output_chars,
        max_asset_bytes=ns.max_asset_bytes, max_assets_total_bytes=ns.max_assets_total_bytes,
        fmt=fmt,
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    # The validator the KB upload runs on these same files: one sentence
    # naming the part of a hand-edited file to fix, never a traceback.
    try:
        title, messages = parse_chat_export(ns.source.read_bytes())
    except ValueError as exc:
        print(f"{ns.source}: {exc}", file=sys.stderr)
        return 2
    if ns.files is not None and not _is_dir(ns.files):
        print(f"--files {ns.files}: not a folder that can be opened", file=sys.stderr)
        return 2
    timeline = build_timeline(title=title, messages=messages, options=ns.options)
    plays = f"will play {timeline.playback_ms / 1000:.1f}s"
    if timeline.time_scale < 1:
        plays += f" (squeezed from {timeline.estimated_ms / 1000:.1f}s to fit --max-seconds)"
    wanted = timeline.referenced_paths()
    assets = (
        load_assets(ns.files, wanted, max_bytes=ns.options.max_asset_bytes)
        if ns.files is not None
        else {}
    )
    # The page's own verdict per path, not "what was read": a file can be
    # read and still not drawn (over the budget). A declared image then
    # becomes a card; an answer's `![]()` becomes its alt text. (A declared
    # non-image is a card by design and is not in the list; what the bytes
    # of a sent picture ARE is the browser's business, as in the chat.)
    for path, verdict in decide_assets(timeline.wanted_files(), assets, ns.options).items():
        why = verdict.why
        if not why:
            continue
        if why == NOT_HANDED:
            why = (
                f"not under {ns.files}, or too big" if ns.files is not None else "pass --files DIR"
            )
        print(f"note: {path} will not be drawn ({why})", file=sys.stderr)
    if ns.html is not None:
        ns.html.write_text(
            render_player_html(timeline, ns.options, assets=assets), encoding="utf-8"
        )
        print(f"wrote {ns.html} ({plays})")
        return 0
    print(plays)
    try:
        with tempfile.TemporaryDirectory(prefix="chat-video-") as tmp:
            videos = render_chat_video(
                title=title,
                messages=messages,
                options=ns.options,
                workdir=Path(tmp),
                assets=assets,
                chromium_path=ns.chromium,
            )
    except RendererUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except RuntimeError as exc:  # RecordingTimedOut, a failed ffmpeg
        print(f"render failed: {exc}", file=sys.stderr)
        return 4
    for fmt, blob in videos.items():
        target = ns.out if fmt == ns.options.fmt[0] else ns.out.with_suffix(f".{fmt}")
        target.write_bytes(blob)
        print(f"wrote {target} ({len(blob) / 1e6:.1f} MB)")
    return 0


__all__ = ["Args", "load_assets", "main", "parse_args"]
