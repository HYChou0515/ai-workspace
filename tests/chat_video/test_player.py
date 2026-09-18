"""The timeline becomes one self-contained page that plays itself (P2).

The page is what the recorder points a headless browser at, and what `--html`
hands a person to preview in their own browser. Two things it must never do:
fetch anything (a worker pod may be air-gapped, and a preview must look the
same as the recording), and let a hand-edited transcript inject markup into
itself (the messages are data, the page is code).
"""

from __future__ import annotations

import json
import re

from workspace_app.agent.shown_files import declare_shown_files
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.player import render_player_html
from workspace_app.chat_video.timeline import PACING, build_timeline

# The smallest valid PNG (1x1, transparent) — a real image, so the mime the
# transcript declares and the bytes agree.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


def _page(messages: list[dict], assets: dict[str, bytes] | None = None, **opts: object) -> str:
    options = VideoOptions(**opts)  # ty: ignore[invalid-argument-type]
    return render_player_html(
        build_timeline(title="t", messages=messages, options=options),
        options,
        assets=assets or {},
    )


def _embedded(page: str) -> dict:
    """The timeline the page's script will play, decoded the way the browser
    decodes it — so an assertion on a step sees what the player sees."""
    m = re.search(r"^const TIMELINE = (.*);$", page, re.MULTILINE)
    assert m, "the page lost its timeline"
    return json.loads(m.group(1))


def test_a_message_cannot_break_out_of_the_page():
    """`</script>` inside a message — a hand-edited file, or a transcript that
    quotes HTML — must stay text: in the embedded JSON and in the DOM."""
    hostile = '</script><script>document.title="pwned"</script>'
    page = _page([{"role": "user", "content": hostile, "author": "u"}])

    assert "pwned" in page  # the text is carried
    assert page.count("</script>") == 1  # …but only the page's own tag closes a script
    assert "<script>document.title" not in page
    # The rule, not just its consequence: the embedded JSON holds no angle
    # bracket at all. Escaping `>` alone happens to keep `</script>` from
    # closing the tag (a mutant that dropped the `<` escape stayed green on the
    # two lines above), and "happens to" is not a guard.
    src = re.search(r"^const TIMELINE = (.*);$", page, re.MULTILINE)
    assert src and "<" not in src.group(1) and ">" not in src.group(1)


def test_the_frame_size_reaches_the_page_as_variables():
    page = _page([], width=1920, height=1080, chat_width=900)

    assert "--frame-w: 1920px" in page
    assert "--frame-h: 1080px" in page
    assert "--chat-w: 900px" in page


def test_the_ui_scales_with_the_frame_unless_told_otherwise():
    """A 1080p frame is not a 720p page with more black around it: the whole
    UI grows with the frame (1.5× at 1080p, 3× at 4K), never below 1, and
    `scale` overrides the rule when someone wants a denser or larger look."""
    assert "--ui-scale: 1;" in _page([], width=1280, height=720)
    assert "--ui-scale: 1.5;" in _page([], width=1920, height=1080)
    assert "--ui-scale: 3;" in _page([], width=3840, height=2160)
    assert "--ui-scale: 1;" in _page([], width=1080, height=1080)  # square: never shrinks
    assert "--ui-scale: 2;" in _page([], width=1280, height=720, scale=2.0)


def test_an_answer_is_markdown_with_html_off_and_links_as_text():
    """Bold and code render; a raw tag in the answer is text; a markdown link
    or image stays the characters it was — no `href`, no `src`."""
    page = _page(
        [
            {
                "role": "assistant",
                "author": "AI",
                "content": "**bold** `code` <b>raw</b> [doc](https://e.com/x) ![i](https://e.com/i.png)",
            }
        ]
    )

    html = _embedded(page)["steps"][0]["html"]
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert (
        "<table>"
        in _embedded(_page([{"role": "assistant", "content": "|a|b|\n|-|-|\n|1|2|"}]))["steps"][0][
            "html"
        ]
    )
    assert "<b>raw</b>" not in html and "&lt;b&gt;raw&lt;/b&gt;" in html
    assert "href=" not in html and "src=" not in html
    assert "https://e.com/x" in html  # the URL survives as text
    # And on the page itself nothing of it is a live tag: it all rides inside
    # the escaped JSON, which is the point.
    assert "<strong>" not in page


def test_the_player_reads_the_same_pacing_table_the_estimate_used():
    """One table, two readers. The estimate is only honest if the script
    pauses for exactly the numbers it was computed from."""
    page = _page([])

    m = re.search(r"^const PACING = (\{.*?\});", page, re.MULTILINE)
    assert m and json.loads(m.group(1)) == PACING
    # …and the script uses the table, not its own literals, for every pause:
    # every `sleep(` goes through `t(`, and nothing inside `t(` is a number.
    # (A count of `PACING.` references let two hard-coded pauses through.)
    script = page[page.index("<script>") :]
    assert "s.ms" not in script
    sleeps = re.findall(r"sleep\(([^)]*)", script)
    assert sleeps and all(arg.startswith("t(") for arg in sleeps), sleeps
    assert not re.search(r"\bt\(\s*\d", script)
    assert "setTimeout" not in script.replace(
        "const sleep = ms => new Promise(r => setTimeout(r, ms));", ""
    )


