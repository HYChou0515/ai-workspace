"""The errands this helper ships with (docs/plan-outside-helper.md D9, D10):
`search` and `open_url`, keyed by the job `kind` the backend hands out.

Both bring back ONE page, whole, as the person would have copied it: a search
brings back the results page itself — links and all — and the AI picks which
to open next (D10); nothing here guesses which result matters.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

from .errands import Errand, ErrandContext, ErrandFailed, FetchedPage

#: How long a page may take to start answering before the errand gives up.
PAGE_TIMEOUT_MS = 30_000
#: After the first page is there, how long it may keep moving — scripts, a
#: form that posts itself, server redirects: a company sign-in hops several
#: times before the page (plan D22) — and take to show its text.
SETTLE_LIMIT_MS = 30_000
#: The hops have ended once the page has loaded and its address has not
#: changed for this long.
QUIET_MS = 1_500
#: How often the page is looked at while it settles.
_LOOK_MS = 250

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


#: Whether the page shows anything a person would read or follow — asked of
#: the live page, before the conversion (which leaves this tab).
_HAS_CONTENT = """() => {
  const body = document.body;
  return !!body && (body.innerText.trim() !== "" || !!body.querySelector("img, a[href]"));
}"""


def looks_like_a_verification(page: Any) -> bool:
    from playwright.sync_api import Error as PlaywrightError

    if "/sorry/" in urlsplit(page.url).path:
        return True
    try:
        return any(page.locator(selector).count() for selector in _VERIFICATION_FRAMES)
    except PlaywrightError:
        # asked while the page moved on (a redirect hop): not a verification —
        # whatever it moves to is looked at again
        return False


def settle(page: Any, deadline: float, *, stop: Callable[[], bool] = lambda: False) -> None:
    """Wait until the page has stopped moving: loaded, and its address
    unchanged for `QUIET_MS` — a script that moves on, a form that posts
    itself, a redirect each restart the wait. A navigation still on its way
    (a slow answer to that form) needs no watching here: reading the page
    meanwhile blocks until the new one is there, then fails as "context
    destroyed" (measured, Chromium; `fetch` reads again). Returns at
    `deadline` (monotonic seconds) regardless, or as soon as `stop()` (a
    verification page is a place to stop, not a hop)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    address, quiet_since = page.url, time.monotonic()
    while (now := time.monotonic()) < deadline:
        # a slow image is no reason to give up the page
        with contextlib.suppress(PlaywrightTimeout):
            page.wait_for_load_state("load", timeout=max(1.0, (deadline - now) * 1000))
        if stop():
            return
        if page.url != address:
            address, quiet_since = page.url, time.monotonic()
        elif time.monotonic() - quiet_since >= QUIET_MS / 1000:
            return
        page.wait_for_timeout(_LOOK_MS)


def fetch(ctx: ErrandContext, url: str) -> FetchedPage:
    from playwright.sync_api import Error as PlaywrightError

    page = ctx.page
    response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    if response is not None and response.status >= 400:
        raise ErrandFailed(f"the page answered {response.status}")
    verification = lambda: looks_like_a_verification(page)  # noqa: E731
    deadline = time.monotonic() + SETTLE_LIMIT_MS / 1000
    settle(page, deadline, stop=verification)
    if verification():
        ctx.wait_for_person(verification)
        # solved, the page moves on: a fresh wait for where that ends
        deadline = time.monotonic() + SETTLE_LIMIT_MS / 1000
        settle(page, deadline)
    # some text where the hops end — read on the live page, before the
    # conversion (which leaves this tab)
    while True:
        try:
            if page.evaluate(_HAS_CONTENT) or time.monotonic() >= deadline:
                title, where = page.title(), page.url
                html = page.evaluate(_PAGE_HTML)
                break
        except PlaywrightError:
            # it moved on while being read: settle again below
            if time.monotonic() >= deadline:
                raise
        page.wait_for_timeout(500)
        settle(page, deadline)
    markdown = ctx.convert(html)
    if not markdown.strip():
        raise ErrandFailed(f"這一頁轉出來沒有文字(可能還在跳轉、是 PDF、或要登入):{where}")
    return FetchedPage(title=title, url=where, markdown=markdown)


def build(targets: Callable[[], list[dict[str, str]]]) -> dict[str, Errand]:
    """The errands, given where the deploy's search destinations come from
    (`GET /api/lookup-targets`, asked per search so an operator's change
    reaches a helper that is already running)."""

    def search(ctx: ErrandContext, query: str) -> FetchedPage:
        target = pick_search_target(targets())
        ctx.where(target.get("name", ""))
        return fetch(ctx, search_url(target["url"], query))

    def open_url(ctx: ErrandContext, url: str) -> FetchedPage:
        if urlsplit(url).scheme not in ("http", "https"):
            raise ErrandFailed(f"not a web address: {url!r}")
        return fetch(ctx, url)

    return {"search": search, "open_url": open_url}


__all__ = ["build", "fetch", "looks_like_a_verification", "pick_search_target", "search_url"]
