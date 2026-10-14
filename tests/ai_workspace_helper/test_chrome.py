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
from ai_workspace_helper.chrome import Chrome
from ai_workspace_helper.errands import FetchedPage

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


# ── a company certificate the helper's Node does not trust (plan D20) ──────


@pytest.fixture
def https_site(tmp_path):
    """The site over HTTPS with a certificate from a private CA, the chain
    left out — what the person's PC met: Chrome trusts it (the company pushes
    its CA to Windows), Playwright's own Node says "unable to verify the first
    certificate"."""
    import datetime
    import ipaddress
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def name(text: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])

    now = datetime.datetime.now(datetime.UTC)
    company_ca, key = (
        ec.generate_private_key(ec.SECP256R1()),
        ec.generate_private_key(ec.SECP256R1()),
    )
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name("127.0.0.1"))
        .issuer_name(name("Company CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(company_ca, hashes.SHA256())
    )
    (tmp_path / "leaf.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "leaf.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "leaf.key")  # the leaf alone
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# D20: the helper's requests to AI Workspace do not verify its certificate —
# this deploy's convention, its operator says ("verify=false"); otherwise every check-in is
# 「連不上」 while the person is signed in fine in Chrome.
def test_the_helpers_requests_to_ai_workspace_pass_a_certificate_node_cannot_verify(
    tmp_path, https_site
):
    chrome = _chrome(tmp_path, https_site)
    try:
        status, data = chrome.send("GET", "/api/me", None, {})
    finally:
        chrome.close()

    assert status == 200 and isinstance(data, dict)


# ...and ONLY those: the pages the helper's Chrome opens for an errand still
# verify, as the person's Chrome does.
def test_the_pages_it_opens_still_verify_certificates(tmp_path, https_site):
    chrome = _chrome(tmp_path, https_site)
    try:
        with pytest.raises(Exception, match="CERT"):
            _visit(chrome, https_site + "/a-page")
    finally:
        chrome.close()


# Playwright's error ends in a "Call log:" listing every request header — the
# login cookie among them — and why it cannot reach AI Workspace now goes to
# helper.log, which the 記錄檔 tab asks the person to send to IT (review of
# #933). The reason stays; the call log does not.
def test_why_a_request_failed_never_carries_the_login_cookie(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed once the block ends: nobody listens
    chrome = _chrome(tmp_path, f"http://127.0.0.1:{port}")
    chrome._ctx.add_cookies(
        [{"name": "sess", "value": "SSO-SECRET", "url": f"http://127.0.0.1:{port}"}]
    )
    try:
        with pytest.raises(OSError) as failed:
            chrome.send("GET", "/api/me", None, {})
    finally:
        chrome.close()

    reason = str(failed.value)
    assert "ECONNREFUSED" in reason
    assert "SSO-SECRET" not in reason and "Call log" not in reason
