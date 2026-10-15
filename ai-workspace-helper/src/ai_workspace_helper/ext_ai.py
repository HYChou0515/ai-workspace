"""Asking the company's web AI (docs/plan-ext-ai.md §4).

The company has an AI with only a web page, and nobody outside the company may
know what that page looks like (D7). So the page is driven by ONE file the
company writes, packed into the helper (`pack_ai_workspace_helper.py
--ext-ai-site`): `URL`, the page that opens on a blank conversation, and
`ask(page, question, h) -> str`, which asks it on the Playwright `page` and
returns the answer — its HTML, or plain text. The server never sends code
(D12): a helper packed without the file has no `ask_ext_ai` errand.

`h` (`Helpers`) is what the file waits with. Every wait goes through it for
two reasons: it tells the card again every `RENEW_S` — which renews the claim
on the job and counts as checking in; unrenewed for 120 s, the job reads as
waiting again and the card falls back to asking by hand while the question is
still being asked, and the person's helper on another PC may take it — and it
stops at
one limit for the whole errand (D11), saying what it was waiting for.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from .errands import (
    RENEW_S,
    VERIFY_TIMEOUT_S,
    Errand,
    ErrandContext,
    ErrandFailed,
    FetchedPage,
)

#: The whole errand's limit, signing in aside (D11): the same five minutes a
#: person gets for a verification.
EXT_AI_TIMEOUT_S = VERIFY_TIMEOUT_S

#: What the card shows while the helper waits for the person to sign in to the
#: external AI in its Chrome window.
LOGIN = "login"

#: Where a packed site file lives: `ai_workspace_helper/ext_ai_site.py`.
PACKED = "ai_workspace_helper.ext_ai_site"

#: The page's visible text — what "still changing" is judged by.
_TEXT = "() => document.body ? document.body.innerText : ''"

#: An answer is HTML when it IS an element — it opens with a tag and closes
#: with one (an element's outerHTML). Anything else is text as it is: text
#: can hold `#include <stdio.h>` or `List<String>`, which the converter would
#: take for tags (review round 1).
_HTML = re.compile(r"\A\s*<[A-Za-z][^>]*>.*</[A-Za-z][A-Za-z0-9-]*>\s*\Z", re.S)

#: What stands in for the company's address in a failure the card shows.
MASK = "(外部AI 的網址)"


def _masked(text: str, url: str) -> str:
    """`text` without the company's address (D12): a Playwright error names the
    page it was on, and an errand's failure goes to the server and the card.
    Every token holding the address's host goes, and the call log after it."""
    text = text.split("\nCall log:", 1)[0].strip()
    host = urlsplit(url).hostname or url.strip()
    if not host:
        return text
    return re.sub(r"\S*" + re.escape(host) + r"\S*", MASK, text)


class Helpers:
    """`h`: the waits a site file uses (D11)."""

    def __init__(self, ctx: ErrandContext, *, timeout_s: float = EXT_AI_TIMEOUT_S) -> None:
        self._ctx = ctx
        self._timeout_s = timeout_s
        self._deadline = ctx.clock() + timeout_s
        self._renew_at = ctx.clock() + RENEW_S

    def _tick(self, what: str) -> None:
        """One second of waiting: fail at the limit, renew the claim when due."""
        now = self._ctx.clock()
        if now >= self._deadline:
            raise ErrandFailed(f"等了 {int(self._timeout_s)} 秒還沒等到:{what}")
        if now >= self._renew_at:
            self._ctx.note("")
            self._renew_at = now + RENEW_S
        self._ctx.sleep(1.0)

    def wait_until(self, cond: Callable[[], bool], what: str) -> None:
        """Wait until `cond()` holds. `what` names it in the failure the card
        shows — 「等了 300 秒還沒等到:外部AI 開始回答」."""
        while not cond():
            self._tick(what)

    def wait_still(self, seconds: float = 3.0, page: Any = None) -> None:
        """Wait until the page's text has not changed for `seconds` — an answer
        written out a few words at a time is done when it stops growing."""
        page = page if page is not None else self._ctx.page
        last = page.evaluate(_TEXT)
        still_since = self._ctx.clock()
        while self._ctx.clock() - still_since < seconds:
            self._tick("外部AI 把回答寫完")
            now = page.evaluate(_TEXT)
            if now != last:
                last, still_since = now, self._ctx.clock()

    def wait_for_login(
        self, ready: Callable[[], bool], *, timeout_s: float = VERIFY_TIMEOUT_S
    ) -> None:
        """While `ready()` does not hold — the page is a sign-in, not the blank
        conversation — bring the helper's Chrome window forward and wait for the
        person to sign in (D4; like `ErrandContext.wait_for_person`). The time
        it takes does not count against the answer's limit."""
        if ready():
            return
        self._ctx.page.bring_to_front()
        self._ctx.note(LOGIN)
        start = self._ctx.clock()
        renew_at = start + RENEW_S
        while not ready():
            now = self._ctx.clock()
            if now >= start + timeout_s:
                raise ErrandFailed("沒有在小幫手的 Chrome 視窗裡登入外部AI")
            if now >= renew_at:
                self._ctx.note(LOGIN)
                renew_at = now + RENEW_S
            self._ctx.sleep(1.0)
        self._ctx.note("")
        # the limit is for asking and answering, not for signing in
        self._deadline += self._ctx.clock() - start
        self._renew_at = self._ctx.clock() + RENEW_S


