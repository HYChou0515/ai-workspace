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
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import msgspec
from markdown_it import MarkdownIt

from .options import VideoOptions
from .timeline import PACING, StreamStep, Timeline

_md = (
    MarkdownIt("commonmark", {"html": False, "linkify": False})
    .enable("table")
    # A link or an image would put a URL in `href`/`src`; the page must not
    # reach for anything, so both stay as the text they were written as.
    .disable(["link", "autolink", "image"])
)


def render_markdown(text: str) -> str:
    return _md.render(text)


def _embed_json(value: Any) -> str:
    """JSON that is safe inside a ``<script>``: no ``<``, ``>`` or ``&`` survives
    unescaped, so no message can close the tag or open another."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def _steps_for_js(timeline: Timeline) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in timeline.steps:
        d = msgspec.to_builtins(step)
        assert isinstance(d, dict)
        if isinstance(step, StreamStep) and not step.reasoning:
            d["html"] = render_markdown(step.text)
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


def render_player_html(timeline: Timeline, options: VideoOptions) -> str:
    payload = {
        "title": timeline.title,
        "steps": _steps_for_js(timeline),
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
