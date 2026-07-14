"""#513 P6 — IImageFetcher: fetch an allowlisted internal image URL → bytes.

Defense-in-depth: even though the Ingestor pre-filters URLs with
``filter_allowed_urls``, the fetcher re-checks the allowlist itself, so a caller
that forgets to filter still can't SSRF. A single bad URL returns ``None`` (the
ingest skips it) rather than raising — one missing figure must not fail the doc.
The network call is monkeypatched here; a real GET is an integration test.
"""

from __future__ import annotations

import pytest

from workspace_app.kb.image_fetcher import HttpImageFetcher

_URLOPEN = "workspace_app.kb.image_fetcher.urlopen"


class _FakeHeaders:
    def __init__(self, mime: str) -> None:
        self._mime = mime

    def get_content_type(self) -> str:
        return self._mime


class _FakeResp:
    def __init__(self, data: bytes, mime: str) -> None:
        self._data = data
        self.headers = _FakeHeaders(mime)

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_off_allowlist_url_is_refused_without_touching_the_network():
    fetcher = HttpImageFetcher(allowed_hosts=["img.local"])
    # No urlopen monkeypatch: if this tried to hit the network the test host
    # would fail/hang — proving the allowlist guard short-circuits first.
    assert fetcher.fetch("http://evil.example.com/steal.png") is None


def test_fetch_returns_bytes_and_mime_on_success(monkeypatch: pytest.MonkeyPatch):
    png = b"\x89PNG\r\n\x1a\n fake"
    monkeypatch.setattr(_URLOPEN, lambda url, timeout: _FakeResp(png, "image/png"))
    fetcher = HttpImageFetcher(allowed_hosts=["img.local"])
    assert fetcher.fetch("http://img.local/a.png") == (png, "image/png")


def test_network_error_returns_none(monkeypatch: pytest.MonkeyPatch):
    def boom(url: str, timeout: float) -> object:
        raise OSError("connection refused")

    monkeypatch.setattr(_URLOPEN, boom)
    fetcher = HttpImageFetcher(allowed_hosts=["img.local"])
    assert fetcher.fetch("http://img.local/a.png") is None


def test_empty_body_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_URLOPEN, lambda url, timeout: _FakeResp(b"", "image/png"))
    fetcher = HttpImageFetcher(allowed_hosts=["img.local"])
    assert fetcher.fetch("http://img.local/empty.png") is None


def test_factory_returns_none_when_no_allowlist_configured():
    """Default off: no `kb.image_fetch.allowed_hosts` ⇒ no fetcher ⇒ ingest
    behaves exactly as before (referenced images stay inert links)."""
    from workspace_app.factories import Settings, get_image_fetcher

    assert get_image_fetcher(Settings()) is None


def test_factory_builds_http_fetcher_from_allowlist():
    from dataclasses import replace

    from workspace_app.factories import Settings, get_image_fetcher

    base = Settings()
    settings = replace(
        base,
        kb=replace(
            base.kb,
            image_fetch=replace(base.kb.image_fetch, allowed_hosts=["img.local"]),
        ),
    )
    fetcher = get_image_fetcher(settings)
    assert isinstance(fetcher, HttpImageFetcher)
