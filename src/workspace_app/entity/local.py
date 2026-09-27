"""Entities read from a workspace directory — a view plugin's `source: {entity: …}`.

A view plugin's sandbox half runs in the item's sandbox, where the workspace is
the current directory and the API is not reachable. Parsing `N.md` there with
code of its own would be a second entity parser to keep in step with this one,
so the reader is THIS package's catalog + store over a `FileStore` that reads a
directory: `read_entity_records(root, type)` is `EntityStore.query` by
construction (`tests/entity/test_local.py` holds it to the store over the same
files). The modules it needs import nothing heavier than msgspec and PyYAML, so
a plugin bundle vendors them (`view-plugins/sdk-python`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, cast

from ..filestore.protocol import FileNotFound, FileStore
from .catalog import discover_catalog
from .store import EntityStore

_WORKSPACE = "local"


class DirFileStore:
    """The read side of a `FileStore` over a directory: paths are
    workspace-absolute (`/issues/1.md`), as every store keys them."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _file(self, path: str) -> Path:
        return self._root / path.lstrip("/")

    async def read(self, workspace_id: str, path: str) -> bytes:
        try:
            return self._file(path).read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as e:
            raise FileNotFound(f"{workspace_id}:{path}") from e

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        # Walk only the directory the prefix names: the workspace may hold a
        # node_modules/ the entity reader has no business listing.
        base = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        start = self._file(base)
        out: list[str] = []
        for dirpath, _dirs, files in os.walk(start):
            rel = Path(dirpath).relative_to(self._root).as_posix()
            head = "/" if rel == "." else f"/{rel}/"
            out.extend(p for f in files if (p := head + f).startswith(prefix))
        return sorted(out)


async def _records(root: Path, type_name: str) -> list[dict[str, Any]]:
    # Read-only on purpose: the catalog and `query` only ever `ls` and `read`,
    # and a plugin has no business writing records behind the store's numbering.
    store = cast(FileStore, DirFileStore(root))
    catalog, _ = await discover_catalog(store, _WORKSPACE)
    if type_name not in catalog:
        names = catalog.names()
        have = f"types: {', '.join(names)}" if names else "no entity types in .entity/"
        raise LookupError(f"entity type {type_name!r} is not defined here ({have})")
    result = await EntityStore(store, _WORKSPACE, catalog).query(type_name)
    return [{"number": e.number, **e.fields} for e in result.entities]


def read_entity_records(root: Path, type_name: str) -> list[dict[str, Any]]:
    """`type_name`'s records under `root` as the entity views project them
    (records that fail to parse are left out), each `{"number": n, **fields}`.
    Raises `LookupError` naming the defined types when `type_name` is not one."""
    return asyncio.run(_records(root, type_name))
