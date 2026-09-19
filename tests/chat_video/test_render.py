"""The heavy end: a headless browser records the page, ffmpeg encodes it (P3).

Neither tool is a dependency of the API image, so the module must import
without them and fail with a sentence — naming what to install — when they
are asked for and absent. The code around the browser is exercised against a
fake `playwright.sync_api`; the two integration tests run the real Chromium
and the real ffmpeg, which the box has and CI does not.
"""

from __future__ import annotations

import builtins
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from workspace_app.chat_video import render
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.render import (
    RECORD_SLICE_MS,
    Cancelled,
    RecordingTimedOut,
    RendererUnavailable,
    encode,
    ensure_tools,
    record,
)


def test_recording_without_playwright_says_what_to_install(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def no_playwright(name, *a, **k):
        if name.startswith("playwright"):
            raise ModuleNotFoundError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_playwright)

    with pytest.raises(RendererUnavailable, match="chat-video"):
        record("<html></html>", VideoOptions(), tmp_path)


def test_encoding_without_ffmpeg_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RendererUnavailable, match="ffmpeg"):
        encode(tmp_path / "in.webm", "gif", tmp_path / "out.gif")


class _FakeFfmpeg:
    """A `subprocess.Popen` stand-in: exits with `returncode` after `ticks`
    one-second waits (each a `TimeoutExpired`, as the real `wait(timeout=1)`),
    with `stderr` as its last words; `killed` records a kill."""

    def __init__(self, *, returncode: int = 0, ticks: int = 0, stderr: str = ""):
        self.returncode_after = returncode
        self.ticks_left = ticks
        self.stderr_text = stderr
        self.killed = False
        self.returncode: int | None = None
        self.args: list[str] = []
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):  # the Popen call itself
        self.args = list(args)
        self.calls.append(list(args))
        Path(args[-1]).write_bytes(b"partial")  # ffmpeg opens its output at once
        # ffmpeg's stderr goes to a FILE (see `_run_ffmpeg`); the fake writes
        # its last words there, as the real one would.
        err = kw.get("stderr")
        if hasattr(err, "write"):
            err.write(self.stderr_text)
            err.flush()
        return self

    def wait(self, timeout: float | None = None):
        if self.ticks_left > 0:
            self.ticks_left -= 1
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        self.returncode = self.returncode_after
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, timeout: float | None = None):
        return "", ""


def test_an_ffmpeg_failure_carries_its_own_last_words(monkeypatch, tmp_path):
    """ffmpeg's stderr is the only diagnosis there is; the tail of it rides on
    the exception instead of being lost to a log nobody reads."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        render.subprocess,
        "Popen",
        _FakeFfmpeg(returncode=1, stderr="…\nInvalid data found when processing input\n"),
    )

    with pytest.raises(RuntimeError, match="Invalid data found"):
        encode(tmp_path / "in.webm", "mp4", tmp_path / "out.mp4")


def test_the_gif_passes_are_both_thread_capped_and_the_palette_is_an_input(monkeypatch, tmp_path):
    """Two things the code did not do while its docstring said so: the
    palette pass had no `-threads 2` before `-i` (the decoder ran on every
    core; measured 164 MB vs 123 MB capped), and the second pass named the
    palette INSIDE a filter graph (`movie=<path>`), where a `:` or `,` in the
    scratch path breaks the parse. The palette is a second input now."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    proc = _FakeFfmpeg()
    monkeypatch.setattr(render.subprocess, "Popen", proc)

    encode(tmp_path / "in.webm", "gif", tmp_path / "out.gif")

    first, second = proc.calls
    for args in (first, second):
        assert args.index("-threads") < args.index("-i") and args[args.index("-threads") + 1] == "2"
    assert second.count("-i") == 2 and not any("movie=" in a for a in second)


