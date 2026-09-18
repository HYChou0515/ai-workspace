"""ONE reading of an answer's markdown, shared by the timeline (which images
does this answer want bytes for?) and the page (which images does it draw?).

They used to be two: a regex on the timeline side, markdown-it's normalised
href on the page side. They disagreed on nine of sixteen inputs — a CJK
filename was read from ``--files`` and never drawn, silently. Now both sides
walk markdown-it's own token stream, and the path they agree on is the
image's ``src`` with markdown-it's percent-encoding undone.

HTML is off: a raw tag in an answer is text. Links are text too (the page
fetches nothing). Images are kept, and drawn only from the bytes the caller
handed over — see ``player._image``.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

_md = (
    MarkdownIt("commonmark", {"html": False, "linkify": False})
    .enable("table")
    # A link would put a URL in `href`; the page must not reach for anything,
    # so it stays as the text it was written as. `image` stays enabled and is
    # rendered by `player._image`, which draws only from `assets`.
    .disable(["link", "autolink"])
)

_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|//)", re.IGNORECASE)


def is_url(ref: str) -> bool:
    """The FE's `workspaceUrl` test: a scheme, a fragment or `//` is not a
    workspace path."""
    return bool(_URL.match(ref))


def abs_path(path: str) -> str:
    """The one spelling of a workspace path: absolute and normalised, so
    `plots/a.png`, `./plots/a.png` and `plots//a.png` are one key — one
    read, one table entry, one copy of the bytes."""
    return posixpath.normpath("/" + path.lstrip("/"))


def image_path(token: Token) -> str | None:
    """The workspace path an image token refers to, or ``None`` when it is
    not one (a URL, or nothing at all). markdown-it hands the normalised
    href (``圖`` → ``%E5%9C%96``, a space → ``%20``); undoing that is what
    makes the lookup key equal to the path a person wrote."""
    src = unquote(str(token.attrGet("src") or ""))
    if not src or is_url(src):
        return None
    return abs_path(src)


def image_paths(text: str) -> list[str]:
    """Every workspace image ``text`` refers to, in reading order, once each
    — through the same parse the page renders with, and skipping what the
    page flattens (an image inside an image's alt), so this list and the
    pictures the page draws come from one reading. The parity tests hold
    the two together; a new markdown construct is a new case there."""
    out: list[str] = []

    def walk(tokens: list[Token]) -> None:
        for t in tokens:
            if t.type == "image":
                path = image_path(t)
                if path is not None and path not in out:
                    out.append(path)
                # An image's children are its ALT, which the page flattens to
                # text — an image nested there is never drawn, so it is not
                # wanted either.
                continue
            if t.children:
                walk(t.children)

    walk(_md.parse(text))
    return out


def render(text: str, env: dict[str, Any]) -> str:
    return _md.render(text, env)


def add_image_rule(rule: Any) -> None:
    _md.add_render_rule("image", rule)


escape_html = _md.utils.escapeHtml
