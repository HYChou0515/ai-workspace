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
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from .options import VideoOptions
from .timeline import PACING, ShownFile, StreamStep, Timeline, ToolStep, _abs, _cut, _is_url

Assets = Mapping[str, bytes]

_md = (
    MarkdownIt("commonmark", {"html": False, "linkify": False})
    .enable("table")
    # A link would put a URL in `href`; the page must not reach for anything,
    # so it stays as the text it was written as. An image is kept: its src
    # goes through `_image` below, which draws it only from `assets`.
    .disable(["link", "autolink"])
)


def _image(
    self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """The FE's rule for `![alt](src)` in an answer (`AgentEntryView`'s `img`):
    a workspace path resolves to the picture, a URL is not fetched, and a path
    that does not resolve draws nothing. Here "resolves" means the caller
    handed us the bytes and they fit the cap; otherwise the alt text stands
    in, so the sentence around it still reads."""
    token = tokens[idx]
    src = str(token.attrGet("src") or "")
    alt = self.renderInlineAsText(token.children or [], options, env)
    assets: Assets = env.get("assets", {})
    cap: int = env.get("max_asset_bytes", 0)
    uri = None if _is_url(src) else _data_uri(_abs(src), "", assets, cap)
    if uri is None:
        return _md.utils.escapeHtml(alt)
    return f'<img class="shown" src="{uri}" alt="{_md.utils.escapeHtml(alt)}">'


_md.add_render_rule("image", _image)


_NO_ASSETS: Assets = {}


def render_markdown(text: str, *, assets: Assets = _NO_ASSETS, max_asset_bytes: int = 0) -> str:
    return _md.render(text, {"assets": assets, "max_asset_bytes": max_asset_bytes})


def _data_uri(path: str, mime: str, assets: Assets, cap: int) -> str | None:
    """The picture as a ``data:`` URI, or ``None`` when it is not one we will
    inline: no bytes handed over, not an image, or over the cap. ``mime``
    empty means "sniff from the bytes" — an answer's ``![]()`` declares none."""
    data = assets.get(path)
    if data is None or len(data) > cap:
        return None
    mime = mime or _sniff_image(data)
    if not mime.startswith("image/"):
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
)


def _sniff_image(data: bytes) -> str:
    """The handful of image types a browser draws, by signature — an SVG is
    text and can carry script, so it is deliberately not one of them."""
    for magic, mime in _SIGNATURES:
        if data.startswith(magic):
            return mime
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
    timeline: Timeline, options: VideoOptions, assets: Assets
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in timeline.steps:
        d = msgspec.to_builtins(step)
        assert isinstance(d, dict)
        if isinstance(step, StreamStep) and not step.reasoning:
            d["html"] = render_markdown(
                step.text, assets=assets, max_asset_bytes=options.max_asset_bytes
            )
        if isinstance(step, ToolStep):
            d["files"] = [_file_for_js(f, assets, options.max_asset_bytes) for f in step.files]
            # The call's arguments, cut like its output: `write_file`'s
            # `content` is the whole file, and uncut it made a 4,000 px card.
            args = json.dumps(step.args, ensure_ascii=False, indent=2) if step.args else ""
            d["args_text"] = _cut(args, options.tool_output_chars)
            del d["args"]
        out.append(d)
    return out


def _file_for_js(file: ShownFile, assets: Assets, cap: int) -> dict[str, Any]:
    """A declared file as the page draws it: the picture inline when it is one
    we were handed (`src`), else the card with its name and size."""
    d = msgspec.to_builtins(file)
    assert isinstance(d, dict)
    uri = _data_uri(file.path, file.mime, assets, cap)
    if uri is not None:
        d["src"] = uri
    return d


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
    payload = {
        "title": timeline.title,
        "steps": _steps_for_js(timeline, options, assets),
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
    )