def test_an_ffmpeg_that_talks_a_lot_still_finishes_with_its_last_words(monkeypatch, tmp_path):
    """stderr used to be a pipe nobody read until exit: a pass writing more
    than the pipe holds (64 KB — per-frame decode errors on a damaged
    recording) blocked on it and was only killed at the 300-second cap,
    reported as a hang. It writes to a file now; the tail is still the
    sentence."""
    talker = tmp_path / "ffmpeg"
    talker.write_text(
        "#!/bin/sh\n"
        'python3 -c "import sys; '
        "sys.stderr.write('noise\\n' * 40000 + 'the real reason\\n'); sys.exit(1)\"\n"
    )
    talker.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda _name: str(talker))
    monkeypatch.setattr(render, "_ENCODE_TIMEOUT_S", 5)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="the real reason"):
        encode(tmp_path / "in.webm", "mp4", tmp_path / "out.mp4")
    assert time.monotonic() - started < 4  # not the timeout


def test_an_ffmpeg_that_hangs_is_a_sentence_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    proc = _FakeFfmpeg(ticks=10_000)
    monkeypatch.setattr(render.subprocess, "Popen", proc)
    monkeypatch.setattr(render, "_ENCODE_TIMEOUT_S", 3)

    with pytest.raises(RuntimeError, match="within 3s"):
        encode(tmp_path / "in.webm", "gif", tmp_path / "out.gif")

    assert proc.killed  # not left running behind the sentence
    assert not list(tmp_path.iterdir())  # and nothing half-written left behind


@pytest.mark.parametrize("fmt", ["mp4", "gif"])
def test_a_stop_during_an_encode_kills_ffmpeg_and_is_cancelled(monkeypatch, tmp_path, fmt):
    """The encode is polled every second with `should_stop` asked each time:
    a True kills ffmpeg then and there, raises `Cancelled`, and leaves no
    output — not the half-written file ffmpeg had opened, not the gif's
    palette."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    proc = _FakeFfmpeg(ticks=10_000)
    monkeypatch.setattr(render.subprocess, "Popen", proc)
    asked: list[int] = []

    def should_stop() -> bool:
        asked.append(1)
        return len(asked) >= 2

    with pytest.raises(Cancelled):
        encode(tmp_path / "in.webm", fmt, tmp_path / f"out.{fmt}", should_stop=should_stop)

    assert proc.killed and len(asked) == 2
    assert not list(tmp_path.iterdir())  # no output, no palette


def test_ensure_tools_names_a_missing_ffmpeg_before_anything_is_recorded(monkeypatch):
    """A missing ffmpeg used to be found after the whole recording had run."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RendererUnavailable, match="ffmpeg"):
        ensure_tools(VideoOptions(fmt=("gif",)))
    ensure_tools(VideoOptions(fmt=("webm",)))  # a straight copy needs no ffmpeg


def _fake_playwright(
    monkeypatch, launch_error_text: str | None = None, *, done_after_waits: int = 1
):
    """A `playwright.sync_api` whose Chromium either cannot launch or plays
    the page — `done` on the `done_after_waits`-th wait, a TimeoutError on
    each wait before that, as the real one when a slice runs out — enough to
    reach the code around the browser. `mod.closed` counts browser closes."""
    import types

    state: dict[str, int] = {"waits": 0, "closed": 0}
    timeouts: list[int] = []

    class Error(Exception):
        pass

    class TimeoutError(Error):  # noqa: A001 — playwright's own name
        pass

    class _Video:
        def __init__(self, path):
            self._p = path

        def path(self):
            return self._p

    class _Page:
        def __init__(self, video_dir):
            self.video = _Video(video_dir / "raw.webm")

        def goto(self, _url):
            pass

        def wait_for_function(self, _expr, timeout):
            state["waits"] += 1
            timeouts.append(timeout)
            if timeout < 1 or state["waits"] < done_after_waits:
                raise TimeoutError(f"Timeout {timeout}ms exceeded")

        def wait_for_timeout(self, _ms):
            pass

    class _Context:
        def __init__(self, video_dir):
            self._dir = video_dir
            self._page = None

        def new_page(self):
            self._page = _Page(self._dir)
            return self._page

        def close(self):
            # As in the real API: the webm exists only once the context is
            # closed. A `record()` that moved the file before closing would
            # pass a fake that wrote it earlier and fail live.
            assert self._page is not None
            self._page.video._p.write_bytes(b"WEBM")

    class _Browser:
        def new_context(self, *, viewport, record_video_dir, record_video_size):
            return _Context(Path(record_video_dir))

        def close(self):
            state["closed"] += 1

    class _Chromium:
        def launch(self):
            if launch_error_text is not None:
                raise Error(launch_error_text)
            return _Browser()

    class _PW:
        chromium = _Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    mod = types.ModuleType("playwright.sync_api")
    mod.sync_playwright = lambda: _PW()  # ty: ignore[unresolved-attribute]
    mod.Error = Error  # ty: ignore[unresolved-attribute]
    mod.TimeoutError = TimeoutError  # ty: ignore[unresolved-attribute]
    mod.ViewportSize = dict  # ty: ignore[unresolved-attribute]
    mod.state = state  # ty: ignore[unresolved-attribute]
    mod.timeouts = timeouts  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mod)
    return mod


