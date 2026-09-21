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


@pytest.mark.parametrize("with_stages", [True, False], ids=["job", "cli"])
def test_it_records_the_rendered_page_once_and_encodes_each_format(
    monkeypatch, tmp_path, with_stages
):
    """`on_stage` is the job's; the CLI passes none and runs the same way."""
    seen: dict[str, object] = {}
    encode_stops: list[object] = []

    def fake_record(
        html: str,
        options: VideoOptions,
        workdir: Path,
        *,
        expected_ms: int,
        should_stop,
        chromium_path: str = "",
    ) -> Path:
        seen["html"] = html
        seen["expected_ms"] = expected_ms
        seen["chromium_path"] = chromium_path
        seen["workdir"] = workdir
        seen["record_stop"] = should_stop
        out = workdir / "recording.webm"
        out.write_bytes(b"WEBM")
        return out

    def fake_encode(src: Path, fmt: str, out: Path, *, should_stop) -> Path:
        encode_stops.append(should_stop)
        out.write_bytes(f"{fmt}:".encode() + src.read_bytes())
        return out

    def stop() -> bool:
        return False

    stages: list[str] = []

    monkeypatch.setattr(service, "record", fake_record)
    monkeypatch.setattr(service, "encode", fake_encode)
    # The tool check is its own test below; this one is the orchestration,
    # and must not depend on the machine having ffmpeg (CI's runners do not —
    # the `rest` shard was red on exactly this line).
    monkeypatch.setattr(service, "ensure_tools", lambda _o: None)

    result = render_chat_video(
        title="t",
        messages=_MESSAGES,
        options=VideoOptions(fmt=("gif", "mp4")),
        workdir=tmp_path,
        assets={"/chart.png": b"\x89PNG\r\n\x1a\n" + b"\0" * 8},
        should_stop=stop,
        on_stage=stages.append if with_stages else None,
        chromium_path="/usr/bin/chromium",
    )

    assert result == {"gif": b"gif:WEBM", "mp4": b"mp4:WEBM"}
    assert seen["chromium_path"] == "/usr/bin/chromium"  # the knob reaches the recorder
    # The worker's heartbeat names the stage from this: once before the
    # recording, once before the encodes (one for all formats).
    assert stages == (["rendering", "encoding"] if with_stages else [])
    # The cancel reaches both long phases: the same callable, unchanged.
    assert seen["record_stop"] is stop and encode_stops == [stop, stop]
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
    # Without this, a runner with no ffmpeg raises `RendererUnavailable` — a
    # RuntimeError too — before `record`, and the test is green over nothing.
    monkeypatch.setattr(service, "ensure_tools", lambda _o: None)

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
