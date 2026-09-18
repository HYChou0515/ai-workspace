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

import pytest

from workspace_app.agent.shown_files import declare_shown_files
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.player import (
    NOT_AN_IMAGE,
    NOT_HANDED,
    Verdict,
    decide_assets,
    render_player_html,
)
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


def _assets(page: str) -> dict:
    m = re.search(r"^const ASSETS = (\{.*?\});", page, re.MULTILINE)
    assert m, "the page lost its asset table"
    return json.loads(m.group(1))


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

    assets = _assets(page)
    assert assets["/plots/a.png"].startswith("data:image/png;base64,")
    assert "iVBORw0KGgo" in assets["/plots/a.png"]  # the PNG signature, base64
    assert _embedded(page)["steps"][0]["files"][0]["path"] == "/plots/a.png"


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

    assert _assets(page) == {}  # nothing inlined: no bytes / not an image / too big
    files = [s["files"][0] for s in _embedded(page)["steps"]]
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
    assert 'data-asset="/plots/a.png"' in html and "data:" not in html  # drawn from ASSETS
    assert "/plots/a.png" in _assets(page)
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
    assert "args" not in step  # the full dict does not ride along in the page
    script = page[page.index("<script>") :]
    assert "JSON.stringify(s.args" not in script  # the page draws the cut text, not the dict


def test_a_stopped_reply_shows_its_label():
    page = _page([{"role": "assistant", "content": "x", "stopped_reason": "repetition"}])

    assert _embedded(page)["steps"][0]["stopped"] == "repetition"
    assert "s.stopped" in page[page.index("<script>") :]


def test_a_riff_that_is_not_webp_is_not_an_image():
    """`RIFF` starts WAV and AVI too; only `RIFF….WEBP` is a picture."""
    wav = b"RIFF" + bytes(4) + b"WAVE" + bytes(8)
    webp = b"RIFF" + bytes(4) + b"WEBP" + bytes(8)
    page = _page(
        [{"role": "assistant", "author": "AI", "content": "![w](/a.wav) ![p](/b.webp)"}],
        assets={"/a.wav": wav, "/b.webp": webp},
    )

    assert list(_assets(page)) == ["/b.webp"]


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


def test_a_file_shown_many_times_is_inlined_once():
    """20 `show_file` turns of one 4 MB chart made a 107 MB page, and one
    answer embedding it 50 times a 267 MB one: every reference carried its
    own copy. The page holds each file once, in a table the steps and the
    answers point into."""
    twenty = [_shown("/big.png", "image/png") for _ in range(20)]
    answer = {"role": "assistant", "author": "AI", "content": "![](big.png) " * 50}
    page = _page([*twenty, answer], assets={"/big.png": _PNG})

    assert page.count("iVBORw0KGgo") == 1
    assert list(_assets(page)) == ["/big.png"]


def test_the_total_of_inlined_bytes_is_capped_in_reading_order():
    """Twenty different 4 MB images would still be an 80 MB page. There is a
    budget for the page as a whole; files past it are cards, the earlier
    ones are pictures — the reader saw the first ones."""
    steps = [_shown(f"/{i}.png", "image/png") for i in range(4)]
    assets = {f"/{i}.png": _PNG + bytes(100) for i in range(4)}  # ~170 B each
    page = _page(steps, assets=assets, max_assets_total_bytes=len(_PNG + bytes(100)) * 2)

    assert list(_assets(page)) == ["/0.png", "/1.png"]


def test_the_budget_is_first_fit_a_big_file_is_skipped_and_a_later_small_one_kept():
    """ "Past the budget, cards" is first-fit: a file that does not fit what is
    left is a card, and a later, smaller one that does fit is still a
    picture. Said so, so nobody reads it as a prefix."""
    small, big = _PNG, _PNG + bytes(1000)
    page = _page(
        [
            _shown("/a.png", "image/png"),
            _shown("/big.png", "image/png"),
            _shown("/c.png", "image/png"),
        ],
        assets={"/a.png": small, "/big.png": big, "/c.png": small},
        max_assets_total_bytes=len(small) * 2 + 10,
    )

    assert list(_assets(page)) == ["/a.png", "/c.png"]


