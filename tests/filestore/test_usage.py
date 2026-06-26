"""Per-workspace logical usage — the `Sum(content.size)` aggregate the #245
quota gates on. Behaviour, through the public `SpecstarFileStore` interface."""

from specstar import BackendBinding, BackendConfig, ConnectionProfile, SpecStar
from specstar.types import Binary

from workspace_app.filestore.specstar_impl import (
    SpecstarFileStore,
    WorkspaceFile,
    _fid,
)


def _disk_backend(root) -> BackendConfig:
    return BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(root)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )


async def test_empty_workspace_has_zero_usage(store: SpecstarFileStore):
    assert await store.workspace_usage("ws1") == 0


async def test_usage_sums_every_file(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x" * 100)
    await store.write("ws1", "/d/b", b"y" * 250)
    assert await store.workspace_usage("ws1") == 350


async def test_usage_is_scoped_per_workspace(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x" * 100)
    await store.write("ws2", "/a", b"y" * 9)
    assert await store.workspace_usage("ws1") == 100
    assert await store.workspace_usage("ws2") == 9


async def test_overwrite_counts_the_new_size_not_the_sum(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x" * 500)
    await store.write("ws1", "/a", b"y" * 50)  # replace, not append
    assert await store.workspace_usage("ws1") == 50


async def test_delete_drops_usage(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x" * 500)
    await store.write("ws1", "/b", b"y" * 50)
    await store.delete("ws1", "/a")
    assert await store.workspace_usage("ws1") == 50


async def test_file_size_reads_one_file_or_none(store: SpecstarFileStore):
    await store.write("ws1", "/a", b"x" * 300)
    assert await store.file_size("ws1", "/a") == 300  # point read, not the bytes
    assert await store.file_size("ws1", "/missing") is None


async def test_pre_index_rows_undercount_then_backfill_via_migrate(tmp_path):
    """A row written before `content_size` was indexed (version None) doesn't
    sum — usage under-counts it as 0 (not a crash on the None aggregate) — until
    the operator runs the migrate route, which re-extracts its indexed_data."""
    backend = _disk_backend(tmp_path)
    # Pre-#245 shape: workspace_id indexed, no content_size, no Schema.
    spec_old = SpecStar()
    spec_old.configure(default_user="u", backend=backend)
    spec_old.add_model(WorkspaceFile, indexed_fields=["workspace_id"])
    rid = _fid("ws1", "/a")
    spec_old.get_resource_manager(WorkspaceFile).create(
        WorkspaceFile(workspace_id="ws1", path="/a", content=Binary(data=b"x" * 500)),
        resource_id=rid,
    )

    # New code (content_size index + Schema) on the SAME store.
    spec_new = SpecStar()
    spec_new.configure(default_user="u", backend=backend)
    store = SpecstarFileStore(spec_new)
    assert await store.workspace_usage("ws1") == 0  # under-counts, no crash

    spec_new.get_resource_manager(WorkspaceFile).migrate(rid)  # operator backfill
    assert await store.workspace_usage("ws1") == 500