def test_a_chromium_that_was_never_installed_is_the_install_sentence(monkeypatch, tmp_path):
    """The Python package installed, `playwright install chromium` not run:
    Playwright raises `Error: Executable doesn't exist at …` plus a boxed
    hint. That is the one gap in the install steps a person hits most."""
    _fake_playwright(monkeypatch, "BrowserType.launch: Executable doesn't exist at /x")

    with pytest.raises(RendererUnavailable, match="playwright install chromium"):
        record("<html></html>", VideoOptions(), tmp_path)


def test_a_page_that_never_finishes_fails_the_recording_by_name(monkeypatch, tmp_path):
    """The deadline is the last resort and it says so: no webm comes out of a
    page that hung, and the error names the deadline rather than a traceback
    from inside Playwright."""
    _fake_playwright(monkeypatch)

    with pytest.raises(RecordingTimedOut, match="did not finish"):
        record("<html></html>", VideoOptions(), tmp_path, expected_ms=-40_000)


def test_a_recording_waits_in_slices_and_a_stop_between_them_closes_the_browser(
    monkeypatch, tmp_path
):
    """The recording is the long phase — as long as the video — so the cancel
    has to reach it: `record` waits for `done` in short slices and asks
    `should_stop` between them. A stop closes the browser at once (the
    partial video is discarded) and raises `Cancelled`; nothing is moved to
    `recording.webm`."""
    mod = _fake_playwright(monkeypatch, done_after_waits=1000)
    asked: list[int] = []

    def should_stop() -> bool:
        asked.append(1)
        return len(asked) >= 3

    with pytest.raises(Cancelled):
        record(
            "<html></html>", VideoOptions(), tmp_path, expected_ms=60_000, should_stop=should_stop
        )

    assert len(asked) == 3  # one ask per slice, until the third said stop
    assert mod.state["waits"] == 3 and mod.state["closed"] == 1
    # Each wait is at most one slice — that is what puts a cancel within
    # reach of a three-minute recording, rather than one wait to the deadline.
    assert mod.timeouts == [RECORD_SLICE_MS] * 3
    assert not (tmp_path / "recording.webm").exists()


def test_a_recording_that_finishes_in_a_later_slice_is_the_webm(monkeypatch, tmp_path):
    """Slices are not a deadline: a page that needs several is still recorded."""
    mod = _fake_playwright(monkeypatch, done_after_waits=4)

    out = record("<html></html>", VideoOptions(), tmp_path, expected_ms=60_000)

    assert out.read_bytes() == b"WEBM" and mod.state["waits"] == 4


def test_a_recorded_page_comes_back_as_the_workdirs_webm(monkeypatch, tmp_path):
    _fake_playwright(monkeypatch)

    out = record("<html></html>", VideoOptions(), tmp_path, expected_ms=1000)

    assert out == tmp_path / "recording.webm" and out.read_bytes() == b"WEBM"


