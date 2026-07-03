"""Permanent, never-reused entity numbering (#419 §C / N1-N5).

`N = max(high-water, max existing record) + 1`, then the record file is claimed
by **exclusive create** — `create_exclusive(records/N.md)` succeeds for exactly
one caller; a loser gets `FileExists` and walks to the next free number (N1: the
entity file itself is the token, not a counter overwrite that could lost-update).
The high-water counter in `/.readonly/` (inside the workspace → durable via the
mirror; framework-owned → users can't delete it through normal file tools) is
what stops a hard-deleted top record's number from ever being reissued (§N2).
Single-pod + single local FS is the serialization point (§N5), so no specstar,
flock, or CAS file is needed — the FS's own exclusive-create is the arbiter.
"""

from __future__ import annotations

from ..filestore.protocol import FileExists, FileNotFound, FileStore


def _counter_path(records_path: str) -> str:
    return f"/.readonly/entity/{records_path}.seq"


async def _max_existing(store: FileStore, workspace_id: str, records_path: str) -> int:
    paths = await store.ls(workspace_id, prefix=f"/{records_path}/")
    highest = 0
    for path in paths:
        stem = path.rsplit("/", 1)[-1].removesuffix(".md")
        if stem.isdigit():
            highest = max(highest, int(stem))
    return highest


async def _read_high_water(store: FileStore, workspace_id: str, records_path: str) -> int:
    try:
        raw = await store.read(workspace_id, _counter_path(records_path))
    except FileNotFound:
        return 0
    text = raw.decode("utf-8", errors="replace").strip()
    return int(text) if text.isdigit() else 0


async def next_number(store: FileStore, workspace_id: str, records_path: str) -> int:
    """The first candidate number to try — `max(high-water, max existing) + 1`.
    A read only (no write): the number isn't reserved until the record file is
    exclusively created, so a race is resolved by `create_exclusive`, not here."""
    high_water = await _read_high_water(store, workspace_id, records_path)
    existing = await _max_existing(store, workspace_id, records_path)
    return max(high_water, existing) + 1


async def record_high_water(
    store: FileStore, workspace_id: str, records_path: str, number: int
) -> None:
    """Advance the durable high-water mark after a number is claimed, so a later
    hard-delete of the top record can't let its number be reissued (§N2). Only
    ever moves forward (callers pass the just-claimed number)."""
    await store.write(workspace_id, _counter_path(records_path), str(number).encode())


async def create_exclusive(store: FileStore, workspace_id: str, path: str, data: bytes) -> None:
    """Create `path` with `data` iff it doesn't already exist, else raise
    `FileExists` — the numbering arbiter (N1). Uses the store's native
    `create_exclusive` capability when present (atomic: MemoryFileStore under its
    lock, SpecstarFileStore via a create-only resource, WorkspaceFiles routing to
    the sandbox / durable store). Falls back to a non-atomic exists+write for a
    store without the capability (correct under the single-process lock, §N5)."""
    native = getattr(store, "create_exclusive", None)
    if native is not None:
        await native(workspace_id, path, data)
        return
    if await store.exists(workspace_id, path):
        raise FileExists(path)
    await store.write(workspace_id, path, data)
