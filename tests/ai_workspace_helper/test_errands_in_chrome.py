"""The errands in a real Chrome (docs/plan-outside-helper.md P6): the page the
helper hands the AI is the page the card's own converter would have made of a
paste, links still openable.

Integration: a real Chromium, and the converter BUILT from the frontend source
(`pnpm run build:ai-workspace-helper`) — the fixture builds it, so what is tested is
what the pack script ships.
"""

from __future__ import annotations

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from ai_workspace_helper.builtin_errands import build
from ai_workspace_helper.chrome import Chrome

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_SAMPLES = _ROOT / "ai-workspace-helper" / "parity"


@pytest.fixture(scope="module", autouse=True)
def bundle():
    subprocess.run(
        ["pnpm", "run", "build:ai-workspace-helper"],
        cwd=_ROOT / "web",
        check=True,
        capture_output=True,
    )


class _Site(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/search"):
            self._html(
                "<title>pandas 2.0 - Search</title><h3>"
                '<a href="/url?q=https://pandas.example/whatsnew">What is new</a></h3>'
                '<img src="logo.png" alt="logo">'
            )
        elif self.path.startswith("/guarded"):
            self.send_response(302)
            self.send_header("Location", "/sorry/index")
            self.end_headers()
        elif self.path == "/sso-start":
            # a company sign-in's first hop: nothing to read, a script moves on
            self._html(
                "<title>Signing in</title>"
                "<script>addEventListener('load', () => "
                "setTimeout(() => location.replace('/sso-form'), 300))</script>"
            )
        elif self.path == "/sso-form":
            # the second: a hidden form that posts itself (SAML / OIDC form_post)
            self._html(
                "<title>Signing in</title>"
                '<form id="f" method="post" action="/sso-callback">'
                '<input type="hidden" name="SAMLResponse" value="x"></form>'
                "<script>addEventListener('load', () => "
                "setTimeout(() => document.getElementById('f').submit(), 300))</script>"
            )
        elif self.path == "/landed":
            self._html("<title>Landed</title><p>最後的內容 SSO-TOKEN</p>")
        elif self.path == "/redirecting":
            # an interstitial with words of its own, moving on after a while
            self._html(
                "<title>Redirecting</title><p>正在轉址，請稍候</p>"
                "<script>addEventListener('load', () => "
                "setTimeout(() => location.assign('/landed'), 1200))</script>"
            )
        elif self.path.startswith("/chain/"):
            # four hops, each well inside the quiet time, longer than it in all
            n = int(self.path.rsplit("/", 1)[1])
            nxt = f"/chain/{n + 1}" if n < 3 else "/landed"
            self._html(
                f"<title>Hop {n}</title><p>第 {n} 站</p>"
                f"<script>addEventListener('load', () => "
                f"setTimeout(() => location.replace('{nxt}'), 600))</script>"
            )
        elif self.path == "/slow-form":
            # words of its own, and a form whose answer takes longer than the
            # quiet time: the page is on its way all along
            self._html(
                "<title>Signing in</title><p>正在登入</p>"
                '<form id="f" method="post" action="/slow-callback"></form>'
                "<script>addEventListener('load', () => "
                "setTimeout(() => document.getElementById('f').submit(), 100))</script>"
            )
        elif self.path == "/late":
            # nothing to read, and moving on only after the quiet time
            self._html(
                "<title>Wait</title>"
                "<script>addEventListener('load', () => "
                "setTimeout(() => location.replace('/landed'), 2500))</script>"
            )
        elif self.path == "/blank":
            self._html("<title>Blank</title>")
        elif self.path.startswith("/sorry/"):
            # Google's "unusual traffic" page; the person solves it, it moves on
            self._html(
                "<title>Sorry</title><p>unusual traffic</p>"
                "<script>setTimeout(() => location.assign('/search?q=x'), 1500)</script>"
            )
        else:
            # a site's own "Not Found" page (with no body at all, Chrome
            # refuses the navigation itself — a failure too, with its reason)
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>Not Found</title><h1>Not Found</h1>")

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/slow-callback":
            import time

            time.sleep(2.5)
        # the sign-in's last hop: the server sends the browser to the page
        self.send_response(302)
        self.send_header("Location", "/landed")
        self.end_headers()

    def _html(self, body: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def chrome(tmp_path, site):
    c = Chrome(tmp_path / "profile", site, channel="chromium", headless=True)
    yield c
    c.close()


# D11 parity: the helper's converter IS the card's — the same sample pages
# come out byte for byte as the frontend pinned them
# (web/src/lib/aiWorkspaceHelperBundle.test.ts).
@pytest.mark.parametrize("sample", sorted(_SAMPLES.glob("*.html")), ids=lambda p: p.stem)
def test_a_page_comes_out_as_the_cards_own_converter_makes_it(chrome, sample):
    expected = sample.with_suffix(".md").read_text(encoding="utf-8")

    assert chrome.convert(sample.read_text(encoding="utf-8")) + "\n" == expected


def test_a_search_brings_back_the_results_with_links_the_ai_can_open(chrome, site):
    errands = build(lambda: [{"name": "Google", "url": f"{site}/search?q={{q}}"}])

    page = chrome.run(errands["search"], "pandas 2.0", lambda _n: None)

    assert page.title == "pandas 2.0 - Search"
    assert page.url == f"{site}/search?q=pandas%202.0"
    # relative in the page's HTML; absolute once the helper hands it over
    assert f"[What is new]({site}/url?q=https://pandas.example/whatsnew)" in page.markdown
    assert f"![logo]({site}/logo.png)" in page.markdown


def test_a_verification_waits_for_the_person_then_carries_on(chrome, site):
    notes: list[str] = []

    page = chrome.run(build(lambda: [])["open_url"], f"{site}/guarded", notes.append)

    assert notes == ["captcha", ""]
    assert "What is new" in page.markdown


def test_a_page_that_is_not_there_fails(chrome, site):
    with pytest.raises(Exception, match="404"):
        chrome.run(build(lambda: [])["open_url"], f"{site}/gone", lambda _n: None)


# A page reached through hops the person never sees — a script, a form that
# posts itself, a server redirect, as a company sign-in does — is read where
# the hops end, not at the first (it read as nothing: 「沒查到：nothing to
# send」 on the person's PC, plan D22).
def test_a_page_behind_redirects_is_read_where_they_end(chrome, site):
    page = chrome.run(build(lambda: [])["open_url"], f"{site}/sso-start", lambda _n: None)

    assert page.url == f"{site}/landed"
    assert "SSO-TOKEN" in page.markdown


# An interstitial with words of its own is not the page: it is read once the
# address has stopped changing.
def test_an_interstitial_that_moves_on_is_not_taken_for_the_page(chrome, site):
    page = chrome.run(build(lambda: [])["open_url"], f"{site}/redirecting", lambda _n: None)

    assert page.url == f"{site}/landed"
    assert "SSO-TOKEN" in page.markdown and "正在轉址" not in page.markdown


# A page that never shows any text fails with why — in words the person can
# act on — rather than an empty page the backend refuses in English.
def test_a_page_with_no_text_says_so(chrome, site, monkeypatch):
    from ai_workspace_helper import builtin_errands

    monkeypatch.setattr(builtin_errands, "SETTLE_LIMIT_MS", 3_000)

    with pytest.raises(Exception, match="沒有文字"):
        chrome.run(build(lambda: [])["open_url"], f"{site}/blank", lambda _n: None)


# Each hop inside the quiet time, the hops together longer than it — and
# each with words of its own: the wait starts again at every hop, so the
# chain is followed to its end.
def test_a_long_chain_of_quick_hops_is_followed_to_its_end(chrome, site):
    page = chrome.run(build(lambda: [])["open_url"], f"{site}/chain/0", lambda _n: None)

    assert page.url == f"{site}/landed"
    assert "站" not in page.markdown


# A page with words on it whose form is still being answered is on its way:
# it is not read as the page — reading it meanwhile waits for the new one and
# fails as "context destroyed", and the errand reads again.
def test_a_page_whose_form_is_still_being_answered_is_not_the_page(chrome, site):
    page = chrome.run(build(lambda: [])["open_url"], f"{site}/slow-form", lambda _n: None)

    assert page.url == f"{site}/landed"
    assert "正在登入" not in page.markdown


# Settled on a page with nothing to read, the helper keeps looking (up to the
# limit) rather than handing back nothing.
def test_a_blank_page_that_moves_on_late_is_waited_for(chrome, site):
    page = chrome.run(build(lambda: [])["open_url"], f"{site}/late", lambda _n: None)

    assert page.url == f"{site}/landed"
    assert "SSO-TOKEN" in page.markdown
