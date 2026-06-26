import pytest
from specstar import BackendBinding, BackendConfig, ConnectionProfile

from workspace_app.filestore.protocol import FileNotFound
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec


@pytest.fixture
def disk_store(tmp_path) -> SpecstarFileStore:
    """A specstar filestore on a real on-disk blob store — so the streaming
    blob paths (DiskBlobStore upload-session + get_stream) are exercised, unlike
    the in-memory backend the `store` fixture uses."""
    backend = BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(tmp_path)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )
    return SpecstarFileStore(make_spec(default_user="u", backend=backend))


async def test_second_instance_on_the_same_store_sees_the_files():
    """Multi-pod (#16): a fresh SpecstarFileStore on the same backing store (a
    second pod, with an empty cache) must see what another instance wrote — the
    workspace's resource id is derived from workspace_id, not held in memory."""
    spec = make_spec(default_user="u")
    pod1 = SpecstarFileStore(spec)
    await pod1.write("ws1", "/a.txt", b"hello")
    await pod1.mkdir("ws1", "/sub")

    pod2 = SpecstarFileStore(spec)  # second pod: fresh instance, same store
    assert await pod2.read("ws1", "/a.txt") == b"hello"
    assert "/sub" in await pod2.listdir("ws1")
    await pod2.write("ws1", "/b.txt", b"world")
    assert await pod1.read("ws1", "/b.txt") == b"world"  # one shared resource, no duplicate


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


# --- streaming write from a temp file (issue #219, no whole-file-in-RAM) ---


async def test_write_from_path_stores_content_and_ancestor_dirs(store, tmp_path):
    payload = b"streamed-content-larger-than-one-chunk" * 100
    src = tmp_path / "big.bin"
    src.write_bytes(payload)
    await store.write_from_path("ws1", "/data/big.bin", src, "application/octet-stream")
    assert await store.read("ws1", "/data/big.bin") == payload
    assert await store.is_dir("ws1", "/data")


async def test_write_from_path_overwrites_existing(store, tmp_path):
    await store.write("ws1", "/x", b"old")
    src = tmp_path / "new.bin"
    src.write_bytes(b"new-streamed")
    await store.write_from_path("ws1", "/x", src, None)
    assert await store.read("ws1", "/x") == b"new-streamed"


async def test_write_from_path_empty_file(store, tmp_path):
    src = tmp_path / "empty.bin"
    src.write_bytes(b"")
    await store.write_from_path("ws1", "/empty", src, None)
    assert await store.read("ws1", "/empty") == b""


async def test_read_to_file_streams_content_out(store, tmp_path):
    await store.write("ws1", "/a.bin", b"content-out" * 50)
    dest = tmp_path / "out.bin"
    await store.read_to_file("ws1", "/a.bin", dest)
    assert dest.read_bytes() == b"content-out" * 50


async def test_read_to_file_missing_raises(store, tmp_path):
    with pytest.raises(FileNotFound):
        await store.read_to_file("ws1", "/nope", tmp_path / "out.bin")


async def test_disk_backend_streams_write_and_read(disk_store, tmp_path):
    # Exercises DiskBlobStore: write_from_path's upload-session AND
    # read_to_file's get_stream chunk loop (both no-ops on the memory backend).
    src = tmp_path / "src.bin"
    src.write_bytes(b"disk-streamed-payload" * 1000)
    await disk_store.write_from_path("ws1", "/big.bin", src, "application/octet-stream")
    assert await disk_store.read("ws1", "/big.bin") == b"disk-streamed-payload" * 1000
    out = tmp_path / "out.bin"
    await disk_store.read_to_file("ws1", "/big.bin", out)
    assert out.read_bytes() == b"disk-streamed-payload" * 1000
