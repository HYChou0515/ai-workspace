"""The helper's Chrome (docs/plan-outside-helper.md §3.3, P5), driven for real
against a local fake site: the login the person makes in the helper's browser is
what the helper's backend requests carry (D6), and it survives a restart.

Integration: a real Chromium (Playwright's bundled build here — on the person's
PC it is their installed Chrome, `channel="chrome"`). CI does not install it.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from outside_helper.chrome import Chrome
from outside_helper.errands import FetchedPage

pytestmark = pytest.mark.integration


class _Site(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        if self.path == "/login":
            # the deploy's login, as the gateway would finish it: a lasting cookie
            self._send(
                200,
                "text/html",
                b"<title>signed in</title>ok",
                ("Set-Cookie", "sid=abc; Max-Age=86400; Path=/"),
            )
        elif self.path == "/api/me":
            body = json.dumps({"cookie": self.headers.get("Cookie", "")}).encode()
            self._send(200, "application/json", body)
        elif self.path == "/api/expired":
            self._send(302, "text/html", b"", ("Location", "/login"))
        else:
            self._send(200, "text/html", b"<title>a page</title><p>hello</p>")

    def _send(self, status: int, ctype: str, body: bytes, *extra: tuple[str, str]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _chrome(tmp_path, url):
    # Full Chromium, as the person's Chrome is: Playwright's default headless
    # build is the stripped "headless shell", which keeps no cookies across a
    # restart — measured: the login test below fails on it.
    return Chrome(tmp_path / "profile", url, channel="chromium", headless=True)


def _visit(chrome, url):
    def go(ctx, _args):
        ctx.page.goto(url)
        return FetchedPage(title=ctx.page.title(), url=ctx.page.url, markdown="")

    return chrome.run(go, "", lambda _note: None)


def test_the_login_made_in_the_helpers_browser_is_what_its_requests_carry(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    try:
        _visit(chrome, f"{site}/login")

        status, data = chrome.send("GET", "/api/me", None, {})
    finally:
        chrome.close()

    assert status == 200
    assert "sid=abc" in data["cookie"]


# Login lasts days (§1): the helper's profile keeps it across restarts — and
# across updates, since the profile lives outside the version folders.
def test_the_login_survives_closing_the_helper(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    _visit(chrome, f"{site}/login")
    chrome.close()

    again = _chrome(tmp_path, site)
    try:
        _status, data = again.send("GET", "/api/me", None, {})
    finally:
        again.close()

    assert "sid=abc" in data["cookie"]


def test_a_redirect_is_handed_back_not_followed(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    try:
        status, _data = chrome.send("GET", "/api/expired", None, {})
    finally:
        chrome.close()

    assert status == 302


def test_a_backend_that_is_not_there_is_an_os_error(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed once the block ends: nobody listens
    chrome = _chrome(tmp_path, f"http://127.0.0.1:{port}")
    try:
        with pytest.raises(OSError):
            chrome.send("GET", "/api/me", None, {})
    finally:
        chrome.close()


def test_each_errand_gets_its_own_tab_and_it_is_closed_after(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    pages = []

    def errand(ctx, _args):
        pages.append(ctx.page)
        ctx.page.goto(f"{site}/x")
        return FetchedPage(title=ctx.page.title(), url=ctx.page.url, markdown="")

    try:
        got = chrome.run(errand, "", lambda _note: None)
        chrome.run(errand, "", lambda _note: None)
        # while Chrome is still open: closing it would close every tab anyway
        closed = [p.is_closed() for p in pages]
    finally:
        chrome.close()

    assert got.title == "a page"
    assert pages[0] is not pages[1]
    assert closed == [True, True]


# The 登入 button (and an expired login, §3.3): Chrome comes forward on the app.
def test_the_login_opens_the_app_in_the_helpers_browser(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    try:
        chrome.open_login()
        urls = [p.url for p in chrome._ctx.pages]
    finally:
        chrome.close()

    assert f"{site}/" in urls


def test_an_answer_that_is_not_json_reads_as_none(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    try:
        status, data = chrome.send("GET", "/login", None, {})
    finally:
        chrome.close()

    assert (status, data) == (200, None)


# Chrome not installed (or its profile held by another helper): the error
# reaches the window instead of the helper hanging.
def test_a_chrome_that_will_not_start_says_why(tmp_path, site):
    with pytest.raises(Exception, match="no-such-browser"):
        Chrome(tmp_path / "profile", site, channel="no-such-browser", headless=True)


# §3.7.4: the update zip comes through the same signed-in line, as bytes.
def test_a_file_is_fetched_whole_with_the_login(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    try:
        _visit(chrome, f"{site}/login")
        status, body = chrome.fetch_bytes("/api/me", {})
        redirect, _ = chrome.fetch_bytes("/api/expired", {})
    finally:
        chrome.close()

    assert status == 200 and b"sid=abc" in body
    assert redirect == 302


def test_a_file_from_a_backend_that_is_not_there_is_an_os_error(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    chrome = _chrome(tmp_path, f"http://127.0.0.1:{port}")
    try:
        with pytest.raises(OSError):
            chrome.fetch_bytes("/api/x", {})
    finally:
        chrome.close()


# The person closed the helper's Chrome window: it says so, so the helper can
# open it again (review round 1), and closing it after that is harmless.
def test_a_closed_chrome_says_it_is_gone(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    assert chrome.gone is False

    chrome._ctx.close()  # what closing its last window does

    assert chrome.gone is True
    chrome.close()


# §3.7.7: which Chrome the helper drives, for the check-in.
def test_it_says_which_chrome_it_drives(tmp_path, site):
    chrome = _chrome(tmp_path, site)
    try:
        version = chrome.version()
    finally:
        chrome.close()

    assert version.split(".")[0].isdigit()
