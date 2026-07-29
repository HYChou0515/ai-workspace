import pytest
from specstar import BackendBinding, BackendConfig, ConnectionProfile

from workspace_app.filestore.protocol import FileExists, FileNotFound
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


async def test_create_exclusive_is_create_only(store: SpecstarFileStore):
    """#419 N1 arbiter: a create-only claim on the durable store rejects a
    duplicate path (the `create` with a fixed resource_id can't overwrite), so
    the same entity number can't be issued twice — even across pods."""
    await store.create_exclusive("ws", "/issues/1.md", b"one")
    with pytest.raises(FileExists):
        await store.create_exclusive("ws", "/issues/1.md", b"two")
    assert await store.read("ws", "/issues/1.md") == b"one"


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


async def test_stat_all_returns_path_and_size(store: SpecstarFileStore):
    await store.write("ws1", "/a.txt", b"hello")
    await store.write("ws1", "/sub/b.txt", b"world!")
    assert sorted(await store.stat_all("ws1")) == [("/a.txt", 5), ("/sub/b.txt", 6)]


async def test_stat_all_filters_by_prefix(store: SpecstarFileStore):
    await store.write("ws1", "/src/a.py", b"aa")
    await store.write("ws1", "/README", b"r")
    assert await store.stat_all("ws1", "/src/") == [("/src/a.py", 2)]


async def test_stat_all_unknown_workspace_is_empty(store: SpecstarFileStore):
    assert await store.stat_all("nope") == []


async def test_stat_all_reports_sizes_without_restoring_blobs(disk_store, tmp_path):
    """#362 perf guard: sizes come from each record's inline metadata, so
    listing a blob-backed file NEVER restores its offloaded bytes. A regression
    to per-file reads would trip ``restore_binary`` and fail this."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 5000)
    await disk_store.write("ws1", "/a.txt", b"hello")  # 5, inline
    await disk_store.write_from_path(
        "ws1", "/big.bin", big, "application/octet-stream"
    )  # 5000, blob-backed

    calls: list[int] = []
    orig = disk_store._files.restore_binary

    def _spy(data):
        calls.append(1)
        return orig(data)

    disk_store._files.restore_binary = _spy
    try:
        entries = await disk_store.stat_all("ws1")
    finally:
        disk_store._files.restore_binary = orig
    assert sorted(entries) == [("/a.txt", 5), ("/big.bin", 5000)]
    assert calls == []  # never restored a blob just to size it


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


async def test_ls_with_a_prefix_does_not_materialise_the_whole_workspace(store):
    """`ls(prefix=…)` must scope the QUERY, not fetch every file and filter in
    Python. The entity paths call it ~10 times per interaction
    (`discover_catalog` once per request, `_parse_type` once per type,
    `_corpus` again per type), so an O(whole workspace) listing multiplies
    straight into the request: measured on a disk backend, one `ls` cost 95ms
    at 300 files and 796ms at 3000, while reading a record cost 0.9ms.

    Asserting on rows materialised rather than wall time — the row count IS
    the cost, and a timing assertion would be flaky in CI."""
    for i in range(30):
        await store.write("ws1", f"/notes/n{i}.txt", b"x")
    for i in range(3):
        await store.write("ws1", f"/.entity/issue/records/{i}.md", b"y")

    seen: list[int] = []
    inner = store._files.list_resources

    def counting(*args, **kwargs):
        rows = list(inner(*args, **kwargs))
        seen.append(len(rows))
        return rows

    store._files.list_resources = counting  # type: ignore[assignment]
    try:
        paths = await store.ls("ws1", prefix="/.entity/")
    finally:
        store._files.list_resources = inner  # type: ignore[assignment]

    assert sorted(paths) == [f"/.entity/issue/records/{i}.md" for i in range(3)]
    assert seen == [3], f"fetched {seen} rows to return 3 — the prefix is not in the query"


async def test_ls_prefix_matches_the_same_paths_as_a_python_startswith(store):
    """The pushed-down predicate keeps `startswith` semantics exactly, including
    a prefix that is not a directory boundary — both treat it as a plain string
    prefix, so `/a` matches `/ab`."""
    for path in ("/a", "/ab", "/a/c", "/b"):
        await store.write("ws1", path, b"x")
    everything = await store.ls("ws1")
    assert sorted(await store.ls("ws1", prefix="/a")) == sorted(
        p for p in everything if p.startswith("/a")
    )


def _disk_backend(root) -> BackendConfig:
    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


async def test_prefix_index_ready_is_false_over_rows_written_before_path_was_indexed(tmp_path):
    """The guard that keeps a pod from serving an empty-looking workspace.

    specstar extracts indexed_data at WRITE time, so rows written before `path`
    joined `indexed_fields` do not answer a `path` predicate — `ls(prefix=…)`
    returns nothing for them until `migrate/execute` persists the
    re-extraction. Serving then would show an empty file tree over intact data,
    and a 3-replica RollingUpdate would make it flicker between old and new
    pods, which reads as data loss.

    Writes through a spec registered the OLD way, then opens the SAME on-disk
    store through today's registration — the deploy, in one test."""
    from specstar import Schema
    from specstar.types import IndexableField

    from workspace_app.filestore.specstar_impl import WorkspaceFile, _reindex_only

    old = make_spec(default_user="u", backend=_disk_backend(tmp_path))
    old.add_model(
        Schema(WorkspaceFile, "v2").step(None, _reindex_only, source_type=WorkspaceFile),
        indexed_fields=[
            "workspace_id",
            IndexableField("content.size", index_key="content_size"),
        ],
    )
    old_store = SpecstarFileStore(old)
    for i in range(3):
        await old_store.write("ws1", f"/.entity/issue/records/{i}.md", b"legacy")

    new_store = SpecstarFileStore(make_spec(default_user="u", backend=_disk_backend(tmp_path)))

    # The symptom the guard exists to prevent — intact rows, empty listing.
    assert len(await new_store.ls("ws1")) == 3
    assert await new_store.ls("ws1", prefix="/.entity/") == []
    assert await new_store.prefix_index_ready() is False


