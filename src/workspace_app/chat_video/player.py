"""The timeline as one self-contained page that plays itself.

The page is the whole visual: a dark chat column, bottom-anchored like a real
thread, a composer, and a "camera" — the stage every element lives on, which
the player pushes in on the composer for a user turn and pulls back after.

Nothing is fetched. Fonts are the system stack, the script is inline, the
timeline is embedded. That is what lets the same page be previewed in a
browser (``--html``) and recorded on a worker pod with no network.

The transcript is DATA. Every string from it reaches the DOM through
``textContent``, except an assistant answer, which is rendered from markdown
here in Python with HTML disabled — so a hand-edited file cannot put a tag on
the page, and the embedded JSON escapes ``<``/``>``/``&`` so it cannot close
the script that carries it.

The files a tool showed (``[shown-files]``) and the ``![](path)`` images in an
answer are the two ways the chat puts a picture in front of the user. Their
bytes are not in the transcript: the caller hands them in as ``assets``
(path → bytes; the CLI reads them from ``--files``, a job prefetches them),
and an image becomes a ``data:`` URI inside the page — still nothing fetched.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from importlib import resources
from typing import Any

import msgspec
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from . import markdown as md
from .options import VideoOptions
from .timeline import PACING, StreamStep, Timeline, ToolStep, _cut

Assets = Mapping[str, bytes]
_NO_ASSETS: Assets = {}


def _image(
    self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """`![alt](src)` in an answer. The FE (`AgentEntryView`'s `img`) resolves a
    workspace path against the item's file route and draws it, and draws a
    URL too. This page's own rule is stricter, because it fetches nothing:
    a workspace path is drawn when the caller handed over its bytes (the
    picture then comes from the page's ASSETS table), a URL is never loaded,
    and a path nobody handed bytes for is its alt text — so the sentence
    around it still reads."""
    token = tokens[idx]
    alt = self.renderInlineAsText(token.children or [], options, env)
    path = md.image_path(token)
    inlined: Mapping[str, str] = env.get("inlined", {})
    if path is None or path not in inlined:
        return md.escape_html(alt)
    return f'<img class="shown" data-asset="{md.escape_html(path)}" alt="{md.escape_html(alt)}">'


md.add_image_rule(_image)


_NO_INLINED: Mapping[str, str] = {}


def render_markdown(text: str, *, inlined: Mapping[str, str] = _NO_INLINED) -> str:
    """``inlined`` maps a workspace path to the ``data:`` URI the page holds
    for it; an image whose path is not in it is drawn as its alt text."""
    return md.render(text, {"inlined": inlined})


NOT_HANDED, NOT_AN_IMAGE, OVER_BUDGET = (
    "no bytes were handed over",
    "not an image the page draws",
    "over the page's image budget",
)


class Verdict(msgspec.Struct, frozen=True):
    """One path's fate on the page: ``mime`` when it will be a picture,
    else ``why`` (one of the three sentences above)."""

    mime: str = ""
    why: str = ""


def decide_assets(
    paths: list[tuple[str, str]], assets: Assets, options: VideoOptions
) -> dict[str, Verdict]:
    """Per distinct path, in reading order: the page's own decision, which
    the CLI's note relays (deciding from "what was read" alone left an SVG
    chart read, undrawn and unmentioned).

    The verdict is a property of the PATH, from all of its references, so
    the first reference's verdict does not stick to it (round 4). The
    BYTES decide the mime whenever they can be sniffed (png / jpeg / gif /
    webp); a tool's ``image/*`` declaration is consulted only where
    sniffing cannot tell (SVG is text) — whether that declaration comes
    before or after an answer's ``![]()`` of the same path. Round 5: the
    other way round sent PNG bytes out as ``data:image/svg+xml``, which
    Chromium decodes by mime alone — a broken picture, and no note. A path
    is a picture when its bytes were handed over and are not empty, the
    mime is ``image/*`` without a ``,`` (a comma ends a ``data:`` URL's
    header), and they fit ``max_asset_bytes`` and what is left of the
    page's total budget (``max_assets_total_bytes``, raw bytes; base64 makes
    the page a third larger). First-fit: a file that does not fit the
    remainder is refused, and a later, smaller one that fits is still a
    picture."""
    declared: dict[str, str] = {}
    for path, mime in paths:
        if mime.startswith("image/"):
            declared.setdefault(path, mime)
    out: dict[str, Verdict] = {}
    budget = options.max_assets_total_bytes
    for path, _mime in paths:
        if path in out:
            continue
        data = assets.get(path)
        if data is None:
            out[path] = Verdict(why=NOT_HANDED)
            continue
        mime = _sniff_image(data) or declared.get(path, "")
        if not data or not mime.startswith("image/") or "," in mime:
            out[path] = Verdict(why=NOT_AN_IMAGE)
            continue
        if len(data) > options.max_asset_bytes or len(data) > budget:
            out[path] = Verdict(why=OVER_BUDGET)
            continue
        out[path] = Verdict(mime=mime)
        budget -= len(data)
    return out


def inline_assets(
    paths: list[tuple[str, str]], assets: Assets, options: VideoOptions
) -> dict[str, str]:
    """The page's ASSETS table: path → ``data:`` URI for every path
    ``decide_assets`` made a picture, each path ONCE however many times it
    is shown (twenty `show_file`s of one chart used to carry twenty copies,
    a 107 MB page). Whether a given reference then DRAWS it is the
    reference's business: an answer's ``![]()`` does; a tool's declaration
    does iff its own mime is ``image/*`` — the chat's ``isInlineImage`` — so
    one path shown as ``image/png`` and again as ``text/csv`` is a picture,
    then a card (`player.html`, ``shownFiles``)."""
    verdicts = decide_assets(paths, assets, options)
    return {
        path: f"data:{v.mime};base64,{base64.b64encode(assets[path]).decode('ascii')}"
        for path, v in verdicts.items()
        if v.mime
    }


_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image(data: bytes) -> str:
    """The handful of image types a browser draws, by signature, for an
    answer's ``![]()`` which declares no mime. `RIFF` alone is also WAV and
    AVI; WebP is `RIFF….WEBP`. Sniffing never yields SVG — an SVG is text —
    but a DECLARED ``image/svg+xml`` is inlined like the FE does, and inside
    an ``<img>`` an SVG runs no script and fetches nothing."""
    for magic, mime in _SIGNATURES:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _embed_json(value: Any) -> str:
    """JSON that is safe inside a ``<script>``: no ``<``, ``>`` or ``&`` survives
    unescaped, so no message can close the tag or open another."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _steps_for_js(
    timeline: Timeline, options: VideoOptions, inlined: Mapping[str, str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in timeline.steps:
        d = msgspec.to_builtins(step)
        assert isinstance(d, dict)
        if isinstance(step, StreamStep) and not step.reasoning:
            d["html"] = render_markdown(step.text, inlined=inlined)
        if isinstance(step, ToolStep):
            # The call's arguments, cut like its output: `write_file`'s
            # `content` is the whole file, and uncut it made a 4,000 px card.
            try:
                args = json.dumps(step.args, ensure_ascii=False, indent=2) if step.args else ""
            except RecursionError:  # a hand-edited value nested past the encoder
                args = "(arguments nested too deep to show)"
            d["args_text"] = _cut(args, options.tool_output_chars)
            del d["args"]
        out.append(d)
    return out


def _template() -> str:
    return resources.files(__package__).joinpath("player.html").read_text(encoding="utf-8")


def ui_scale(options: VideoOptions) -> float:
    """``options.scale``, or the automatic rule: the frame relative to
    1280×720, floored at 1 (a small or square frame keeps the base size)."""
    if options.scale > 0:
        return options.scale
    return max(1.0, min(options.width / 1280, options.height / 720))


def _css_number(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def render_player_html(
    timeline: Timeline, options: VideoOptions, *, assets: Assets = _NO_ASSETS
) -> str:
    inlined = inline_assets(timeline.wanted_files(), assets, options)
    payload = {
        "title": timeline.title,
        "steps": _steps_for_js(timeline, options, inlined),
        "time_scale": timeline.time_scale,
    }
    return (
        _template()
        .replace("/*FRAME_W*/", str(options.width))
        .replace("/*FRAME_H*/", str(options.height))
        .replace("/*CHAT_W*/", str(options.chat_width))
        .replace("/*UI_SCALE*/", _css_number(ui_scale(options)))
        .replace("/*TIMELINE*/", _embed_json(payload))
        .replace("/*OPTIONS*/", _embed_json(msgspec.to_builtins(options)))
        .replace("/*PACING*/", _embed_json(PACING))
        .replace("/*ASSETS*/", _embed_json(inlined))
    )
