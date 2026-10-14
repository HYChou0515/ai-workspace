"""The helper's own Chrome (docs/plan-outside-helper.md §3.3, §3.4).

A Playwright persistent context over the person's INSTALLED Chrome
(`channel="chrome"`) — the only program on their PC that reaches the internet —
with a profile of its own: the person signs into the app there once, through the
deploy's normal login (D6), and the cookies stay in that profile, apart from
their everyday browser's (a refresh token is single-use; sharing one would sign
each other out).

The same context is the helper's line to the backend: `send` goes through the
context's `APIRequestContext`, which carries — and stores back — that profile's
cookies. Python speaks HTTP directly (D5); no page JavaScript is involved.

Headed by default: a visible browser is less often taken for a robot, and is
where the person solves a captcha (§3.4).

All Playwright calls must come from the thread that created the `Chrome` (the
sync API is not thread-safe); the window drives it from one worker thread.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errands import Errand, ErrandContext, ErrandFailed, FetchedPage

#: A backend request that takes longer than this is treated as unreachable.
REQUEST_TIMEOUT_MS = 30_000

#: The card's paste converter, built from the frontend source into this
#: package (web/vite.ai-workspace-helper.config.ts) — defines `__aiWorkspaceHelper`.
_CONVERTER = Path(__file__).with_name("page_to_markdown.js")


def _converter_source() -> str:
    try:
        return _CONVERTER.read_text(encoding="utf-8")
    except OSError as exc:
        raise ErrandFailed("the helper's page converter is missing — reinstall the helper") from exc


def _reason(exc: BaseException) -> str:
    """Why a backend request failed, without Playwright's "Call log:", which
    lists every request header — the login cookie among them — and would go
    on to the window and helper.log, the file IT is sent."""
    return str(exc).split("\nCall log:", 1)[0].strip()


class Chrome:
    def __init__(
        self,
        profile: Path,
        app_url: str,
        *,
        channel: str | None = "chrome",
        headless: bool = False,
        args: list[str] | None = None,
    ) -> None:
        from playwright.sync_api import sync_playwright

        self.app_url = app_url.rstrip("/")
        profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(profile), channel=channel, headless=headless, no_viewport=True, args=args
            )
        except BaseException:
            self._pw.stop()
            raise
        #: The window was closed under the helper (or by `close`): its
        #: Playwright objects are dead, and a new `Chrome` is needed.
        self.gone = False
        self._ctx.on("close", lambda _ctx: setattr(self, "gone", True))

    def send(
        self, method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str]
    ) -> tuple[int, Any]:
        """One backend request with the profile's cookies; see `backend.Send`.
        Redirects are NOT followed — a redirect is the gateway sending an
        expired login to sign in, and followed it would read as a success.

        AI Workspace's certificate is not verified (D20): this request is sent
        by Playwright's own Node, which neither trusts the company CA Windows
        and Chrome do nor fetches a missing intermediate — so a deploy Chrome
        reaches fine read 「連不上」 ("unable to verify the first certificate").
        Only these requests: the pages an errand opens still verify."""
        from playwright.sync_api import Error as PlaywrightError

        try:
            r = self._ctx.request.fetch(
                f"{self.app_url}{path}",
                method=method,
                headers={**headers, "Content-Type": "application/json"},
                data=json.dumps(body) if body is not None else None,
                max_redirects=0,
                fail_on_status_code=False,
                ignore_https_errors=True,
                timeout=REQUEST_TIMEOUT_MS,
            )
        except PlaywrightError as exc:
            # refused, DNS, timeout, TLS: the backend is not reachable from here
            raise OSError(_reason(exc)) from exc
        try:
            try:
                data = r.json() if r.body() else None
            except (ValueError, PlaywrightError):
                data = None
            return r.status, data
        finally:
            r.dispose()

    def version(self) -> str:
        """The Chrome being driven, e.g. `141.0.7390.55` (§3.7.7). A persistent
        context has no `Browser` object to ask, so the browser itself is."""
        page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        session = self._ctx.new_cdp_session(page)
        try:
            product = str(session.send("Browser.getVersion")["product"])
        finally:
            session.detach()
        return product.split("/", 1)[-1]

    def run(
        self,
        errand: Errand,
        args: str,
        note: Callable[[str], None],
        where: Callable[[str], None] | None = None,
    ) -> FetchedPage:
        """Run one errand in a fresh tab, closed afterwards whatever happens.
        `note` reaches the card; `where` the window."""
        page = self._ctx.new_page()
        try:
            ctx = ErrandContext(
                page=page,
                note=note,
                convert=lambda html: self._convert_in(page, html),
                sleep=lambda s: page.wait_for_timeout(s * 1000),
                where=where if where is not None else (lambda _site: None),
            )
            return errand(ctx, args)
        finally:
            page.close()

    def convert(self, html: str) -> str:
        """`html` as Markdown, by the card's own converter (D11), in a tab of
        its own."""
        page = self._ctx.new_page()
        try:
            return self._convert_in(page, html)
        finally:
            page.close()

    def _convert_in(self, page: Any, html: str) -> str:
        # On a blank page of the helper's own: the fetched page's CSP or
        # Trusted Types (Google enforces them) would refuse the converter's
        # HTML parsing, and nothing here needs the network.
        page.goto("about:blank")
        page.evaluate(_converter_source())
        return page.evaluate(
            "html => __aiWorkspaceHelper.aiWorkspaceHelper.htmlToMarkdown(html)", html
        )

    def open_login(self) -> None:
        """Bring the browser forward on the app, so the person signs in the way
        they always do (the deploy's SSO); the cookies land in this profile."""
        page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        page.goto(self.app_url)
        page.bring_to_front()

    def close(self) -> None:
        try:
            if not self.gone:
                self._ctx.close()
        finally:
            self._pw.stop()


__all__ = ["REQUEST_TIMEOUT_MS", "Chrome"]
