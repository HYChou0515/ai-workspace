"""`walk_tree` is one algorithm behind every `walk`; these pin the parts the
integration-marked sandbox tests are the only other witness to, so the unit
run (what CI executes) covers them too."""

import os

from sandbox_host.walk import Entry, dict_lister, scandir_lister, walk_tree


def test_a_root_that_names_nothing_lists_nothing():
    """Not an error: the file tree's callers hand a user-typed prefix straight
    in, and the recursive walk this replaced answered an absent one with []."""
    walked = walk_tree(dict_lister({"/a.txt": b"x"}, set()), "/nope")
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
