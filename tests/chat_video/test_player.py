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

from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.player import render_player_html
from workspace_app.chat_video.timeline import PACING, build_timeline


def _page(messages: list[dict], **opts: object) -> str:
    options = VideoOptions(**opts)  # ty: ignore[invalid-argument-type]
    return render_player_html(
        build_timeline(title="t", messages=messages, options=options), options
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
    # …and the script uses the table, not its own literals, for every pause.
    script = page[page.index("<script>") :]
    assert "s.ms" not in script
    assert script.count("PACING.") >= len(PACING)


def test_the_page_fetches_nothing():
    page = _page([{"role": "assistant", "content": "see https://example.com", "author": "AI"}])

    # The transcript may mention a URL; the page itself must not load one.
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', page)
    assert "@import" not in page
