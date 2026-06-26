"""Issue #247: the generic two-step ZIP download infra — pure helpers.

The route-level behaviour (prepare → stream, 404s, reaping) is exercised through
the KB + workspace endpoints; these cover the pure functions branch-by-branch.
"""

from __future__ import annotations

import io
import os
import time
import zipfile

from workspace_app.files.zip_download import (
    downloads_dir,
    prepared_path,
    safe_zip_filename,
    subtree_arcname,
    sweep_stale_downloads,
    write_zip_members,
)


def test_subtree_arcname_reroots_under_prefix():
    # whole-tree: every path maps to itself
    assert subtree_arcname("a/b.md", "") == "a/b.md"
    assert subtree_arcname("/a/b.md", "/") == "a/b.md"
    # an empty path at the root has no name
    assert subtree_arcname("", "") is None
    # a descendant re-roots at the prefix
    assert subtree_arcname("img/sub/a.txt", "img") == "sub/a.txt"
    assert subtree_arcname("/img/logo.md", "img/") == "logo.md"
    # the prefix naming a file itself re-roots to the basename
    assert subtree_arcname("img/logo.md", "img/logo.md") == "logo.md"
    # a sibling that merely shares the prefix string is NOT inside the folder
    assert subtree_arcname("imgs/x.md", "img") is None
    assert subtree_arcname("top.md", "img") is None


def test_safe_zip_filename_sanitizes_and_falls_back():
    assert safe_zip_filename("My Folder") == "My Folder.zip"
    assert safe_zip_filename("a/b:c") == "a_b_c.zip"
    assert safe_zip_filename("   ") == "download.zip"  # blank → default fallback
    assert safe_zip_filename("   ", fallback="workspace") == "workspace.zip"


def test_write_zip_members_writes_deflated_entries():
    out = downloads_dir() / "members_test.zip"
    write_zip_members(out, [("a.txt", b"alpha"), ("d/b.txt", b"beta")])
    with zipfile.ZipFile(io.BytesIO(out.read_bytes())) as zf:
        assert set(zf.namelist()) == {"a.txt", "d/b.txt"}
        assert zf.read("d/b.txt") == b"beta"
    out.unlink()


def test_prepared_path_rejects_malformed_or_missing():
    assert prepared_path("not-a-hex-id") is None  # malformed → no fs touch
    assert prepared_path("0" * 32) is None  # well-formed but no such file


def test_sweep_removes_stale_but_keeps_fresh_downloads():
    d = downloads_dir()
    stale = d / "stale1234567890abcdef1234567890ab.zip"
    fresh = d / "fresh1234567890abcdef1234567890ab.zip"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old = time.time() - 10_000
    os.utime(stale, (old, old))

    sweep_stale_downloads(ttl_seconds=3600)

    assert not stale.exists()  # older than the TTL → reaped
    assert fresh.exists()  # within the TTL → kept
    fresh.unlink()
