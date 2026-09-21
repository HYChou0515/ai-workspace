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

    assets = load_assets(
        ws,
        ["/plots/a.png", "/missing.png", "/../secret.txt", "/nul\0byte.png", "/" + "x" * 5000],
        max_bytes=10,
    )

    assert assets == {"/plots/a.png": b"PNG-A"}


def test_a_symlink_loop_under_files_dir_is_skipped_not_a_traceback(tmp_path):
    """Python 3.12's `Path.resolve()` raises `RuntimeError` on a symlink
    loop — not an `OSError`. A hand-edited path into one is left out like any
    other path the OS rejects."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "loop").symlink_to(ws / "loop")

    assert load_assets(ws, ["/loop/a.png"], max_bytes=10) == {}


def test_the_note_names_every_path_the_page_will_not_draw_whatever_the_reason(
    tmp_path, capsys, monkeypatch
):
    """Two ways a wanted path is not drawn: nobody handed over bytes, or the
    page's budget ran out. (What the bytes ARE is the browser's business,
    as in the chat — the page sends them under the type the file route
    serves.) The note used to come from the first alone."""
    ws = tmp_path / "ws"
    (ws / "plots").mkdir(parents=True)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")
    (ws / "plots" / "a.png").write_bytes(png)
    (ws / "plots" / "b.png").write_bytes(png)
    (ws / "plots" / "chart.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    (ws / "plots" / "t.csv").write_bytes(b"a,b\n")
    (ws / "plots" / "empty.png").write_bytes(b"")
    src = _source(
        tmp_path,
        {
            "title": "t",
            "messages": [
                {
                    "role": "assistant",
                    "author": "AI",
                    "content": (
                        "![a](plots/a.png) ![b](plots/b.png) "
                        "![s](plots/chart.svg) ![m](plots/missing.png)"
                    ),
                },
                # A declared non-image is a card by the chat's own rule — it IS
                # drawn, as a card — so it is not in the note (round 4).
                {
                    "role": "tool",
                    "tool_name": "show_file",
                    "content": declare_shown_files(
                        "",
                        [
                            {"path": "/plots/t.csv", "mime": "text/csv", "size": 4},
                            {"path": "/plots/empty.png", "mime": "image/png", "size": 0},
                        ],
                    ),
                },
            ],
        },
    )
    monkeypatch.setattr("workspace_app.chat_video.cli.render_chat_video", lambda **k: {"gif": b"g"})

    code = main(
        [
            str(src),
            "-o",
            str(tmp_path / "d.gif"),
            "--files",
            str(ws),
            "--max-assets-total-bytes",
            str(len(png)),
        ]
    )

    err = capsys.readouterr().err
    assert code == 0
    assert "/plots/a.png" not in err  # drawn
    assert "/plots/b.png will not be drawn (over the page's image budget)" in err
    # image/svg+xml by its name; it comes after b.png and the budget (one
    # PNG) is spent, so it is the budget that refuses it.
    assert "/plots/chart.svg will not be drawn (over the page's image budget)" in err
    assert f"/plots/missing.png will not be drawn (not under {ws}, or too big)" in err
    assert "t.csv" not in err
    assert "empty.png" not in err  # image/png by its name: sent, and broken as in the chat


def test_a_dot_dot_above_the_workspace_is_not_folded_onto_a_file_that_exists(tmp_path, capsys):
    """`![](../secret.png)` when `DIR/secret.png` exists: the chat's URL for
    it resolves above `/files/` and draws nothing, so the video must not
    draw `DIR/secret.png` either. Round 4: `normpath` folded it inside and
    the picture appeared."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "secret.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")
    )
    src = _source(
        tmp_path,
        {
            "title": "t",
            "messages": [
                {"role": "assistant", "author": "AI", "content": "![s](../secret.png)"},
                {
                    "role": "tool",
                    "tool_name": "show_file",
                    "content": declare_shown_files(
                        "", [{"path": "/../secret.png", "mime": "image/png", "size": 33}]
                    ),
                },
            ],
        },
    )
    out = tmp_path / "p.html"

    assert main([str(src), "--html", str(out), "--files", str(ws)]) == 0

    assert "iVBORw0KGgo" not in out.read_text()
    assert (
        f"/../secret.png will not be drawn (not under {ws}, or too big)" in capsys.readouterr().err
    )


def test_an_export_nested_too_deep_is_one_sentence_and_exit_2(tmp_path, capsys):
    """Round 3 caught the DECLARATION nested 100k deep; the export file
    itself was the same traceback (`parse_chat_export` caught only
    `JSONDecodeError`). Same class, same sentence."""
    code = main([str(_source(tmp_path, "[" * 100_000)), "--html", str(tmp_path / "p.html")])

    assert code == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_a_files_dir_that_cannot_be_resolved_is_one_sentence_and_exit_2(tmp_path, capsys):
    """`--files` pointing at a symlink loop: `Path.resolve()` raised before
    the per-path guard existed to catch it. An operator's typo, one line."""
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    src = _source(tmp_path, {"title": "t", "messages": [{"role": "user", "content": "x"}]})

    code = main([str(src), "--html", str(tmp_path / "p.html"), "--files", str(loop)])

    assert code == 2
    assert "--files" in capsys.readouterr().err


def test_files_dir_does_not_read_what_the_page_would_not_inline(tmp_path):
    """The size check happens BEFORE the read: a 300 MB file named in a
    declaration used to be read whole and then become a card."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.png").write_bytes(b"x" * 100)

    assert load_assets(ws, ["/big.png"], max_bytes=99) == {}
    assert load_assets(ws, ["/big.png"], max_bytes=100) == {"/big.png": b"x" * 100}


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

    assert stop.value.code == 2 and "speed must be 0.1..100" in capsys.readouterr().err


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


def test_chromium_names_the_binary_to_record_with_and_is_playwrights_own_by_default(
    tmp_path, monkeypatch
):
    """`--chromium /usr/bin/chromium`: an image whose Debian mirror has
    Chromium but no reach to Playwright's CDN records with that binary —
    the same knob the worker reads as `chat_video.chromium_path`."""
    src = _source(tmp_path, {"title": "t", "messages": [{"role": "user", "content": "hi"}]})
    seen: list[dict] = []
    monkeypatch.setattr(
        "workspace_app.chat_video.cli.render_chat_video",
        lambda **k: (seen.append(k), {"gif": b"g"})[1],
    )

    main([str(src), "-o", str(tmp_path / "a.gif")])
    main([str(src), "-o", str(tmp_path / "b.gif"), "--chromium", "/usr/bin/chromium"])

    assert [k["chromium_path"] for k in seen] == ["", "/usr/bin/chromium"]


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
    assert "/gone.png will not be drawn (pass --files DIR)" in err