def errand(site: Any) -> Errand:
    """The `ask_ext_ai` errand over `site`, the company's site file."""

    def ask_ext_ai(ctx: ErrandContext, question: str) -> FetchedPage:
        ctx.where("外部AI")
        try:
            answer = site.ask(ctx.page, question, Helpers(ctx))
        except Exception as exc:  # noqa: BLE001 — whatever failed, said without the address
            raise ErrandFailed(_masked(str(exc) or type(exc).__name__, site.URL)) from None
        if not isinstance(answer, str) or not answer.strip():
            raise ErrandFailed("外部AI 的網站檔沒有交回回答")
        # the converter leaves the page it runs in: last (ErrandContext.convert)
        markdown = ctx.convert(answer) if _HTML.match(answer) else answer
        if not markdown.strip():
            raise ErrandFailed("外部AI 的回答轉出來沒有文字")
        return FetchedPage(title="外部AI", url="", markdown=markdown)

    return ask_ext_ai


def load_site(path: Path | None = None) -> ModuleType | None:
    """The company's site file: the one at `path` (`try-ext-ai --site`), or the
    one packed in — None when the helper was packed without one. A file
    missing its `URL` or its `ask` is refused, saying which."""
    if path is None:
        try:
            module = importlib.import_module(PACKED)
        except ModuleNotFoundError as exc:
            if exc.name != PACKED:
                raise  # the file is there; something IT imports is not
            return None
    else:
        spec = importlib.util.spec_from_file_location("ext_ai_site", path)
        if spec is None or spec.loader is None:
            raise ValueError(f"not a Python file: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    url = getattr(module, "URL", None)
    if not isinstance(url, str) or not url.strip():
        raise ValueError("the external AI's site file needs URL — the page of a blank conversation")
    if not callable(getattr(module, "ask", None)):
        raise ValueError("the external AI's site file needs ask(page, question, h)")
    return module


def site_label(site: Any) -> str:
    """What the helper reports when it checks in (D9): the file's `NAME`, or
    `ext_ai_site` — "" with no file."""
    if site is None:
        return ""
    name = getattr(site, "NAME", "")
    return name.strip() if isinstance(name, str) and name.strip() else "ext_ai_site"


def try_main(
    argv: list[str],
    *,
    make_chrome: Callable[[], Any] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """`python -m ai_workspace_helper try-ext-ai --site <file> "<question>"`
    (P4): the company tries its site file on its own PC, against the real page,
    in a Chrome of its own (sign in once; it remembers). The question, what it
    waits on and the answer — as the card would get it — print here; nothing
    goes to AI Workspace's server. 0 answered, 1 failed, 2 the file is broken."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m ai_workspace_helper try-ext-ai",
        description="Ask the external AI once with a site file, and print the answer.",
    )
    parser.add_argument("--site", type=Path, required=True, help="the ext_ai_site.py to try")
    parser.add_argument("question")
    args = parser.parse_args(argv)
    try:
        site = load_site(args.site)
    except Exception as exc:  # noqa: BLE001 — whatever is wrong with the file, say it
        out(f"網站檔不能用:{exc}")
        return 2
    chrome = (make_chrome or _own_chrome)()

    def note(text: str) -> None:
        if text == LOGIN:
            out("請在剛開的 Chrome 視窗裡登入外部AI(登入一次,之後會記得)…")

    try:
        out(f"問:{args.question}")
        try:
            page = chrome.run(errand(site), args.question, note)
        except ErrandFailed as exc:
            out(f"沒問到:{exc}")
            return 1
        out("外部AI 的回答(卡片會收到的樣子):")
        out(page.markdown)
        return 0
    finally:
        chrome.close()


def _own_chrome() -> Any:  # pragma: no cover — opens the person's Chrome
    from .chrome import Chrome
    from .home import root

    # a profile of its own: the helper's window may be running on the other
    return Chrome(root() / "try-ext-ai", "")


__all__ = [
    "EXT_AI_TIMEOUT_S",
    "LOGIN",
    "MASK",
    "Helpers",
    "errand",
    "load_site",
    "site_label",
    "try_main",
]