@pytest.mark.integration
def test_a_synthetic_clip_encodes_to_every_format(tmp_path):
    """Real ffmpeg on a one-second generated clip: each format comes out
    non-empty and probes back at the size it went in."""
    src = tmp_path / "in.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=1",
            "-c:v",
            "libvpx",
            src,
        ],
        check=True,
    )
    for fmt in ("gif", "mp4", "webm"):
        out = encode(src, fmt, tmp_path / f"out.{fmt}")
        assert out.exists() and out.stat().st_size > 0
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                out,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert probe == "320,180"


@pytest.mark.integration
def test_a_real_chromium_records_a_page_at_the_asked_size(tmp_path):
    """The plan's first acceptance line, as a test: a headless Chromium plays
    a page that finishes at once, and the webm probes back at exactly the
    frame the options asked for."""
    html = "<html><body><script>document.body.dataset.done='1'</script></body></html>"

    out = record(html, VideoOptions(width=400, height=300), tmp_path, expected_ms=500)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", out],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert probe == "400,300"


def test_an_unknown_format_is_refused_before_ffmpeg_runs(monkeypatch, tmp_path):
    """Unreachable through `VideoOptions` (which validates `fmt`), kept as the
    function's own contract for a caller that bypasses it."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    with pytest.raises(ValueError, match="unknown format"):
        encode(tmp_path / "in.webm", "exe", tmp_path / "out.exe")


def test_a_chromium_that_fails_for_another_reason_is_not_dressed_up(monkeypatch, tmp_path):
    """Only the missing-executable message maps to the install sentence; any
    other launch failure is Playwright's own, unchanged."""
    _fake_playwright(
        monkeypatch, "BrowserType.launch: Target page, context or browser has been closed"
    )

    with pytest.raises(Exception, match="has been closed") as caught:
        record("<html></html>", VideoOptions(), tmp_path)
    assert not isinstance(caught.value, RendererUnavailable)


def _peak_rss_of_ffmpeg_under(run) -> float:
    """Run ``run()`` while sampling every ffmpeg process below this test
    (``ps``, 0.2 s); the peak RSS in MB across them."""
    import os
    import threading
    import time

    me = os.getpid()
    peak = 0.0
    stop = False

    def sample() -> None:
        nonlocal peak
        while not stop:
            table = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,rss=,args="], capture_output=True, text=True
            ).stdout
            kids: dict[int, list[tuple[int, float, str]]] = {}
            for ln in table.splitlines():
                parts = ln.split(None, 3)
                if len(parts) == 4:
                    kids.setdefault(int(parts[1]), []).append(
                        (int(parts[0]), int(parts[2]) / 1024, parts[3])
                    )
            todo = [me]
            while todo:
                for pid, rss, args in kids.get(todo.pop(), []):
                    if "ffmpeg" in args:
                        peak = max(peak, rss)
                    todo.append(pid)
            time.sleep(0.2)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    try:
        run()
    finally:
        stop = True
        th.join()
    return peak


@pytest.mark.integration
@pytest.mark.parametrize(("fmt", "cap_mb"), [("gif", 1024), ("mp4", 512)])
def test_encoding_a_1080p_clip_stays_within_the_measured_bound(tmp_path, fmt, cap_mb):
    """Measured on the 41-second sample at 1080p: the single-pass gif
    (`split → palettegen → paletteuse`) held every frame until the palette
    was known — 4,546 MB — and libx264 at its default preset and thread
    count took 1,329 MB. Now: mp4 273–320 MB (five runs); gif's second
    pass 230–640 MB — bimodal, some runs fill a ~50-frame queue in the
    first seconds and then hold, and a 120-second clip peaks no higher than
    a 20-second one, so it is bounded, not growing. A worker pod's memory
    limit is set from these, so they are pinned on a 20-second synthetic
    1080p clip (the cost is per frame, not per picture)."""
    src = tmp_path / "in.webm"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=25:duration=20",
         "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", src],
        check=True,
    )  # fmt: skip

    peak = _peak_rss_of_ffmpeg_under(lambda: encode(src, fmt, tmp_path / f"out.{fmt}"))

    assert 0 < peak <= cap_mb, f"{fmt}: ffmpeg peaked at {peak:.0f} MB"
