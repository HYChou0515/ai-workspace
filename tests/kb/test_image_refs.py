"""#513 P6 — extract image references from HTML/MD, gate them by an allowlist.

The defect library's existing HTML/MD knowledge carries its figures as
external ``http`` links (``<img src>`` / ``![](url)``) pointing at an internal
image server. Ingest must (1) pull the URLs out of the text and (2) keep only
the allowlisted hosts before any fetch — SSRF fencing is a hard requirement.
These are pure functions; the network lives behind ``IImageFetcher``.
"""

from __future__ import annotations

from workspace_app.kb.image_refs import extract_image_urls, filter_allowed_urls


def test_extract_img_src_from_html():
    html = '<p>see <img src="http://img.local/a.png" alt="a"> and more</p>'
    assert extract_image_urls(html, content_format="html") == ["http://img.local/a.png"]


def test_extract_image_links_from_markdown():
    md = (
        "# Defect X\n\n"
        "Ring-shaped particle:\n\n"
        "![fig1](http://img.local/x/1.png)\n\n"
        "and a second view ![](http://img.local/x/2.jpg) inline.\n"
    )
    assert extract_image_urls(md, content_format="markdown") == [
        "http://img.local/x/1.png",
        "http://img.local/x/2.jpg",
    ]


def test_filter_keeps_only_allowlisted_hosts():
    urls = [
        "http://img.local/a.png",  # allowed host
        "http://evil.example.com/steal.png",  # off-allowlist → dropped (SSRF)
        "https://img.local/b.png",  # allowed host, https
    ]
    kept = filter_allowed_urls(urls, allowed_hosts=["img.local"])
    assert kept == ["http://img.local/a.png", "https://img.local/b.png"]


def test_filter_drops_relative_and_non_web_schemes():
    urls = [
        "fig.png",  # relative — no host
        "/abs/path.png",  # root-relative — no host
        "data:image/png;base64,AAAA",  # data URI — not fetchable, SSRF-ish
        "file:///etc/passwd",  # file scheme — classic SSRF/LFI target
        "ftp://img.local/x.png",  # non-web scheme even on an allowed host
    ]
    assert filter_allowed_urls(urls, allowed_hosts=["img.local"]) == []


def test_filter_host_match_is_case_and_port_insensitive():
    urls = ["http://IMG.Local:8080/a.png", "http://img.local/b.png"]
    kept = filter_allowed_urls(urls, allowed_hosts=["Img.Local"])
    assert kept == ["http://IMG.Local:8080/a.png", "http://img.local/b.png"]
