"""The errands the helper ships with (docs/plan-outside-helper.md D9, D10):
`search` opens the card's search address, `open_url` opens a page; both bring
the page back as the card's own converter would turn a paste into Markdown.

The browser here is a stand-in that records what was asked of it; the real
Chrome runs the same errands in test_errands_in_chrome.py (integration).
"""

from __future__ import annotations

import pytest
from ai_workspace_helper.builtin_errands import build
from ai_workspace_helper.errands import ErrandContext, ErrandFailed


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status
        self.headers: dict[str, str] = {}


class _Locator:
    def count(self) -> int:
        return 0


class _Page:
    """Records where it was sent; answers like a page that loaded."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.url = "about:blank"
        self.went: list[str] = []

    def goto(self, url: str, **_kw):
        self.went.append(url)
        self.url = url
        return _Response(self.status)

    def wait_for_load_state(self, *_a, **_kw) -> None:
        pass

    def title(self) -> str:
        return "the page"

    def evaluate(self, js: str):
        from ai_workspace_helper.builtin_errands import _CLOUDFLARE_CHALLENGE

        # an ordinary page, as a real one answers: no challenge on it (the
        # html answer for everything made every page a verification)
        if js == _CLOUDFLARE_CHALLENGE:
            return False
        return "<p>the page's html</p>"

    def locator(self, _selector: str) -> _Locator:
        return _Locator()

    def bring_to_front(self) -> None:
        pass

    # what `settle` asks of a page: it never moves on here
    main_frame = object()

    def on(self, _event: str, _handler) -> None:
        pass

    def remove_listener(self, _event: str, _handler) -> None:
        pass

    def wait_for_timeout(self, _ms: float) -> None:
        pass


@pytest.fixture(autouse=True)
def _settled_at_once(monkeypatch):
    """A page that never moves on has settled the moment it loaded (the
    real waits are tested in a real Chrome: test_errands_in_chrome.py)."""
    from ai_workspace_helper import builtin_errands

    monkeypatch.setattr(builtin_errands, "QUIET_MS", 0)


def _ctx(page):
    return ErrandContext(page=page, note=lambda _n: None, convert=lambda html: f"md:{html}")


def test_a_search_opens_the_target_it_was_handed():
    """docs/plan-lookup-target-choice.md §3.4: the backend fills the target the
    person chose; the helper opens that string — it picks and fills nothing."""
    page = _Page()

    got = build()["search"](_ctx(page), "https://g.example/search?q=pandas%202.0")

    assert page.went == ["https://g.example/search?q=pandas%202.0"]
    assert (got.title, got.url, got.markdown) == (
        "the page",
        "https://g.example/search?q=pandas%202.0",
        "md:<p>the page's html</p>",
    )


def test_a_target_this_helper_cannot_open_says_so():
    """T6: a target's url may be any URL, for a helper that knows what to do
    with it. This one opens web pages; anything else fails the job with why,
    rather than handing Chrome an address it will not open."""
    page = _Page()

    with pytest.raises(ErrandFailed, match="partsdb"):
        build()["search"](_ctx(page), "partsdb://lookup?pn=A123")
    assert page.went == []


def test_a_page_is_opened_as_given():
    page = _Page()

    got = build()["open_url"](_ctx(page), "https://pandas.example/whatsnew")

    assert page.went == ["https://pandas.example/whatsnew"]
    assert got.markdown == "md:<p>the page's html</p>"


# Only a web page: the helper's Chrome must not be pointed at file://,
# javascript: or chrome:// by whatever an AI wrote.
@pytest.mark.parametrize(
    "url", ["file:///C:/Users/me/secrets.txt", "javascript:alert(1)", "chrome://settings", ""]
)
def test_only_a_web_address_is_opened(url):
    page = _Page()

    with pytest.raises(ErrandFailed):
        build()["open_url"](_ctx(page), url)

    assert page.went == []


# "找不到頁面" (§3.2): a page that answers 404/500 is a failure the card shows,
# not a page of "Not Found" handed to the AI as if it were the content.
def test_a_page_that_answers_an_error_fails():
    with pytest.raises(ErrandFailed, match="404"):
        build()["open_url"](_ctx(_Page(status=404)), "https://x.example/gone")


# A page that starts moving just after it settled: reading it fails as
# "context destroyed" (the old page is gone), and the errand settles again
# and reads the new one rather than failing (plan D22).
def test_a_page_that_moves_on_while_being_read_is_read_again():
    from playwright.sync_api import Error as PlaywrightError

    class _Moving(_Page):
        reads = 0

        def evaluate(self, js: str):
            self.reads += 1
            if self.reads == 1:
                raise PlaywrightError("Execution context was destroyed")
            return super().evaluate(js)

    page = _Moving()

    got = build()["open_url"](_ctx(page), "https://pandas.example/whatsnew")

    assert got.markdown == "md:<p>the page's html</p>"


# Asked while the page moves on (the instant between a hop's answer and its
# new page), the verification check reads "not a verification" rather than
# failing the errand: whatever the page moves to is looked at again (D22).
def test_a_verification_check_that_meets_a_moving_page_reads_as_none():
    from ai_workspace_helper.builtin_errands import looks_like_a_verification
    from playwright.sync_api import Error as PlaywrightError

    class _Gone(_Locator):
        def count(self) -> int:
            raise PlaywrightError("Execution context was destroyed")

    class _Moving(_Page):
        def locator(self, _selector: str) -> _Locator:
            return _Gone()

    assert looks_like_a_verification(_Moving()) is False
