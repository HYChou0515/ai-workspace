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
#: The hops have ended once a page has loaded (or been waited on for
#: `LOAD_WAIT_MS`) and no new page has been asked for this long after.
QUIET_MS = 1_500
#: How long one page's `load` is waited for: a slow image is no reason to give
#: up the page, nor to wait for it for ever (a proxy that drops a tracker).
LOAD_WAIT_MS = 5_000
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


#: Whether the page shows anything at all — text, a link, an image, or a
#: frame / embedded document (a PDF in Chrome's viewer): asked of the live
#: page, before the conversion (which leaves this tab). A page of frames has
#: something there, but nothing the conversion can read: it fails at once
#: rather than after the whole limit.
_HAS_CONTENT = """() => {
  const body = document.body;
  return !!body && (
    body.innerText.trim() !== "" ||
    !!document.querySelector("img, a[href], iframe, frame, embed, object")
  );
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


def _where(url: str) -> str:
    """An address as said in a failure: without its query or fragment — on a
    sign-in hop the query can carry a one-time code."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _moving(page: Any) -> ErrandFailed:
    return ErrandFailed(
        f"這一頁一直在跳轉,{SETTLE_LIMIT_MS // 1000} 秒內沒有停下來:{_where(page.url)}"
    )


class Navigations:
    """The main frame's navigations, watched from before the page is opened
    to the end of the errand. A navigation is on its way from its request to
    its answer (a redirect's answer leads straight to the next request):
    reading the page meanwhile would wait for the new page for as long as its
    server takes (measured, Chromium — a form post and a script navigation
    alike), so nothing reads it then. Watched for the whole errand because a
    form posted AT load (the SAML POST binding) starts before any later look
    could have begun watching. `seen` counts navigations asked for: an address
    the page rewrites itself (history.replaceState, a #fragment) is no hop."""

    def __init__(self, page: Any) -> None:
        self._page = page
        self.on_its_way: set[Any] = set()
        self.seen = 0
        page.on("request", self._asked)
        page.on("response", self._answered)
        page.on("requestfailed", self._failed)

    def _asked(self, request: Any) -> None:
        from playwright.sync_api import Error as PlaywrightError

        with contextlib.suppress(PlaywrightError):  # a service worker's has no frame
            if request.is_navigation_request() and request.frame == self._page.main_frame:
                self.on_its_way.add(request)
                self.seen += 1

    def _answered(self, response: Any) -> None:
        # answered — the new page is there, even if its body streams on; a
        # redirect's answer is followed by the next request at once
        if not 300 <= response.status < 400:
            self.on_its_way.discard(response.request)

    def _failed(self, request: Any) -> None:
        self.on_its_way.discard(request)

    def close(self) -> None:
        self._page.remove_listener("request", self._asked)
        self._page.remove_listener("response", self._answered)
        self._page.remove_listener("requestfailed", self._failed)


def settle(
    page: Any, nav: Navigations, deadline: float, *, stop: Callable[[], bool] = lambda: False
) -> bool:
    """Wait until the page has stopped moving: nothing on its way, the page
    loaded (or waited on for `LOAD_WAIT_MS`), and no new page asked for in
    `QUIET_MS` after that. Every new page — a script that moves on, a form
    that posts itself, a redirect, a sign-in that comes back to the same
    address — restarts the wait and has its own load waited for. Each look
    first lets the browser's news in (`wait_for_timeout`), so a page is read
    only when nothing is on its way. True once it has stopped (or `stop()` —
    a verification page is a place to stop, not a hop); False when
    `deadline` (monotonic seconds) came while it was still moving."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    seen, loaded, moved = nav.seen, False, False
    quiet_since: float | None = None
    while (now := time.monotonic()) < deadline:
        page.wait_for_timeout(_LOOK_MS)  # the browser's news comes in here
        if nav.on_its_way or nav.seen != seen:
            # a new page on its way, or arrived: wait for it from the start
            seen, loaded, moved, quiet_since = nav.seen, False, True, None
            continue
        if not loaded:
            remaining_ms = (deadline - now) * 1000
            with contextlib.suppress(PlaywrightTimeout):
                page.wait_for_load_state("load", timeout=max(1.0, min(LOAD_WAIT_MS, remaining_ms)))
            loaded = True
            continue  # let in what the load set off (a form posted at load)
        if stop():
            return True
        if quiet_since is None:
            quiet_since = time.monotonic()  # counted from after the load
        elif time.monotonic() - quiet_since >= QUIET_MS / 1000:
            return True
    return not moved and not nav.on_its_way


def fetch(ctx: ErrandContext, url: str) -> FetchedPage:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = ctx.page
    nav = Navigations(page)  # before the page is opened: see `Navigations`
    try:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeout:
            raise ErrandFailed(
                f"這一頁 {PAGE_TIMEOUT_MS // 1000} 秒內沒有回應:{_where(url)}"
            ) from None
        if response is not None and response.status >= 400:
            raise ErrandFailed(f"the page answered {response.status}")
        verification = lambda: looks_like_a_verification(page)  # noqa: E731
        deadline = time.monotonic() + SETTLE_LIMIT_MS / 1000
        while True:
            if not settle(page, nav, deadline, stop=verification):
                raise _moving(page)
            if verification():
                ctx.wait_for_person(verification)
                # solved, the page moves on: a fresh wait for where that ends
                deadline = time.monotonic() + SETTLE_LIMIT_MS / 1000
                continue
            # something there, read on the live page before the conversion
            # (which leaves this tab); nothing yet → wait and look again
            try:
                if page.evaluate(_HAS_CONTENT) or time.monotonic() >= deadline:
                    title, where = page.title(), page.url
                    html = page.evaluate(_PAGE_HTML)
                    break
            except PlaywrightError:
                # it moved on while being read: settle again, if there is time
                if time.monotonic() >= deadline:
                    raise _moving(page) from None
    finally:
        nav.close()
    markdown = ctx.convert(html)
    if not markdown.strip():
        raise ErrandFailed(
            "這一頁轉出來沒有文字(可能還在跳轉、是 PDF 或內嵌框架、或要登入):" + _where(where)
        )
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