_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'


@pytest.mark.parametrize(
    ("messages", "mime"),
    [
        # An answer names the chart first (nothing declared, and SVG does not
        # sniff); a later `show_file` declares it `image/svg+xml`. Round 4:
        # the first reference's "not an image" verdict stuck to the path.
        (
            [
                {"role": "assistant", "author": "AI", "content": "![c](plots/chart.svg)"},
                _shown("/plots/chart.svg", "image/svg+xml"),
            ],
            "image/svg+xml",
        ),
        # …and the other order.
        (
            [
                _shown("/plots/chart.svg", "image/svg+xml"),
                {"role": "assistant", "author": "AI", "content": "![c](plots/chart.svg)"},
            ],
            "image/svg+xml",
        ),
        # Shown once as a placeholder, regenerated, shown again as a picture.
        (
            [
                _shown("/plots/chart.svg", "application/x-empty"),
                _shown("/plots/chart.svg", "image/svg+xml"),
            ],
            "image/svg+xml",
        ),
        # Two image declarations: the first image mime is the table's.
        (
            [_shown("/plots/chart.svg", "image/svg+xml"), _shown("/plots/chart.svg", "image/png")],
            "image/svg+xml",
        ),
    ],
    ids=["answer first", "declaration first", "placeholder then picture", "two image mimes"],
)
def test_one_path_many_references_a_declared_image_mime_wins_whatever_the_order(messages, mime):
    """The table is per PATH; its mime comes from every reference of that
    path, not from whichever came first."""
    page = _page(messages, assets={"/plots/chart.svg": _SVG})

    assert _assets(page)["/plots/chart.svg"].startswith(f"data:{mime};base64,")


# What a path's bytes are × what a tool declared it as → the table's verdict.
# The bytes decide whenever they can be sniffed (png / jpeg / gif / webp);
# the declaration is consulted only where sniffing cannot tell (SVG is text).
# Round 5: "declared wins" sent PNG bytes out as data:image/svg+xml, which
# Chromium decodes by mime alone → a broken picture, no note. The chat draws
# it (the file route serves by extension and Chromium sniffs rasters).
_BYTES_X_DECLARED = {
    "png bytes, nothing declared": (_PNG, "", Verdict(mime="image/png")),
    "png bytes, declared png": (_PNG, "image/png", Verdict(mime="image/png")),
    "png bytes, declared svg (the round-5 row)": (_PNG, "image/svg+xml", Verdict(mime="image/png")),
    "png bytes, declared jpeg": (_PNG, "image/jpeg", Verdict(mime="image/png")),
    "svg bytes, declared svg": (_SVG, "image/svg+xml", Verdict(mime="image/svg+xml")),
    "svg bytes, nothing declared": (_SVG, "", Verdict(why=NOT_AN_IMAGE)),
    "text bytes, nothing declared": (b"a,b\n", "", Verdict(why=NOT_AN_IMAGE)),
    # A declared image whose bytes cannot be sniffed is trusted, as the chat
    # trusts the extension: text served as image/png is broken in both.
    "text bytes, declared png": (b"hello", "image/png", Verdict(mime="image/png")),
    # A 0-byte file is not a picture anywhere; "never a broken <img>".
    "empty bytes, declared png": (b"", "image/png", Verdict(why=NOT_AN_IMAGE)),
    "empty bytes, nothing declared": (b"", "", Verdict(why=NOT_AN_IMAGE)),
    # A comma ends a data: URL's header; a declared mime carrying one would
    # break the picture silently. Parameters after `;` are fine.
    "svg bytes, declared mime with a comma": (_SVG, "image/svg+xml,x", Verdict(why=NOT_AN_IMAGE)),
    "svg bytes, declared mime with parameters": (
        _SVG,
        "image/svg+xml; charset=utf-8",
        Verdict(mime="image/svg+xml; charset=utf-8"),
    ),
    "no bytes, declared png": (None, "image/png", Verdict(why=NOT_HANDED)),
}


