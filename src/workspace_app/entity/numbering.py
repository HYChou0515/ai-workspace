"""Permanent, never-reused entity numbering (#419 §C / N1-N5).

`N = max(high-water, max existing record) + 1`. The high-water counter lives in
`/.readonly/` (inside the workspace → durable via the mirror; framework-owned →
users can't delete it through normal file tools), so a hard-deleted top record
never lets its number be reissued. Single-pod + single local FS is the
serialization point, so an in-process lock in `EntityStore` is enough — no
specstar, no flock, no CAS file.
"""

from __future__ import annotations

from ..filestore.protocol import FileNotFound, FileStore


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


async def allocate(store: FileStore, workspace_id: str, records_path: str) -> int:
    """Reserve the next number and advance the high-water mark. Callers must
    serialize concurrent allocations for one (workspace, type)."""
    high_water = await _read_high_water(store, workspace_id, records_path)
    existing = await _max_existing(store, workspace_id, records_path)
    number = max(high_water, existing) + 1
    await store.write(workspace_id, _counter_path(records_path), str(number).encode())
    return number
