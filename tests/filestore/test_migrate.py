import contextlib
from urllib.parse import quote

from workspace_app.filestore.migrate import _WorkspaceFiles, migrate_inline_to_binary
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec


def _seed_legacy(spec, workspace_id, files, dirs):
    with contextlib.suppress(ValueError):
        spec.add_model(_WorkspaceFiles)
    rm = spec.get_resource_manager(_WorkspaceFiles)
    rm.create(
        _WorkspaceFiles(workspace_id=workspace_id, files=files, dirs=dirs),
        resource_id=quote(workspace_id, safe=""),
    )


async def test_migrate_inline_record_to_per_file_binary():
    spec = make_spec(default_user="u")
    _seed_legacy(
        spec,
        "ws1",
        files={"/a.txt": b"hello", "/d/b.txt": b"bee"},
        dirs=["/d", "/empty"],
    )

    assert migrate_inline_to_binary(spec) == 1

    store = SpecstarFileStore(spec)
    assert await store.read("ws1", "/a.txt") == b"hello"
    assert await store.read("ws1", "/d/b.txt") == b"bee"
    assert sorted(await store.ls("ws1")) == ["/a.txt", "/d/b.txt"]
    # explicit empty dir + ancestor dirs preserved
    assert "/empty" in await store.listdir("ws1")
    assert "/d" in await store.listdir("ws1")


async def test_migrate_is_idempotent_after_clean_run():
    spec = make_spec(default_user="u")
    _seed_legacy(spec, "ws1", files={"/a": b"A"}, dirs=[])
    assert migrate_inline_to_binary(spec) == 1
    # legacy rows consumed → a second run migrates nothing
    assert migrate_inline_to_binary(spec) == 0


async def test_migrate_no_legacy_data_is_noop():
    spec = make_spec(default_user="u")
    assert migrate_inline_to_binary(spec) == 0
