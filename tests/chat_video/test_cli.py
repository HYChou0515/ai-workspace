"""`python -m workspace_app.chat_video` (P4): flags are `VideoOptions`, one for one.

The parser is the only thing under test here; what it feeds is the same
`render_chat_video` the service tests cover. A CLI that quietly ignored a
flag would be found here, not in someone's presentation.
"""

from __future__ import annotations

import json

from workspace_app.chat_video.cli import main, parse_args
from workspace_app.chat_video.options import VideoOptions


def test_every_option_has_a_flag_and_the_output_name_picks_the_format():
    ns = parse_args(
        [
            "chat.json", "-o", "demo.mp4",
            "--width", "1920", "--height", "1080", "--chat-width", "900", "--scale", "1.2",
            "--zoom", "1.4", "--zoom-ms", "700",
            "--type-speed", "40", "--stream-speed", "15", "--tool-pause", "900",
            "--speed", "1.5", "--max-seconds", "45", "--tool-output-chars", "300",
        ]
    )  # fmt: skip

    assert ns.options == VideoOptions(
        width=1920, height=1080, chat_width=900, scale=1.2,
        zoom=1.4, zoom_ms=700,
        type_ms=40, stream_ms=15, tool_pause_ms=900,
        speed=1.5, max_seconds=45, tool_output_chars=300,
        fmt=("mp4",),
    )  # fmt: skip


def test_defaults_are_the_structs_defaults_and_gif_is_the_default_output():
    ns = parse_args(["chat.json"])

    assert ns.options == VideoOptions()
    assert ns.out.name == "chat.gif"


def test_extra_formats_ride_along_with_the_named_output():
    ns = parse_args(["chat.json", "-o", "x.gif", "--fmt", "mp4", "--fmt", "webm"])

    assert ns.options.fmt == ("gif", "mp4", "webm")


def test_html_writes_the_player_and_records_nothing(tmp_path, monkeypatch):
    src = tmp_path / "c.chat.json"
    src.write_text(json.dumps({"title": "T", "messages": [{"role": "user", "content": "hi"}]}))
    out = tmp_path / "preview.html"
    called = []
    monkeypatch.setattr(
        "workspace_app.chat_video.cli.render_chat_video", lambda **k: called.append(k)
    )

    code = main([str(src), "--html", str(out)])

    assert code == 0
    assert "const TIMELINE" in out.read_text() and "hi" in out.read_text()
    assert called == []
