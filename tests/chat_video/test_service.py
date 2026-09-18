"""`render_chat_video` — the one entry point the CLI calls today and a job
handler will call tomorrow (P3).

The doubles stand in for the browser and ffmpeg only; the timeline and the
page are real, so what reaches `record` is the page this transcript renders
to, and what `encode` is asked for is the option's format list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workspace_app.chat_video import service
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.render import RendererUnavailable
from workspace_app.chat_video.service import render_chat_video
from workspace_app.chat_video.timeline import build_timeline

_MESSAGES = [
    {"role": "user", "content": "hi", "author": "u"},
    {"role": "assistant", "content": "hello ![c](/chart.png)", "author": "AI"},
]


def test_it_records_the_rendered_page_once_and_encodes_each_format(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_record(html: str, options: VideoOptions, workdir: Path, *, expected_ms: int) -> Path:
        seen["html"] = html
        seen["expected_ms"] = expected_ms
        seen["workdir"] = workdir
        out = workdir / "recording.webm"
        out.write_bytes(b"WEBM")
        return out

    def fake_encode(src: Path, fmt: str, out: Path) -> Path:
        out.write_bytes(f"{fmt}:".encode() + src.read_bytes())
        return out

    monkeypatch.setattr(service, "record", fake_record)
    monkeypatch.setattr(service, "encode", fake_encode)

    result = render_chat_video(
        title="t",
        messages=_MESSAGES,
        options=VideoOptions(fmt=("gif", "mp4")),
        workdir=tmp_path,
        assets={"/chart.png": b"\x89PNG\r\n\x1a\n" + b"\0" * 8},
    )

    assert result == {"gif": b"gif:WEBM", "mp4": b"mp4:WEBM"}
    assert "hello" in str(seen["html"]) and "const TIMELINE" in str(seen["html"])
    # The assets reached the page: the answer's `![](/chart.png)` is a data URI.
    assert "data:image/png;base64," in str(seen["html"])
    # The deadline is set from what will PLAY, not from what was asked: a
    # squeezed transcript's deadline used to be its unsqueezed estimate — 40
    # messages of 500 characters capped at 1 s got a 24-minute deadline for
    # a 2.4-minute playback (computed with `build_timeline`); now 4 minutes.
    expected = seen["expected_ms"]
    assert isinstance(expected, int) and expected > 0
    assert (
        expected
        == build_timeline(
            title="t", messages=_MESSAGES, options=VideoOptions(fmt=("gif", "mp4"))
        ).playback_ms
    )
    assert not (tmp_path / "chat-video").exists()  # the scratch is gone


def test_the_scratch_dir_is_removed_even_when_the_browser_fails(monkeypatch, tmp_path):
    def failing_record(*_a, **_k) -> Path:
        raise RuntimeError("browser died")

    monkeypatch.setattr(service, "record", failing_record)

    try:
        render_chat_video(title="t", messages=_MESSAGES, options=VideoOptions(), workdir=tmp_path)
    except RuntimeError:
        pass
    else:  # pragma: no cover - the failure must propagate
        raise AssertionError("the browser's failure was swallowed")

    assert not any(tmp_path.iterdir())


def test_a_missing_ffmpeg_is_reported_before_the_browser_is_opened(monkeypatch, tmp_path):
    recorded = []
    monkeypatch.setattr(service, "record", lambda *a, **k: recorded.append(1))
    monkeypatch.setattr(
        service, "ensure_tools", lambda _o: (_ for _ in ()).throw(RendererUnavailable("ffmpeg"))
    )

    with pytest.raises(RendererUnavailable):
        render_chat_video(title="t", messages=_MESSAGES, options=VideoOptions(), workdir=tmp_path)
    assert recorded == []
