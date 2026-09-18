"""The heavy end: a headless browser records the page, ffmpeg encodes it (P3).

Neither tool is a dependency of the API image, so the module must import
without them and fail with a sentence — naming what to install — when they
are asked for and absent. The real recording is an integration test: it needs
Chromium and ffmpeg on the box, which CI does not have.
"""

from __future__ import annotations

import builtins
import shutil
import subprocess
from pathlib import Path

import pytest

from workspace_app.chat_video import render
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.render import RendererUnavailable, encode, record


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
        assert out.stat().st_size > 0
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
    assert Path(out).suffix == ".webm"
