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
import time
from collections.abc import Callable
from pathlib import Path

from .options import VideoOptions

Format = str
StopCheck = Callable[[], bool]
"""Asked between slices of the recording and each second of an encode; True
means abandon the render — the worker answers it from the progress file."""


class RendererUnavailable(RuntimeError):
    """A tool this rendering needs is not installed here. The message says
    which and what to install — the one thing the person can act on."""


class RecordingTimedOut(RuntimeError):
    """The page did not mark itself done before the deadline. No webm comes
    out of it — the deadline is the last resort, and it fails the job by
    name rather than handing back a video cut at an arbitrary frame."""


class Cancelled(RuntimeError):
    """``should_stop`` answered True: the browser was closed or ffmpeg killed
    at once, whatever it had produced is discarded, and no file is written."""


# A recording is waited for in slices this long, with ``should_stop`` asked
# between them — a cancel reaches a 3-minute recording within one slice.
RECORD_SLICE_MS = 2_000


def ensure_tools(options: VideoOptions) -> None:
    """Refuse up front what would otherwise be refused after the whole
    recording had run: ffmpeg is only needed to re-encode (``webm`` is a
    straight copy), so the check is per format asked."""
    if any(f != "webm" for f in options.fmt) and shutil.which("ffmpeg") is None:
        raise RendererUnavailable("encoding needs ffmpeg on PATH (apt install ffmpeg)")


# ffmpeg gets this long per encode, whatever the clip: a re-encode of a
# 90-second recording is seconds; minutes means something is wrong.
_ENCODE_TIMEOUT_S = 300


def record(
    html: str,
    options: VideoOptions,
    workdir: Path,
    *,
    expected_ms: int = 0,
    should_stop: StopCheck | None = None,
) -> Path:
    """Play ``html`` in a headless Chromium sized to the frame and return the
    ``.webm`` it recorded. The page marks ``document.body.dataset.done`` when
    its script is finished; the deadline (``expected_ms`` × 1.5 + 30 s of
    slack) fails it instead, as :class:`RecordingTimedOut`. The wait is in
    ``RECORD_SLICE_MS`` slices with ``should_stop`` asked between them: a
    True closes the browser then and there and raises :class:`Cancelled`."""
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
            started = time.monotonic()
            while True:
                left_ms = deadline_ms - int((time.monotonic() - started) * 1000)
                if left_ms <= 0:
                    raise RecordingTimedOut(
                        f"the page did not finish within {deadline_ms / 1000:.0f}s "
                        f"(expected {expected_ms / 1000:.0f}s): the browser may be starved, or "
                        "the transcript is too long to play in that time — cut it down"
                    )
                try:
                    page.wait_for_function(
                        "document.body.dataset.done === '1'",
                        timeout=min(RECORD_SLICE_MS, left_ms),
                    )
                    break
                except TimeoutError:
                    if should_stop is not None and should_stop():
                        raise Cancelled("the recording was cancelled") from None
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


def encode(src: Path, fmt: Format, out: Path, *, should_stop: StopCheck | None = None) -> Path:
    """Re-encode the recording. ``gif`` builds a palette first (the difference
    between a GIF with banding and one without); ``mp4`` is H.264 yuv420p so
    slide decks accept it; ``webm`` is a straight copy.

    Memory is bounded per frame, not per clip — a worker pod's limit is set
    from it (measured on the 41-second sample at 1080p). The gif is TWO
    passes (a palette PNG, then the frames through it): the single-pass
    ``split → palettegen → paletteuse`` graph holds every frame until the
    palette is known — 4,546 MB; two passes peak at 230–640 MB (bimodal:
    some runs fill a ~50-frame queue early and hold there; a 120-second
    clip peaks no higher), same bytes out. The mp4 asks libx264 for
    ``veryfast`` on two threads: the default preset and thread count took
    1,329 MB, this 273–320 MB and a second longer. The decoder is held to
    two threads as well (``-threads 2`` before ``-i``): VP8 frame-threading
    otherwise keeps as many 8 MB frames in flight as there are cores."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RendererUnavailable("encoding needs ffmpeg on PATH (apt install ffmpeg)")
    if fmt == "webm":
        shutil.copyfile(src, out)
        return out
    if fmt == "gif":
        palette = out.with_name(out.name + ".palette.png")
        passes = [
            [ffmpeg, "-v", "error", "-y", "-i", str(src),
             "-vf", "fps=12,palettegen=max_colors=200", str(palette)],
            [ffmpeg, "-v", "error", "-y", "-threads", "2", "-i", str(src),
             "-vf", f"movie={palette}[p];[in]fps=12[x];[x][p]paletteuse=dither=bayer", str(out)],
        ]  # fmt: skip
    elif fmt == "mp4":
        passes = [
            [ffmpeg, "-v", "error", "-y", "-threads", "2", "-i", str(src),
             "-c:v", "libx264", "-preset", "veryfast", "-threads", "2",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        ]  # fmt: skip
    else:
        raise ValueError(f"unknown format {fmt!r} (gif, mp4 or webm)")
    try:
        for args in passes:
            _run_ffmpeg(args, fmt, should_stop)
    except BaseException:
        out.unlink(missing_ok=True)  # nothing half-written survives a refusal
        raise
    finally:
        if fmt == "gif":
            out.with_name(out.name + ".palette.png").unlink(missing_ok=True)
    return out


def _run_ffmpeg(args: list[str], fmt: Format, should_stop: StopCheck | None) -> None:
    """One ffmpeg pass, polled every second so a stop reaches it: ``should_stop``
    True kills it and raises :class:`Cancelled`; the pass overrunning
    ``_ENCODE_TIMEOUT_S`` kills it and is a sentence naming the limit; a
    non-zero exit carries ffmpeg's last words."""
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    waited = 0
    while True:
        try:
            code = proc.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            waited += 1
            if should_stop is not None and should_stop():
                proc.kill()
                proc.communicate()
                raise Cancelled("the encode was cancelled") from None
            if waited >= _ENCODE_TIMEOUT_S:
                proc.kill()
                proc.communicate()
                raise RuntimeError(
                    f"ffmpeg did not finish encoding {fmt} within {_ENCODE_TIMEOUT_S}s"
                ) from None
    _, stderr = proc.communicate()
    if code:
        tail = (stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"ffmpeg failed encoding {fmt}: " + " | ".join(tail))
