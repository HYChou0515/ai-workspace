"""The errands this helper ships with (docs/plan-outside-helper.md D9, D10):
`search` and `open_url`, keyed by the job `kind` the backend hands out.

Both bring back ONE page, whole, as the person would have copied it: a search
brings back the results page itself — links and all — and the AI picks which
to open next (D10); nothing here guesses which result matters.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

from .errands import Errand, ErrandContext, ErrandFailed, FetchedPage

#: How long a page may take to start answering before the errand gives up.
PAGE_TIMEOUT_MS = 30_000
#: After the document is there, how long to let the rest (images, late
#: scripts that draw the results) arrive before reading it anyway.
SETTLE_MS = 5_000

#: What `encodeURIComponent` leaves unescaped besides letters and digits —
#: the card fills `{q}` with it (web/src/api/outsideLookup.ts).
_URI_COMPONENT_SAFE = "-_.!~*'()"

#: The page as the person would copy it: the body, with every link and image
#: made absolute against the page's own address (a copy from the browser
#: carries absolute links; the raw HTML of, say, Google's results does not,
#: and a relative link is one the AI cannot open). Only READS the live page —
#: the conversion runs elsewhere, so the page's CSP / Trusted Types never apply.
_PAGE_HTML = """() => {
  const body = document.body ? document.body.cloneNode(true) : document.createElement("body");
  for (const [selector, attr] of [["a[href]", "href"], ["img[src]", "src"]]) {
    for (const el of body.querySelectorAll(selector)) {
      try {
        el.setAttribute(attr, new URL(el.getAttribute(attr), document.baseURI).href);
      } catch (e) {}
    }
  }
  return body.innerHTML;
}"""

#: Signs that a page put a verification in the person's way rather than the
#: content: Google's "unusual traffic" page, and the common challenge widgets.
_VERIFICATION_FRAMES = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="challenges.cloudflare.com"]',
)


def search_url(template: str, query: str) -> str:
    """`template` with every `{q}` filled the way the card fills it."""
    return template.replace("{q}", quote(query, safe=_URI_COMPONENT_SAFE))


def pick_search_target(targets: list[dict[str, str]]) -> dict[str, str]:
    """The card's Google button, or — on a deploy that lists its own — the
    first destination it lists."""
    for target in targets:
        if target.get("name") == "Google":
            return target
    if not targets:
        raise ErrandFailed("this deploy lists no search destination")
    return targets[0]


def looks_like_a_verification(page: Any) -> bool:
    if "/sorry/" in urlsplit(page.url).path:
        return True
    return any(page.locator(selector).count() for selector in _VERIFICATION_FRAMES)


def fetch(ctx: ErrandContext, url: str) -> FetchedPage:
    page = ctx.page
    response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    if response is not None and response.status >= 400:
        raise ErrandFailed(f"the page answered {response.status}")
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    with contextlib.suppress(PlaywrightTimeout):  # a slow image is no reason to give up the page
        page.wait_for_load_state("load", timeout=SETTLE_MS)
    ctx.wait_for_person(lambda: looks_like_a_verification(page))
    title, where = page.title(), page.url
    html = page.evaluate(_PAGE_HTML)
    return FetchedPage(title=title, url=where, markdown=ctx.convert(html))


def build(targets: Callable[[], list[dict[str, str]]]) -> dict[str, Errand]:
    """The errands, given where the deploy's search destinations come from
    (`GET /api/lookup-targets`, asked per search so an operator's change
    reaches a helper that is already running)."""

    def search(ctx: ErrandContext, query: str) -> FetchedPage:
        return fetch(ctx, search_url(pick_search_target(targets())["url"], query))

    def open_url(ctx: ErrandContext, url: str) -> FetchedPage:
        if urlsplit(url).scheme not in ("http", "https"):
            raise ErrandFailed(f"not a web address: {url!r}")
        return fetch(ctx, url)

    return {"search": search, "open_url": open_url}


__all__ = ["build", "fetch", "looks_like_a_verification", "pick_search_target", "search_url"]
