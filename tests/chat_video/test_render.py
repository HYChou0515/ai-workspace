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
from pathlib import Path

import pytest

from workspace_app.chat_video import render
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.render import (
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


def test_an_ffmpeg_failure_carries_its_own_last_words(monkeypatch, tmp_path):
    """ffmpeg's stderr is the only diagnosis there is; the tail of it rides on
    the exception instead of being lost to a log nobody reads."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def boom(*_a, **_k):
        raise subprocess.CalledProcessError(
            1, ["ffmpeg"], stderr="…\nInvalid data found when processing input\n"
        )

    monkeypatch.setattr(render.subprocess, "run", boom)

    with pytest.raises(RuntimeError, match="Invalid data found"):
        encode(tmp_path / "in.webm", "mp4", tmp_path / "out.mp4")


def test_an_ffmpeg_that_hangs_is_a_sentence_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(["ffmpeg"], 300)

    monkeypatch.setattr(render.subprocess, "run", hang)

    with pytest.raises(RuntimeError, match="300"):
        encode(tmp_path / "in.webm", "gif", tmp_path / "out.gif")


def test_ensure_tools_names_a_missing_ffmpeg_before_anything_is_recorded(monkeypatch):
    """A missing ffmpeg used to be found after the whole recording had run."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RendererUnavailable, match="ffmpeg"):
        ensure_tools(VideoOptions(fmt=("gif",)))
    ensure_tools(VideoOptions(fmt=("webm",)))  # a straight copy needs no ffmpeg


def _fake_playwright(monkeypatch, launch_error_text: str | None = None):
    """A `playwright.sync_api` whose Chromium either cannot launch or plays
    the page instantly — enough to reach the code around the browser."""
    import types

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
            if timeout < 1:
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
            pass

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
