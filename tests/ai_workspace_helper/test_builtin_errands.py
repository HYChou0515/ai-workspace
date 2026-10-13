"""The errands the helper ships with (docs/plan-outside-helper.md D9, D10):
`search` opens the card's search address, `open_url` opens a page; both bring
the page back as the card's own converter would turn a paste into Markdown.

The browser here is a stand-in that records what was asked of it; the real
Chrome runs the same errands in test_errands_in_chrome.py (integration).
"""

from __future__ import annotations

import pytest
from ai_workspace_helper.builtin_errands import build, pick_search_target, search_url
from ai_workspace_helper.errands import ErrandContext, ErrandFailed


# The helper must open exactly the address the card's own Google button would:
# every `{q}`, filled like the card's `encodeURIComponent` (outsideLookup.ts).
def test_the_search_address_is_the_cards_own():
    template = "https://www.google.com/search?q={q}&hl=zh-TW&oq={q}"

    assert search_url(template, "pandas 2.0 新功能") == (
        "https://www.google.com/search?q=pandas%202.0%20%E6%96%B0%E5%8A%9F%E8%83%BD"
        "&hl=zh-TW&oq=pandas%202.0%20%E6%96%B0%E5%8A%9F%E8%83%BD"
    )
    # what encodeURIComponent escapes, and what it leaves alone
    assert search_url("{q}", "a&b=c/d?#+%") == "a%26b%3Dc%2Fd%3F%23%2B%25"
    assert search_url("{q}", "it's (ok)*!~-_.") == "it's%20(ok)*!~-_."


def test_a_search_goes_to_google_or_else_the_first_destination():
    google = {"name": "Google", "url": "https://www.google.com/search?q={q}"}
    corp = {"name": "Corp search", "url": "https://search.corp/?q={q}"}

    assert pick_search_target([corp, google]) == google
    assert pick_search_target([corp]) == corp
    with pytest.raises(ErrandFailed):
        pick_search_target([])


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status


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

    def evaluate(self, _js: str):
        return "<p>the page's html</p>"

    def locator(self, _selector: str) -> _Locator:
        return _Locator()

    def bring_to_front(self) -> None:
        pass


def _ctx(page):
    return ErrandContext(page=page, note=lambda _n: None, convert=lambda html: f"md:{html}")


def test_a_search_brings_back_the_results_page_itself():
    page = _Page()
    errands = build(lambda: [{"name": "Google", "url": "https://g.example/search?q={q}"}])

    got = errands["search"](_ctx(page), "pandas 2.0")

    assert page.went == ["https://g.example/search?q=pandas%202.0"]
    assert (got.title, got.url, got.markdown) == (
        "the page",
        "https://g.example/search?q=pandas%202.0",
        "md:<p>the page's html</p>",
    )


def test_a_page_is_opened_as_given():
    page = _Page()

    got = build(lambda: [])["open_url"](_ctx(page), "https://pandas.example/whatsnew")

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
        build(lambda: [])["open_url"](_ctx(page), url)

    assert page.went == []


# "找不到頁面" (§3.2): a page that answers 404/500 is a failure the card shows,
# not a page of "Not Found" handed to the AI as if it were the content.
def test_a_page_that_answers_an_error_fails():
    with pytest.raises(ErrandFailed, match="404"):
        build(lambda: [])["open_url"](_ctx(_Page(status=404)), "https://x.example/gone")
