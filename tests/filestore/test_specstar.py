import pytest

from workspace_app.filestore.protocol import FileNotFound
from workspace_app.filestore.specstar_impl import SpecstarFileStore


async def test_write_then_read_returns_same_bytes(store: SpecstarFileStore):
    await store.write("ws1", "/a.txt", b"hello")
    assert await store.read("ws1", "/a.txt") == b"hello"


async def test_read_missing_path_raises_file_not_found(store: SpecstarFileStore):
    await store.write("ws1", "/exists", b"x")
    with pytest.raises(FileNotFound):
        await store.read("ws1", "/nope")


async def test_read_in_unknown_workspace_raises_file_not_found(store: SpecstarFileStore):
    with pytest.raises(FileNotFound):
        await store.read("never-touched", "/any")


async def test_list_returns_all_written_paths(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"A")
    await store.write("ws1", "/b/c", b"BC")
    assert sorted(await store.ls("ws1")) == ["/a", "/b/c"]


async def test_list_unknown_workspace_returns_empty(store: SpecstarFileStore):
    assert await store.ls("never-touched") == []


async def test_exists_true_after_write(store: SpecstarFileStore):
    await store.write("ws1", "/x", b"x")
    assert await store.exists("ws1", "/x") is True


async def test_exists_false_for_unknown_path(store: SpecstarFileStore):
    await store.write("ws1", "/x", b"x")
    assert await store.exists("ws1", "/y") is False


async def test_exists_false_in_unknown_workspace(store: SpecstarFileStore):
    assert await store.exists("never-touched", "/x") is False


async def test_write_overwrites_previous_content(store: SpecstarFileStore):
    await store.write("ws1", "/x", b"first")
    await store.write("ws1", "/x", b"second")
    assert await store.read("ws1", "/x") == b"second"


async def test_delete_removes_file(store: SpecstarFileStore):
    await store.write("ws1", "/x", b"x")
    await store.delete("ws1", "/x")
    assert await store.exists("ws1", "/x") is False
    with pytest.raises(FileNotFound):
        await store.read("ws1", "/x")


async def test_delete_missing_path_raises(store: SpecstarFileStore):
    await store.write("ws1", "/exists", b"x")
    with pytest.raises(FileNotFound):
        await store.delete("ws1", "/nope")


async def test_delete_in_unknown_workspace_raises(store: SpecstarFileStore):
    with pytest.raises(FileNotFound):
        await store.delete("never", "/x")


async def test_list_filters_by_prefix(store: SpecstarFileStore):
    await store.write("ws1", "/src/a.py", b"a")
    await store.write("ws1", "/src/b.py", b"b")
    await store.write("ws1", "/README", b"r")
    assert sorted(await store.ls("ws1", prefix="/src/")) == ["/src/a.py", "/src/b.py"]


async def test_two_workspaces_are_isolated(store: SpecstarFileStore):
    await store.write("ws1", "/x", b"one")
    await store.write("ws2", "/x", b"two")
    assert await store.read("ws1", "/x") == b"one"
    assert await store.read("ws2", "/x") == b"two"


async def test_write_marks_path_dirty(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x")
    await store.write("ws1", "/b/c", b"y")
    assert store.dirty_paths("ws1") == {"/a", "/b/c"}


async def test_clear_dirty_empties_set(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x")
    store.clear_dirty("ws1")
    assert store.dirty_paths("ws1") == set()


async def test_dirty_paths_is_per_workspace(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x")
    await store.write("ws2", "/b", b"y")
    assert store.dirty_paths("ws1") == {"/a"}
    assert store.dirty_paths("ws2") == {"/b"}


async def test_read_does_not_dirty(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x")
    store.clear_dirty("ws1")
    await store.read("ws1", "/a")
    await store.ls("ws1")
    await store.exists("ws1", "/a")
    assert store.dirty_paths("ws1") == set()


async def test_dirty_paths_for_unknown_workspace_is_empty(store: SpecstarFileStore):
    assert store.dirty_paths("never") == set()


# --- Honest directories ---


async def test_write_creates_ancestor_dirs(store: SpecstarFileStore):
    await store.write("ws1", "/data/raw/x.csv", b"x")
    assert await store.is_dir("ws1", "/data")
    assert await store.is_dir("ws1", "/data/raw")
    assert not await store.is_dir("ws1", "/data/raw/x.csv")


async def test_mkdir_empty_dir_persists_without_files(store: SpecstarFileStore):
    await store.mkdir("ws1", "/empty")
    assert await store.is_dir("ws1", "/empty")
    assert await store.ls("ws1") == []
    assert "/empty" in await store.listdir("ws1")


async def test_mkdir_over_existing_file_raises(store: SpecstarFileStore):
    from workspace_app.filestore.protocol import FileExists

    await store.write("ws1", "/d", b"x")
    with pytest.raises(FileExists):
        await store.mkdir("ws1", "/d")


async def test_delete_last_file_keeps_dir(store: SpecstarFileStore):
    await store.write("ws1", "/d/a.txt", b"a")
    await store.delete("ws1", "/d/a.txt")
    assert await store.is_dir("ws1", "/d")


async def test_rmdir_removes_subtree(store: SpecstarFileStore):
    await store.write("ws1", "/d/a.txt", b"a")
    await store.write("ws1", "/d/sub/b.txt", b"b")
    await store.mkdir("ws1", "/d/empty")
    await store.rmdir("ws1", "/d")
    assert not await store.is_dir("ws1", "/d")
    assert not await store.is_dir("ws1", "/d/sub")
    assert not await store.exists("ws1", "/d/a.txt")


async def test_rmdir_missing_raises(store: SpecstarFileStore):
    with pytest.raises(FileNotFound):
        await store.rmdir("ws1", "/nope")


async def test_listdir_returns_all_dirs(store: SpecstarFileStore):
    await store.write("ws1", "/a/b/x", b"1")
    await store.mkdir("ws1", "/c")
    assert sorted(await store.listdir("ws1")) == ["/a", "/a/b", "/c"]


async def test_rmdir_missing_dir_in_existing_workspace_raises(store: SpecstarFileStore):
    await store.write("ws1", "/a.txt", b"x")  # creates the workspace record
    with pytest.raises(FileNotFound):
        await store.rmdir("ws1", "/nope")
