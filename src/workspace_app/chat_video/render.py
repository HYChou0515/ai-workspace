"""Record the page with a headless browser and encode the result.

The two heavy tools live behind this module alone. Playwright is imported
inside ``record`` and ffmpeg is a subprocess inside ``encode``, so importing
the package — and everything up to the HTML — needs neither. An API pod that
never renders a video carries none of it; the worker pod that does installs
the ``chat-video`` extra and Chromium.

Every external wait is bounded and fails by name: the browser is given the
playback estimate plus slack (``RecordingTimedOut``), ffmpeg a fixed ceiling
(a ``RuntimeError`` saying so). A job that hangs is a job the worker cannot
finish; a job that fails says why.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .options import VideoOptions

Format = str


class RendererUnavailable(RuntimeError):
    """A tool this rendering needs is not installed here. The message says
    which and what to install — the one thing the person can act on."""


class RecordingTimedOut(RuntimeError):
    """The page did not mark itself done before the deadline. No webm comes
    out of it — the deadline is the last resort, and it fails the job by
    name rather than handing back a video cut at an arbitrary frame."""


def ensure_tools(options: VideoOptions) -> None:
    """Refuse up front what would otherwise be refused after the whole
    recording had run: ffmpeg is only needed to re-encode (``webm`` is a
    straight copy), so the check is per format asked."""
    if any(f != "webm" for f in options.fmt) and shutil.which("ffmpeg") is None:
        raise RendererUnavailable("encoding needs ffmpeg on PATH (apt install ffmpeg)")


# ffmpeg gets this long per encode, whatever the clip: a re-encode of a
# 90-second recording is seconds; minutes means something is wrong.
_ENCODE_TIMEOUT_S = 300


def record(html: str, options: VideoOptions, workdir: Path, *, expected_ms: int = 0) -> Path:
    """Play ``html`` in a headless Chromium sized to the frame and return the
    ``.webm`` it recorded. The page marks ``document.body.dataset.done`` when
    its script is finished; the deadline (``expected_ms`` × 1.5 + 30 s of
    slack) fails it instead, as :class:`RecordingTimedOut`."""
    try:
        from playwright.sync_api import Error, TimeoutError, ViewportSize, sync_playwright
    except ModuleNotFoundError as exc:
        raise RendererUnavailable(
            "recording needs Playwright: install the `chat-video` extra "
            "(`uv sync --extra chat-video`) and run `playwright install chromium`"
        ) from exc

    workdir.mkdir(parents=True, exist_ok=True)
    page_file = workdir / "player.html"
    page_file.write_text(html, encoding="utf-8")
    video_dir = workdir / "video"
    video_dir.mkdir(exist_ok=True)
    size = ViewportSize(width=options.width, height=options.height)
    # The page's own playback estimate, with room for the browser to be
    # slower than the model, plus 30 s of slack on top for launch and paint.
    deadline_ms = int(expected_ms * 1.5) + 30_000

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Error as exc:
            # The Python package is here but `playwright install chromium`
            # was never run — Playwright's own message is a traceback with a
            # boxed hint. One sentence, the same install step.
            if "Executable doesn't exist" in str(exc):
                raise RendererUnavailable(
                    "recording needs Chromium: run `playwright install chromium` once"
                ) from exc
            raise
        try:
            context = browser.new_context(
                viewport=size, record_video_dir=str(video_dir), record_video_size=size
            )
            page = context.new_page()
            page.goto(page_file.as_uri())
            try:
                page.wait_for_function("document.body.dataset.done === '1'", timeout=deadline_ms)
            except TimeoutError as exc:
                raise RecordingTimedOut(
                    f"the page did not finish within {deadline_ms / 1000:.0f}s "
                    f"(expected {expected_ms / 1000:.0f}s): the browser may be starved, or "
                    "the transcript is too long to play in that time — cut it down"
                ) from exc
            page.wait_for_timeout(300)  # let the last frame land before the file closes
            video = page.video
            assert video is not None  # recording was requested on the context
            recorded = Path(video.path())
            context.close()  # flushes the video
        finally:
            browser.close()
    out = workdir / "recording.webm"
    shutil.move(recorded, out)
    return out


def encode(src: Path, fmt: Format, out: Path) -> Path:
    """Re-encode the recording. ``gif`` builds a palette first (the difference
    between a GIF with banding and one without); ``mp4`` is H.264 yuv420p so
    slide decks accept it; ``webm`` is a straight copy."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RendererUnavailable("encoding needs ffmpeg on PATH (apt install ffmpeg)")
    if fmt == "webm":
        shutil.copyfile(src, out)
        return out
    if fmt == "gif":
        filters = (
            "fps=12,split[s0][s1];[s0]palettegen=max_colors=200[p];[s1][p]paletteuse=dither=bayer"
        )
        args = [ffmpeg, "-v", "error", "-y", "-i", str(src), "-vf", filters, str(out)]
    elif fmt == "mp4":
        args = [
            ffmpeg, "-v", "error", "-y", "-i", str(src),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        ]  # fmt: skip
    else:
        raise ValueError(f"unknown format {fmt!r} (gif, mp4 or webm)")
    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=_ENCODE_TIMEOUT_S)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"ffmpeg failed encoding {fmt}: " + " | ".join(tail)) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg did not finish encoding {fmt} within {_ENCODE_TIMEOUT_S}s"
        ) from exc
    return out
