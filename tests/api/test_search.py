"""POST /a/{slug}/items/{id}/search + /replace — VSCode-style global
text search/replace over the FileStore (grep + sed equivalents).
"""

from __future__ import annotations

from .conftest import Harness


def _seed(h: Harness, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        h.client.put(h.wpath(f"/files{path}"), content=data)


def test_search_plain_substring(harness: Harness):
    _seed(
        harness,
        {
            "/a.md": b"void rate spiked\nall good\nVOID again",
            "/b.txt": b"nothing here",
        },
    )
    resp = harness.client.post(harness.wpath("/search"), json={"query": "void"})
    assert resp.status_code == 200
    by_path = {r["path"]: r["matches"] for r in resp.json()}
    # case-insensitive by default → matches line 1 and line 3 of a.md
    assert "/a.md" in by_path
    lines = [m["line"] for m in by_path["/a.md"]]
    assert lines == [1, 3]
    assert "/b.txt" not in by_path


def test_search_case_sensitive(harness: Harness):
    _seed(harness, {"/a.md": b"void\nVOID"})
    resp = harness.client.post(
        harness.wpath("/search"),
        json={"query": "void", "caseSensitive": True},
    )
    matches = resp.json()[0]["matches"]
    assert [m["line"] for m in matches] == [1]


def test_search_whole_word(harness: Harness):
    _seed(harness, {"/a.md": b"void\navoidance\nvoid!"})
    resp = harness.client.post(
        harness.wpath("/search"),
        json={"query": "void", "wholeWord": True},
    )
    lines = [m["line"] for m in resp.json()[0]["matches"]]
    assert lines == [1, 3]  # "avoidance" excluded


def test_search_regex(harness: Harness):
    _seed(harness, {"/a.md": b"err 500\nok 200\nerr 503"})
    resp = harness.client.post(
        harness.wpath("/search"),
        json={"query": r"err \d+", "regex": True},
    )
    lines = [m["line"] for m in resp.json()[0]["matches"]]
    assert lines == [1, 3]


def test_search_include_exclude_globs(harness: Harness):
    _seed(
        harness,
        {"/keep.md": b"hit", "/skip.txt": b"hit", "/data/x.csv": b"hit"},
    )
    inc = harness.client.post(
        harness.wpath("/search"), json={"query": "hit", "include": "*.md"}
    ).json()
    assert {r["path"] for r in inc} == {"/keep.md"}

    exc = harness.client.post(
        harness.wpath("/search"), json={"query": "hit", "exclude": "data/**"}
    ).json()
    assert {r["path"] for r in exc} == {"/keep.md", "/skip.txt"}


def test_search_invalid_regex_returns_422(harness: Harness):
    resp = harness.client.post(harness.wpath("/search"), json={"query": "(", "regex": True})
    assert resp.status_code == 422


def test_replace_rewrites_matches(harness: Harness):
    _seed(harness, {"/a.md": b"void rate\nvoid count", "/b.md": b"no match"})
    resp = harness.client.post(
        harness.wpath("/replace"),
        json={"query": "void", "replacement": "VOID"},
    )
    assert resp.status_code == 200
    assert resp.json()["replaced"] == 2
    assert harness.client.get(harness.wpath("/files/a.md")).content == b"VOID rate\nVOID count"
    # untouched file unchanged
    assert harness.client.get(harness.wpath("/files/b.md")).content == b"no match"


def test_replace_regex_with_backref(harness: Harness):
    _seed(harness, {"/a.md": b"zone 3 drift"})
    resp = harness.client.post(
        harness.wpath("/replace"),
        json={"query": r"zone (\d+)", "replacement": r"Z\1", "regex": True},
    )
    assert resp.json()["replaced"] == 1
    assert harness.client.get(harness.wpath("/files/a.md")).content == b"Z3 drift"


def test_search_empty_query_returns_nothing(harness: Harness):
    """An empty query box matches nothing rather than every line."""
    _seed(harness, {"/a.md": b"anything"})
    resp = harness.client.post(harness.wpath("/search"), json={"query": ""})
    assert resp.status_code == 200
    assert resp.json() == []


def test_replace_empty_query_is_a_noop(harness: Harness):
    _seed(harness, {"/a.md": b"anything"})
    resp = harness.client.post(harness.wpath("/replace"), json={"query": "", "replacement": "x"})
    assert resp.json() == {"replaced": 0}
    assert harness.client.get(harness.wpath("/files/a.md")).content == b"anything"


def test_search_skips_binary_files(harness: Harness):
    """Non-UTF-8 blobs are skipped, not crashed on."""
    _seed(harness, {"/img.png": b"\xff\xd8\xff\x00rate", "/note.md": b"rate"})
    resp = harness.client.post(harness.wpath("/search"), json={"query": "rate"})
    assert {r["path"] for r in resp.json()} == {"/note.md"}