@pytest.mark.parametrize("case", _BYTES_X_DECLARED)
def test_the_bytes_decide_and_the_declaration_only_where_they_cannot(case):
    data, declared, expected = _BYTES_X_DECLARED[case]
    # Both orders of reference: an answer's ![]() (mime "") and, when there
    # is one, the declaration — the verdict is the path's, not the first
    # reference's.
    refs = [("/x", "")] + ([("/x", declared)] if declared else [])
    assets = {"/x": data} if data is not None else {}

    assert decide_assets(refs, assets, VideoOptions()) == {"/x": expected}
    assert decide_assets(refs[::-1], assets, VideoOptions()) == {"/x": expected}


def test_a_declared_non_image_is_a_card_even_when_the_path_is_in_the_table():
    """`isInlineImage` is the chat's rule: a declaration draws the picture
    iff ITS mime is `image/*`. The same path shown as `image/png` and again
    as `text/csv` is a picture then a card — the table alone does not decide.
    (The JS half — the card — is the integration test below; this pins that
    the page carries what the JS needs: the declared mime on every file.)"""
    page = _page(
        [_shown("/c.png", "image/png"), _shown("/c.png", "text/csv")],
        assets={"/c.png": _PNG},
    )

    steps = _embedded(page)["steps"]
    assert [f["mime"] for f in steps[0]["files"]] == ["image/png"]
    assert [f["mime"] for f in steps[1]["files"]] == ["text/csv"]
    assert list(_assets(page)) == ["/c.png"]
    assert "f.mime.startsWith('image/') && ASSETS[f.path]" in page


@pytest.mark.integration
def test_a_real_chromium_draws_a_declaration_by_its_own_mime(tmp_path):
    """The JS rule, in a browser: `image/png` then `text/csv` for one path
    whose bytes are a PNG → one thumbnail, one card; `application/x-empty`
    then `image/png` for another → one card, one thumbnail."""
    from playwright.sync_api import sync_playwright

    page_html = _page(
        [
            _shown("/c.png", "image/png"),
            _shown("/c.png", "text/csv"),
            _shown("/d.png", "application/x-empty"),
            _shown("/d.png", "image/png"),
        ],
        assets={"/c.png": _PNG, "/d.png": _PNG},
        speed=100, zoom_ms=0, type_ms=0, stream_ms=0, tool_pause_ms=0,
    )  # fmt: skip
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(page_html)
            page.wait_for_function("document.body.dataset.done === '1'", timeout=30_000)
            imgs = page.locator(".shown-files img").count()
            cards = page.locator(".shown-files .file-card").count()
        finally:
            browser.close()

    assert (imgs, cards) == (2, 2)


def test_tool_arguments_nested_too_deep_to_print_are_said_so_not_a_traceback():
    """`json.dumps(indent=2)` recurses; a hand-edited `tool_args` 1,500 deep
    loads fine (the decoder allows it) and then the page's render raised
    where the export file's own depth is caught (round 5, the third door of
    that class)."""
    deep: list = []
    for _ in range(1_500):
        deep = [deep]
    page = _page([{"role": "tool", "tool_name": "x", "tool_args": {"v": deep}, "content": "ok"}])

    assert _embedded(page)["steps"][0]["args_text"] == "(arguments nested too deep to show)"


def test_the_same_file_under_three_spellings_is_one_entry():
    """`show_file` declares `/plots/a.png`; the answer writes `./plots/a.png`
    and `plots//a.png`. One file, one table entry, one copy of the bytes —
    with the bytes keyed the way the CLI (and a job) keys them: by the
    timeline's own list. Keyed by hand, un-normalised spellings would be alt
    text and the copy count would look fine."""
    options = VideoOptions()
    tl = build_timeline(
        title="t",
        messages=[
            _shown("/plots/a.png", "image/png"),
            {
                "role": "assistant",
                "author": "AI",
                "content": "![x](./plots/a.png) ![y](plots//a.png)",
            },
        ],
        options=options,
    )

    page = render_player_html(tl, options, assets={p: _PNG for p in tl.referenced_paths()})

    assert list(_assets(page)) == ["/plots/a.png"]
    assert page.count("iVBORw0KGgo") == 1
    answer = _embedded(page)["steps"][1]["html"]
    assert answer.count('<img class="shown" data-asset="/plots/a.png"') == 2