def test_the_page_fetches_nothing():
    page = _page([{"role": "assistant", "content": "see https://example.com", "author": "AI"}])

    # The transcript may mention a URL; the page itself must not load one.
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', page)
    assert "@import" not in page


# ─── the files a tool put in front of the user ───────────────────────────────


def _shown(path: str, mime: str, size: int = 1) -> dict:
    return {
        "role": "tool",
        "tool_name": "show_file",
        "content": declare_shown_files("", [{"path": path, "mime": mime, "size": size}]),
    }


def test_an_image_the_renderer_was_handed_is_inlined_as_a_data_uri():
    """The page fetches nothing, so an image travels INSIDE it. The FE draws a
    declared image as a 260px thumbnail; so does the player."""
    page = _page([_shown("/plots/a.png", "image/png")], assets={"/plots/a.png": _PNG})

    step = _embedded(page)["steps"][0]
    assert step["files"][0]["src"].startswith("data:image/png;base64,")
    assert "iVBORw0KGgo" in step["files"][0]["src"]  # the PNG signature, base64


def test_a_file_with_no_bytes_or_no_image_mime_or_too_big_is_a_card_not_a_picture():
    """Nothing handed over (the CLI had no --files, the job could not read it),
    a non-image (a .md, a .csv), or an image over the cap: the file card with
    its name and size, never a broken <img> and never a 40 MB page."""
    big = _PNG + b"\0" * 100
    page = _page(
        [
            _shown("/missing.png", "image/png"),
            _shown("/notes.md", "text/markdown", size=88),
            _shown("/big.png", "image/png", size=len(big)),
        ],
        assets={"/notes.md": b"# hi", "/big.png": big},
        max_asset_bytes=len(big) - 1,
    )

    files = [s["files"][0] for s in _embedded(page)["steps"]]
    assert [f.get("src") for f in files] == [None, None, None]
    assert files[1]["size"] == 88 and files[1]["path"] == "/notes.md"


def test_an_image_in_an_answer_resolves_the_same_way_and_a_url_never_loads():
    """`![c](path)` in an answer is the FE's second path to a picture: a
    workspace path resolves (thumbnail), a URL is left as its alt text — the
    page fetches nothing — and a path nobody handed bytes for draws nothing,
    not a broken image."""
    md = "before ![chart](plots/a.png) and ![ext](https://x/y.png) and ![gone](/z.png) after"
    page = _page(
        [{"role": "assistant", "author": "AI", "content": md}], assets={"/plots/a.png": _PNG}
    )

    html = _embedded(page)["steps"][0]["html"]
    assert html.count("<img") == 1
    assert 'src="data:image/png;base64,' in html
    assert "https://x/y.png" not in html and "ext" in html  # the URL image is its alt text
    assert "/z.png" not in html and "gone" in html


def test_tool_args_are_cut_like_the_output_so_a_write_file_is_not_a_wall():
    """`write_file`'s `content` argument is the whole file. Uncut, one such
    call made a 4,000 px card whose header sat 3,500 px above the frame."""
    page = _page(
        [
            {
                "role": "tool",
                "tool_name": "write_file",
                "tool_args": {"path": "/a.py", "content": "x" * 5000},
            }
        ],
        tool_output_chars=80,
    )

    step = _embedded(page)["steps"][0]
    assert step["args_text"].endswith("…") and len(step["args_text"]) <= 81
    script = page[page.index("<script>") :]
    assert "JSON.stringify(s.args" not in script  # the page draws the cut text, not the dict


def test_a_stopped_reply_shows_its_label():
    page = _page([{"role": "assistant", "content": "x", "stopped_reason": "repetition"}])

    assert _embedded(page)["steps"][0]["stopped"] == "repetition"
    assert "s.stopped" in page[page.index("<script>") :]


def test_an_answer_image_whose_bytes_are_not_a_picture_draws_nothing():
    """`![]()` declares no mime, so the bytes are sniffed; an SVG (text that
    can carry script) or a stray file is not one of the types drawn."""
    md = "![s](/a.svg) ![t](/b.txt)"
    page = _page(
        [{"role": "assistant", "author": "AI", "content": md}],
        assets={"/a.svg": b"<svg onload=alert(1)></svg>", "/b.txt": b"hello"},
    )

    html = _embedded(page)["steps"][0]["html"]
    assert "<img" not in html and "onload" not in html
