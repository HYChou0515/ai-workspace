"""Coverage fill for WorkspaceFiles._edit_cas: the text-conflict early return.

When the backing store exposes the CAS hooks and the read-back content does NOT
contain exactly one occurrence of `old`, the edit is a text conflict — it hands
back the current content for the caller to re-base, without ever calling
write_cas (facade.py line 211).
"""

from __future__ import annotations

from workspace_app.files import WorkspaceFiles


class _CasStoreMissingText:
    """A CAS store whose content does not contain `old` at all → the edit is a
    text conflict on the very first read, so write_cas must never run."""

    content = b"Zone 5 at 300C.\n"
    etag = "v1"
    write_calls = 0

    async def read_with_etag(self, ws, path):
        return (self.content, self.etag)

    async def write_cas(self, ws, path, data, expected):  # pragma: no cover
        self.write_calls += 1
        raise AssertionError("write_cas must not run on a text conflict")


async def test_edit_cas_text_not_found_returns_current_without_writing():
    store = _CasStoreMissingText()
    files = WorkspaceFiles(store)  # ty: ignore[invalid-argument-type] — duck-typed CAS store
    # `old` ("245C") is absent → count != 1 → return current content as conflict.
    result = await files.edit("ws", "/p.md", "245C", "250C")
    assert result == "Zone 5 at 300C.\n"
    assert store.write_calls == 0


class _CasStoreAmbiguousText:
    """`old` appears twice → still count != 1 → the same conflict return."""

    content = b"245C and again 245C\n"
    etag = "v1"

    async def read_with_etag(self, ws, path):
        return (self.content, self.etag)

    async def write_cas(self, ws, path, data, expected):  # pragma: no cover
        raise AssertionError("write_cas must not run on an ambiguous match")


async def test_edit_cas_ambiguous_match_returns_current_without_writing():
    files = WorkspaceFiles(_CasStoreAmbiguousText())  # ty: ignore[invalid-argument-type]
    result = await files.edit("ws", "/p.md", "245C", "250C")
    assert result == "245C and again 245C\n"
