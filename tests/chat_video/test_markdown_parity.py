"""The list of images the timeline asks bytes for and the images the page
draws are ONE reading of the markdown, not two kept alike by hand.

Round 2 found the two apart on 9 of 16 inputs — a hand-written regex on the
timeline side, markdown-it's normalised href on the page side — so a CJK
filename was read from --files and never drawn, with nothing said.

Two guards. `EXPECTED` is a hand-written spec of what each input refers
to, compared as a list, so it catches the timeline naming too little (a
regex that misses `<…>` or reference-style images) AND too much (an image
nested in an alt) — but a hand-written row can itself be wrong. The
page-vs-timeline check needs no spec: hand the page bytes for exactly what
the timeline named, and the set drawn must equal the set named — a path
read from --files and never drawn is the round-2 defect, and it is caught
even when a wrong row in `EXPECTED` lists that path. The page can only ever
be handed what the timeline named, so the page check alone would let an
under-reading pass; that is why the spec list is not redundant.
"""

from __future__ import annotations

import json
import re

import pytest

from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.player import render_player_html
from workspace_app.chat_video.timeline import build_timeline

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)

EXPECTED = {
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
    # markdown-it parses an image inside an image's ALT; the page flattens
    # the alt to text, so the inner one is never drawn and must not be read.
    "image inside an image's alt": ("![![y](q.png)](p.png)", ["/p.png"]),
    "dot-slash and double slash": ("![a](./p.png) ![b](plots//q.png)", ["/p.png", "/plots/q.png"]),
    "dot-dot inside": ("![a](plots/../p.png)", ["/p.png"]),
    # Climbing above the root is named as written (the jail refuses it; the
    # chat draws nothing for it either), never folded to a file that exists.
    "dot-dot escaping": ("![a](../p.png) ![b](/x/../../q.png)", ["/../p.png", "/x/../../q.png"]),
    "in a table cell": ("|a|\n|-|\n|![x](p.png)|", ["/p.png"]),
    "in a blockquote": ("> ![x](p.png)", ["/p.png"]),
}


def _timeline(text: str, options: VideoOptions):
    return build_timeline(
        title="t",
        messages=[{"role": "assistant", "author": "AI", "content": text}],
        options=options,
    )


def _named(tl) -> list[str]:
    """Every path the timeline wants, once each, in order — `wanted_files`
    rather than `referenced_paths`, which drops a path that climbs above the
    root from the prefetch list while the page still names it."""
    out: list[str] = []
    for path, _mime in tl.wanted_files():
        if path not in out:
            out.append(path)
    return out


@pytest.mark.parametrize("case", EXPECTED)
def test_the_timeline_names_what_the_input_refers_to(case: str):
    text, wanted = EXPECTED[case]

    assert _named(_timeline(text, VideoOptions())) == wanted


@pytest.mark.parametrize("case", EXPECTED)
def test_the_page_draws_exactly_the_set_the_timeline_named(case: str):
    text, _ = EXPECTED[case]
    options = VideoOptions()
    tl = _timeline(text, options)
    # What a job (or the CLI) would hand over: the prefetch list, which
    # drops a path that climbs above the root — the page refuses that path
    # too, so nothing is drawn for it.
    named = tl.referenced_paths()

    page = render_player_html(tl, options, assets={p: _PNG for p in named})
    drawn = re.findall(r'<img class="shown" data-asset="([^"]*)"', _step_html(page))

    assert set(drawn) == set(named), (case, drawn, named)


def _step_html(page: str) -> str:
    m = re.search(r"^const TIMELINE = (.*);$", page, re.MULTILINE)
    assert m
    return json.loads(m.group(1))["steps"][0]["html"]
