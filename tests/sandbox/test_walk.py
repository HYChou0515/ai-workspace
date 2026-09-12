"""`walk_tree` is one algorithm behind every `walk`; these pin the parts the
integration-marked sandbox tests are the only other witness to, so the unit
run (what CI executes) covers them too."""

import os

import pytest

from workspace_app.sandbox.walk import Entry, flat_lister, scandir_lister, walk_tree


def test_a_root_that_names_nothing_lists_nothing():
    """Not an error: the file tree's callers hand a user-typed prefix straight
    in, and the recursive walk this replaced answered an absent one with []."""
    walked = walk_tree(flat_lister({"/a.txt": (1, "v")}, set()), "/nope")
    assert walked.files == [] and walked.dirs == [] and walked.unwalked == []
    assert walked.truncated is False


def test_a_child_that_vanishes_mid_walk_stays_on_the_tree_but_empty():
    listing = {
        "/": [Entry("gone", "dir"), Entry("kept", "dir")],
        "/kept": [Entry("f.txt", "file", 1, "v1")],
    }

    def list_dir(rel: str):
        if rel not in listing:
            raise FileNotFoundError(rel)
        return listing[rel]

    walked = walk_tree(list_dir, "/")
    assert sorted(walked.dirs) == ["/gone", "/kept"]
    assert [e.path for e in walked.files] == ["/kept/f.txt"]
    assert walked.unwalked == []


def test_a_symlinked_directory_is_listed_and_never_entered():
    """It is on the tree (empty), and NOT in `unwalked` — an on-demand fetch
    would follow the link, and a link can point outside the workspace."""
    listing = {
        "/": [Entry("link", "linkdir"), Entry("real", "dir"), Entry("pipe", "other")],
        "/real": [],
    }
    walked = walk_tree(lambda rel: listing[rel], "/", prune=["link"])
    assert sorted(walked.dirs) == ["/link", "/real"]
    assert walked.unwalked == []
    assert walked.files == []  # the fifo is nobody's file


def test_scandir_lister_classifies_what_the_recursive_walk_did(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "f.txt").write_bytes(b"four")
    os.symlink(tmp_path / "real", tmp_path / "linkdir")
    os.symlink(tmp_path / "real" / "f.txt", tmp_path / "linkfile")
    os.symlink(tmp_path / "nowhere", tmp_path / "dangling")
    os.mkfifo(tmp_path / "pipe")

    by_name = {e.name: e for e in scandir_lister(tmp_path)("/")}
    assert by_name["real"].kind == "dir"
    assert by_name["linkdir"].kind == "linkdir"
    assert by_name["linkfile"].kind == "file" and by_name["linkfile"].size == 4
    assert by_name["dangling"].kind == "other"
    assert by_name["pipe"].kind == "other"
    f = {e.name: e for e in scandir_lister(tmp_path)("/real")}["f.txt"]
    assert f.kind == "file" and f.size == 4
    st = (tmp_path / "real" / "f.txt").stat()
    assert f.version == f"{st.st_mtime_ns}-{st.st_size}"


def test_a_cyclic_symlink_is_skipped_and_its_siblings_are_still_listed(tmp_path):
    """`Path.is_dir()` swallowed ELOOP and the old walk skipped the entry; a
    `DirEntry.is_dir()` raises it. One bad link must not 500 the whole tree —
    and on `kind: local` the mirror walks too, so it must not stop persisting."""
    (tmp_path / "a.txt").write_bytes(b"x")
    os.symlink("loop", tmp_path / "loop")
    walked = walk_tree(scandir_lister(tmp_path), "/")
    assert [e.path for e in walked.files] == ["/a.txt"]
    assert walked.dirs == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_an_unreadable_directory_is_listed_but_not_entered(tmp_path):
    """What `os.walk` / `Path.walk` do: the folder is there (its parent said
    so), its contents are not ours to see, and the rest of the tree goes on."""
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "f.txt").write_bytes(b"x")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_bytes(b"x")
    locked.chmod(0)
    try:
        walked = walk_tree(scandir_lister(tmp_path), "/")
    finally:
        locked.chmod(0o755)
    assert sorted(walked.dirs) == ["/locked", "/ok"]
    assert [e.path for e in walked.files] == ["/ok/f.txt"]


