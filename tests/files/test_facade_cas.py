"""WorkspaceFiles.edit optimistic-concurrency path (#50 cross-worker hardening).

When the backing store exposes ``read_with_etag`` + ``write_cas`` and no sandbox
is live, edit runs an etag-guarded read→write→retry loop, so a writer in another
process can't be silently clobbered. Stores without those hooks keep the
lock-only behaviour (covered by test_facade.py).
"""

from __future__ import annotations

from workspace_app.files import WorkspaceFiles
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, make_spec


async def test_edit_over_a_cas_store_applies_and_persists():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    store = WikiFileStore(spec)
    await store.write(cid, "/index.md", b"Zone 3 at 245C.\n")

    files = WorkspaceFiles(store)  # no sandbox → CAS path
    assert await files.edit(cid, "/index.md", "245C", "250C") is None
    assert (await store.read(cid, "/index.md")).decode() == "Zone 3 at 250C.\n"


class _FlakyCas:
    """write_cas fails once (a concurrent writer won the race), then succeeds —
    so edit must re-read and retry rather than give up or clobber."""

    def __init__(self) -> None:
        self.content = b"hello world"
        self.etag = "v1"
        self.cas_calls = 0

    async def read_with_etag(self, ws, path):
        return (self.content, self.etag)

    async def write_cas(self, ws, path, data, expected):
        self.cas_calls += 1
        if self.cas_calls == 1:
            self.etag = "v2"  # someone else moved it; `expected` is now stale
            return False
        self.content = data
        return True


async def test_edit_retries_against_a_concurrent_writer():
    fake = _FlakyCas()
    files = WorkspaceFiles(fake)  # ty: ignore[invalid-argument-type] — duck-typed CAS store
    assert await files.edit("ws", "/p.md", "hello", "HELLO") is None
    assert fake.cas_calls == 2  # one failed attempt, one success
    assert fake.content == b"HELLO world"


class _AlwaysContended:
    content = b"hello world"
    etag = "v"

    async def read_with_etag(self, ws, path):
        return (self.content, self.etag)

    async def write_cas(self, ws, path, data, expected):
        return False  # never wins


async def test_edit_reports_a_conflict_under_persistent_contention():
    files = WorkspaceFiles(_AlwaysContended())  # ty: ignore[invalid-argument-type]
    # Exhausts retries → hands back the current content so the agent re-bases.
    assert await files.edit("ws", "/p.md", "hello", "HI") == "hello world"


class _MissingPage:
    async def read_with_etag(self, ws, path):
        return None

    async def write_cas(self, ws, path, data, expected):
        raise AssertionError("should not write a missing page")


async def test_edit_of_a_missing_page_returns_empty():
    files = WorkspaceFiles(_MissingPage())  # ty: ignore[invalid-argument-type]
    assert await files.edit("ws", "/gone.md", "a", "b") == ""
