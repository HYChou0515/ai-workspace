"""Pure helpers for #513 P6 — pull image URLs out of HTML/MD, gate by allowlist.

Kept side-effect-free (no network): the Ingestor uses ``extract_image_urls`` to
find an HTML/MD document's figures and ``filter_allowed_urls`` to drop anything
off the configured host allowlist BEFORE handing survivors to an
``IImageFetcher``. The SSRF fence lives here, not in the fetcher, so it's
exercised without touching a socket.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html.parser import HTMLParser
from urllib.parse import urlsplit

# Markdown image: ``![alt](url "optional title")`` — grab the URL (up to the
# first whitespace or the closing paren), tolerating an empty alt text.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")


class _ImgSrcCollector(HTMLParser):
    """Collect the ``src`` of every ``<img>`` in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        for name, value in attrs:
            if name == "src" and value:
                self.urls.append(value)


def extract_image_urls(text: str, *, content_format: str) -> list[str]:
    """Image URLs referenced by an HTML or Markdown document, in document order.

    ``content_format`` is ``"html"`` or ``"markdown"``.
    """
    if content_format == "markdown":
        return _MD_IMAGE.findall(text)
    collector = _ImgSrcCollector()
    collector.feed(text)
    return collector.urls


def filter_allowed_urls(urls: Iterable[str], *, allowed_hosts: Iterable[str]) -> list[str]:
    """Keep only ``http``/``https`` URLs whose host is on ``allowed_hosts``.

    The SSRF fence: relative URLs (no host), non-web schemes (``data:``,
    ``file:``, ``ftp:`` …) and any off-allowlist host are dropped. Host match is
    case-insensitive and port-insensitive.
    """
    allowed = {h.lower() for h in allowed_hosts}
    kept: list[str] = []
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        if (parts.hostname or "").lower() in allowed:
            kept.append(url)
    return kept