def test_an_entry_that_vanishes_mid_listing_is_skipped_without_losing_its_siblings(
    tmp_path, monkeypatch
):
    """An agent deletes temp files during a turn; the readdir has already
    returned the name when the stat finds it gone. Only that entry is dropped
    — dropping the whole directory would make the mirror delete its durable
    copies of every sibling."""
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_bytes(b"x")
    real_scandir = os.scandir

    class _Vanishing:
        def __init__(self, it):
            self._it = it

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._it.close()

        def __iter__(self):
            for e in self._it:
                if e.name == "b.txt":
                    os.unlink(e.path)  # gone between the readdir and the stat
                yield e

    monkeypatch.setattr(os, "scandir", lambda p: _Vanishing(real_scandir(p)))
    walked = walk_tree(scandir_lister(tmp_path), "/")
    assert sorted(e.path for e in walked.files) == ["/a.txt", "/c.txt"]


def test_flat_lister_reaches_a_recorded_folder_whose_parent_was_never_recorded():
    """A store row for `/a/b` with no row for `/a` (an orphan) still draws:
    the old listing never walked, so it showed such a folder; the walk must
    infer the missing ancestor rather than lose the subtree behind it."""
    walked = walk_tree(flat_lister({}, ["/a/b"]), "/")
    assert sorted(walked.dirs) == ["/a", "/a/b"]


def test_flat_lister_hangs_every_ancestor_of_a_deep_path_under_its_parent():
    """`/a/b/c/f.txt` alone must produce the chain `/a` → `/a/b` → `/a/b/c`,
    each listed by its parent — the index must not stop registering at the
    first ancestor it happens to have created a slot for."""
    walked = walk_tree(flat_lister({"/a/b/c/f.txt": (1, "v")}, []), "/")
    assert sorted(walked.dirs) == ["/a", "/a/b", "/a/b/c"]
    assert [e.path for e in walked.files] == ["/a/b/c/f.txt"]
    level = walk_tree(flat_lister({"/a/b/c/f.txt": (1, "v")}, []), "/a/b", depth=1)
    assert level.dirs == ["/a/b/c"] and level.unwalked == ["/a/b/c"]


def test_flat_lister_tolerates_keys_that_are_not_canonical_paths():
    """An in-memory sandbox stores whatever path it was handed: a test uploads
    `pyproject.toml` with no leading slash, an agent may write `//x`. The old
    per-directory scan silently skipped such keys; an index must not KeyError
    on them, and a `//` directory must not become a child named "" that walks
    the root forever. Canonicalise on the way in, and look the entry up by
    its original key."""
    files = {"pyproject.toml": (3, "v1"), "//x": (1, "v2"), "/ok/a.txt": (1, "v3")}
    walked = walk_tree(flat_lister(files, ["//", "ok/", "/ok"]), "/")
    assert sorted((e.path, e.size) for e in walked.files) == [
        ("/ok/a.txt", 1),
        ("/pyproject.toml", 3),
        ("/x", 1),
    ]
    assert walked.dirs == ["/ok"]


def test_flat_lister_indexes_once_so_a_large_listing_walks_in_linear_time():
    """The cold store branch runs this on the request path. Scanning the whole
    listing per directory made 12k files cost a second and 50k fifteen; an
    index makes 20k files with 2k folders a few tens of milliseconds. The
    bound is ~20× the measured cost, so it fails on the algorithm, not the box."""
    import time

    files = {f"/pkg{i}/lib/f{j}.js": (1, "v") for i in range(1000) for j in range(20)}
    t0 = time.perf_counter()
    walked = walk_tree(flat_lister(files, []), "/")
    assert len(walked.files) == 20_000 and len(walked.dirs) == 2_000
    assert time.perf_counter() - t0 < 3.0
