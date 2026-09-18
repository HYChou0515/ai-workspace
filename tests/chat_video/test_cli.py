"""`python -m workspace_app.chat_video` (P4): every `VideoOptions` field has a flag.

The parser, and `main` with the renderer stood in for; what the renderer does
is the service tests' business. A CLI that quietly ignored a flag would be
found here, not in someone's presentation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.agent.shown_files import declare_shown_files
from workspace_app.chat_video.cli import load_assets, main, parse_args
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.render import RecordingTimedOut, RendererUnavailable


def test_every_field_has_a_flag_and_the_output_name_picks_the_format():
    ns = parse_args(
        [
            "chat.json", "-o", "demo.mp4",
            "--width", "1920", "--height", "1080", "--chat-width", "900", "--scale", "1.2",
            "--zoom", "1.4", "--zoom-ms", "700",
            "--type-speed", "40", "--stream-speed", "15", "--tool-pause", "900",
            "--speed", "1.5", "--max-seconds", "45", "--tool-output-chars", "300",
            "--max-asset-bytes", "1000",
        ]
    )  # fmt: skip

    assert ns.options == VideoOptions(
        width=1920, height=1080, chat_width=900, scale=1.2,
        zoom=1.4, zoom_ms=700,
        type_ms=40, stream_ms=15, tool_pause_ms=900,
        speed=1.5, max_seconds=45, tool_output_chars=300, max_asset_bytes=1000,
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


# ─── --files: where the pictures come from ───────────────────────────────────


def test_files_dir_supplies_exactly_the_referenced_paths_and_never_outside_it(tmp_path):
    """The transcript names workspace paths; `--files DIR` is that workspace
    on disk. Only the referenced files are read (a workspace can be large),
    a missing one is simply absent (a card, not a crash), and a path that
    climbs out of DIR is refused — the JSON is hand-edited."""
    ws = tmp_path / "ws"
    (ws / "plots").mkdir(parents=True)
    (ws / "plots" / "a.png").write_bytes(b"PNG-A")
    (ws / "unrelated.bin").write_bytes(b"X")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"hunter2")

    assets = load_assets(ws, ["/plots/a.png", "/missing.png", "/../secret.txt"])

    assert assets == {"/plots/a.png": b"PNG-A"}


def test_html_mode_inlines_a_shown_image_from_files_dir(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "chart.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")
    )
    src = tmp_path / "c.chat.json"
    src.write_text(
        json.dumps(
            {
                "title": "T",
                "messages": [
                    {
                        "role": "tool",
                        "tool_name": "show_file",
                        "content": declare_shown_files(
                            "", [{"path": "/chart.png", "mime": "image/png", "size": 33}]
                        ),
                    }
                ],
            }
        )
    )
    out = tmp_path / "preview.html"

    assert main([str(src), "--html", str(out), "--files", str(ws)]) == 0

    assert "data:image/png;base64,iVBORw0KGgo" in out.read_text()


def _source(tmp_path, body: object) -> Path:
    src = tmp_path / "c.chat.json"
    src.write_text(body if isinstance(body, str) else json.dumps(body))
    return src


@pytest.mark.parametrize(
    ("body", "word"),
    [
        ('{"title": "t", "messages": [}', "invalid JSON"),
        ([1, 2], "object at the top level"),
        ({"messages": [{"role": "user", "content": "x"}]}, '"title"'),
        ({"title": "t", "messages": [{"role": "user", "content": "x"}, "oops"]}, "message 2"),
        ({"title": "t", "messages": [{"role": "user", "content": ["a", "b"]}]}, "message 1"),
    ],
)
def test_a_hand_edited_file_that_is_wrong_gets_one_sentence_naming_what(
    tmp_path, capsys, body, word
):
    """The same validator the KB upload path runs on these files
    (`kb.chat_export.parse_chat_export`), so the sentence is the one an
    operator already knows — and never a traceback."""
    code = main([str(_source(tmp_path, body)), "--html", str(tmp_path / "p.html")])

    assert code == 2
    assert word in capsys.readouterr().err


def test_an_invalid_option_is_one_sentence_too(tmp_path, capsys):
    src = _source(tmp_path, {"title": "t", "messages": [{"role": "user", "content": "x"}]})

    with pytest.raises(SystemExit) as stop:  # argparse's own refusal: a line + exit 2
        main([str(src), "--html", str(tmp_path / "p.html"), "--speed", "0"])

    assert stop.value.code == 2 and "speed must be positive" in capsys.readouterr().err


def test_recording_writes_every_format_and_says_how_long_it_will_play(
    tmp_path, capsys, monkeypatch
):
    src = _source(tmp_path, {"title": "t", "messages": [{"role": "user", "content": "hi"}]})
    monkeypatch.setattr(
        "workspace_app.chat_video.cli.render_chat_video",
        lambda **k: {fmt: f"{fmt}-bytes".encode() for fmt in k["options"].fmt},
    )

    code = main([str(src), "-o", str(tmp_path / "demo.mp4"), "--fmt", "gif"])

    assert code == 0
    assert (tmp_path / "demo.mp4").read_bytes() == b"mp4-bytes"
    assert (tmp_path / "demo.gif").read_bytes() == b"gif-bytes"
    out = capsys.readouterr().out
    assert "will play" in out and "s" in out and "wrote" in out


@pytest.mark.parametrize(
    ("exc", "code", "word"),
    [
        (RendererUnavailable("install the chat-video extra"), 3, "chat-video"),
        (RecordingTimedOut("the page did not finish within 45s"), 4, "did not finish"),
        (RuntimeError("ffmpeg failed encoding gif: boom"), 4, "ffmpeg"),
    ],
)
def test_a_renderer_failure_is_its_sentence_and_an_exit_code(
    tmp_path, capsys, monkeypatch, exc, code, word
):
    src = _source(tmp_path, {"title": "t", "messages": [{"role": "user", "content": "hi"}]})

    def fail(**_k):
        raise exc

    monkeypatch.setattr("workspace_app.chat_video.cli.render_chat_video", fail)

    assert main([str(src), "-o", str(tmp_path / "demo.gif")]) == code
    assert word in capsys.readouterr().err


def test_an_output_name_without_a_known_extension_is_refused_by_name(tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        parse_args(["chat.json", "-o", "demo"])

    assert stop.value.code == 2 and "--out must end in one of" in capsys.readouterr().err


def test_a_squeezed_transcript_is_told_so_and_a_missing_file_is_noted(
    tmp_path, capsys, monkeypatch
):
    """Two things the person needs to know before the recording starts: the
    video will be shorter than the transcript asked for, and a file the
    transcript shows will be a card because nobody handed over its bytes."""
    src = _source(
        tmp_path,
        {
            "title": "t",
            "messages": [
                {"role": "user", "content": "x" * 400},
                {"role": "assistant", "content": "y" * 400 + " ![c](/gone.png)"},
            ],
        },
    )
    monkeypatch.setattr("workspace_app.chat_video.cli.render_chat_video", lambda **k: {"gif": b"g"})

    code = main([str(src), "-o", str(tmp_path / "d.gif"), "--max-seconds", "5"])

    out, err = capsys.readouterr()
    assert code == 0
    assert "squeezed from" in out and "will play 5" in out
    assert "/gone.png shown as a card (pass --files DIR)" in err
