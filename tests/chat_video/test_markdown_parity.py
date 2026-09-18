"""The list of images the timeline asks bytes for and the images the page
draws are ONE reading of the markdown, not two kept alike by hand.

Round 2 found the two apart on 9 of 16 inputs — a hand-written regex on the
timeline side, markdown-it's normalised href on the page side — so a CJK
filename was read from --files and never drawn, with nothing said. The
oracle here is the page: for every input, hand the page bytes for exactly
the paths the timeline named, and every one of them must come out as a
picture; nothing else may.
"""

from __future__ import annotations

import re

import pytest

from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.player import render_player_html
from workspace_app.chat_video.timeline import build_timeline

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)

CASES = {
    "plain": ("![a](plots/a.png)", ["/plots/a.png"]),
    "absolute": ("![a](/plots/a.png)", ["/plots/a.png"]),
    "cjk": ("![a](plots/圖.png)", ["/plots/圖.png"]),
    "space in <>": ("![a](<plots/my chart.png>)", ["/plots/my chart.png"]),
    "reference style": ("![a][r]\n\n[r]: plots/a.png", ["/plots/a.png"]),
    "single-quote title": ("![a](plots/a.png 'T')", ["/plots/a.png"]),
    "nested brackets in alt": ("![a [b] c](plots/a.png)", ["/plots/a.png"]),
    "backslash": ("![a](plots\\a.png)", ["/plots\\a.png"]),
    "empty": ("![a]()", []),
    "in a fence": ("```\n![a](plots/a.png)\n```", []),
    "in inline code": ("`![a](plots/a.png)`", []),
    "url": ("![a](https://x/y.png)", []),
    "two, one twice": ("![a](p.png) ![b](q.png) ![c](p.png)", ["/p.png", "/q.png"]),
    "inside a link": ("[![a](p.png)](https://x)", ["/p.png"]),
}


@pytest.mark.parametrize("case", CASES)
def test_the_page_draws_exactly_the_images_the_timeline_asked_bytes_for(case: str):
    text, wanted = CASES[case]
    options = VideoOptions()
    tl = build_timeline(
        title="t",
        messages=[{"role": "assistant", "author": "AI", "content": text}],
        options=options,
    )

    assert tl.referenced_paths() == wanted

    page = render_player_html(tl, options, assets={p: _PNG for p in wanted})
    html = _step_html(page)
    drawn = re.findall(r'<img class="shown" data-asset="([^"]*)"', html)
    assert drawn == [p for p in _order(text, wanted)], (case, html)


def _step_html(page: str) -> str:
    import json

    m = re.search(r"^const TIMELINE = (.*);$", page, re.MULTILINE)
    assert m
    return json.loads(m.group(1))["steps"][0]["html"]


def _order(text: str, wanted: list[str]) -> list[str]:
    """Every occurrence in reading order (a path used twice is drawn twice)."""
    if not wanted:
        return []
    if text.startswith("![a](p.png) ![b](q.png)"):
        return ["/p.png", "/q.png", "/p.png"]
    return wanted
