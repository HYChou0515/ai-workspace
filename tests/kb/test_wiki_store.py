"""WikiFileStore (#50 P1) — a FileStore-protocol backend for a collection's
LLM wiki, one specstar resource per page.

Two structural performance properties are the whole point (plan §5①):
  - per-page: editing one page touches one resource, not the whole wiki;
  - draft writes: repeated edits ``modify()`` in place → no revision bloat.
So the wiki agents can reuse the existing file tools (FileStore protocol)
without the single-blob write amplification of SpecstarFileStore.
"""

from __future__ import annotations

import pytest
from specstar import SpecStar

from workspace_app.filestore.protocol import FileNotFound
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, WikiPage, make_spec


def _spec_with_collection() -> tuple[SpecStar, str]:
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    return spec, cid


async def test_write_then_read_roundtrips():
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    await store.write(cid, "/index.md", b"# Index\n")
    assert await store.read(cid, "/index.md") == b"# Index\n"


async def test_write_from_path_and_read_to_file_roundtrip(tmp_path):
    # #219: the FileStore streaming contract — wiki pages are small, so these
    # delegate to read/write, but the round-trip must hold.
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    src = tmp_path / "src.md"
    src.write_bytes(b"# Streamed\n")
    await store.write_from_path(cid, "/index.md", src, "text/markdown")
    assert await store.read(cid, "/index.md") == b"# Streamed\n"
    dest = tmp_path / "out.md"
    await store.read_to_file(cid, "/index.md", dest)
    assert dest.read_bytes() == b"# Streamed\n"


async def test_read_missing_raises_file_not_found():
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    with pytest.raises(FileNotFound):
        await store.read(cid, "/nope.md")


async def test_each_page_is_its_own_resource_scoped_by_collection():
    spec, cid = _spec_with_collection()
    cid2 = spec.get_resource_manager(Collection).create(Collection(name="c2")).resource_id
    store = WikiFileStore(spec)
    await store.write(cid, "/index.md", b"a")
    await store.write(cid, "/entities/x.md", b"b")
    await store.write(cid2, "/index.md", b"other")

    # ls is scoped per collection (workspace_id = collection id).
    assert sorted(await store.ls(cid)) == ["/entities/x.md", "/index.md"]
    assert await store.ls(cid2) == ["/index.md"]
    # Two pages in cid → two WikiPage resources (per-page, not one blob).
    from specstar import QB

    rm = spec.get_resource_manager(WikiPage)
    rows = [r.data for r in rm.list_resources((QB["collection_id"] == cid).build())]
    assert len(rows) == 2


async def test_overwrite_uses_draft_modify_so_no_revision_bloat():
    """Repeated writes to the same page mutate in place (draft modify),
    so revision history stays flat — the high-churn maintainer edits
    can't explode storage (plan §5①b)."""
    from specstar import QB

    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    for i in range(5):
        await store.write(cid, "/index.md", f"v{i}".encode())
    assert await store.read(cid, "/index.md") == b"v4"

    rm = spec.get_resource_manager(WikiPage)
    [row] = rm.list_resources((QB["collection_id"] == cid).build())
    revs = rm.list_revisions(row.info.resource_id)  # ty: ignore[unresolved-attribute]
    assert len(revs) == 1  # draft modify in place → one revision, not five


async def test_ls_prefix_and_delete():
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    await store.write(cid, "/index.md", b"a")
    await store.write(cid, "/entities/x.md", b"b")
    await store.write(cid, "/entities/y.md", b"c")
    assert sorted(await store.ls(cid, "/entities/")) == ["/entities/x.md", "/entities/y.md"]

    await store.delete(cid, "/entities/x.md")
    assert await store.ls(cid, "/entities/") == ["/entities/y.md"]
    with pytest.raises(FileNotFound):
        await store.read(cid, "/entities/x.md")
    with pytest.raises(FileNotFound):
        await store.delete(cid, "/already-gone.md")


# NOTE: WikiPage declares `Ref("collection", on_delete=cascade)` (intent +
# future-proofing), but collection-delete does NOT currently cascade to its
# children in this specstar config — SourceDoc/DocChunk orphan the same way
# (verified). So that's a pre-existing collection-lifecycle gap across the
# whole KB, not something #50 introduces or scopes; no cleanup test here.