async def test_prefix_index_ready_is_true_once_rows_carry_the_path_index(disk_store):
    """Rows written by today's code are extracted at write time, so a deploy
    onto a store with no legacy rows is ready immediately."""
    await disk_store.write("ws1", "/.entity/issue/records/1.md", b"x")
    assert await disk_store.prefix_index_ready() is True


async def test_prefix_index_ready_is_true_on_an_empty_store(disk_store):
    """Nothing to backfill — a fresh deploy must not wedge itself unready."""
    assert await disk_store.prefix_index_ready() is True


async def test_readyz_is_503_until_the_path_index_is_backfilled(tmp_path):
    """The endpoint k8s gates the rollout on. A pod whose store still holds
    rows written before `path` was indexed must NOT accept traffic: it would
    serve empty file trees over intact data, and across a 3-replica
    RollingUpdate the mixed fleet flickers, which reads as data loss."""
    from fastapi.testclient import TestClient
    from specstar import Schema
    from specstar.types import IndexableField

    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.specstar_impl import WorkspaceFile, _reindex_only
    from workspace_app.sandbox.mock import MockSandbox

    old = make_spec(default_user="u", backend=_disk_backend(tmp_path))
    old.add_model(
        Schema(WorkspaceFile, "v2").step(None, _reindex_only, source_type=WorkspaceFile),
        indexed_fields=[
            "workspace_id",
            IndexableField("content.size", index_key="content_size"),
        ],
    )
    await SpecstarFileStore(old).write("ws1", "/a.txt", b"legacy")

    spec = make_spec(default_user="u", backend=_disk_backend(tmp_path))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    with TestClient(app) as client:
        assert client.get("/api/readyz").status_code == 503

    fresh = make_spec(default_user="u")
    app2 = create_app(
        spec=fresh,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(fresh),
        runner=ScriptedAgentRunner([]),
    )
    with TestClient(app2) as client:
        assert client.get("/api/readyz").status_code == 200
