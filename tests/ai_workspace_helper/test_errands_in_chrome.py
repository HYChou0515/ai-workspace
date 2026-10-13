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
