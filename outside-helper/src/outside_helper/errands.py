"""Errands: what the helper can do in the browser (docs/plan-outside-helper.md D9).

An errand is a function `(ctx, args) -> FetchedPage`, registered under the job
`kind` the backend hands out. `ctx` carries the Playwright page to work in and
what every errand shares — waiting for the person when a verification stands in
the way. A new errand is a new function; the backend and the card need no
change.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: How long the helper waits for the person to complete a verification in its
#: Chrome window before giving the job up (the card then offers 「重查」).
VERIFY_TIMEOUT_S = 300.0

#: What the card shows while the helper waits on the person.
CAPTCHA = "captcha"


class ErrandFailed(Exception):
    """The errand could not bring the page back; the message goes to the card."""


@dataclass(frozen=True)
class FetchedPage:
    """One page as the person would have copied it: its title, where it was,
    and the page as Markdown (the card's own paste converter, D11)."""

    title: str
    url: str
    markdown: str


def _no_converter(_html: str) -> str:
    raise ErrandFailed("no page converter in this context")


@dataclass
class ErrandContext:
    #: The Playwright `Page` the errand works in (a fresh tab, closed after).
    page: Any
    #: Tell the card what the helper waits on (`captcha`), or "" once it doesn't.
    note: Callable[[str], None]
    #: A page's HTML as Markdown — the card's own paste converter (D11), run in
    #: the helper's Chrome. It may navigate `page` away: call it last.
    convert: Callable[[str], str] = field(default=_no_converter)
    sleep: Callable[[float], None] = field(default=time.sleep)
    clock: Callable[[], float] = field(default=time.monotonic)

    def wait_for_person(
        self, blocked: Callable[[], bool], *, timeout_s: float = VERIFY_TIMEOUT_S
    ) -> None:
        """While `blocked()` (a captcha, a "are you a robot" page), bring the
        helper's window forward and wait for the person to clear it — the
        helper runs headed for exactly this (§3.4). Fails the errand if nobody
        does within `timeout_s`."""
        if not blocked():
            return
        self.page.bring_to_front()
        self.note(CAPTCHA)
        deadline = self.clock() + timeout_s
        while blocked():
            if self.clock() >= deadline:
                raise ErrandFailed(
                    "the verification in the outside helper's Chrome window was not completed"
                )
            self.sleep(1.0)
        self.note("")


Errand = Callable[[ErrandContext, str], FetchedPage]

__all__ = ["CAPTCHA", "Errand", "ErrandContext", "ErrandFailed", "FetchedPage", "VERIFY_TIMEOUT_S"]
