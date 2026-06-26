from __future__ import annotations

from pathlib import Path
from typing import Protocol


class FileNotFound(LookupError):
    """Raised when a path does not exist in a workspace's file store."""


class FileExists(ValueError):
    """Raised when creating a directory whose path is already a file."""


def dir_ancestors(path: str) -> list[str]:
    """Directory paths above `path`, e.g. /a/b/c.txt → [/a, /a/b]."""
    parts = path.strip("/").split("/")
    return ["/" + "/".join(parts[: i + 1]) for i in range(len(parts) - 1)]


class FileStore(Protocol):
    """Durable per-workspace file storage — the agent's file tools target this,
    and it is decoupled from the sandbox (pure file ops never spin one up).
    Paths are absolute workspace-relative (leading `/`); `workspace_id` scopes
    each call to one workspace. Implement every method below to swap in your own
    backend (S3, a database, …); none require a base class (duck typing).
    """

    async def write(self, workspace_id: str, path: str, data: bytes) -> None:
        """Write `data` to `path`, overwriting any existing file and
        auto-creating ancestor directories."""
        ...

    async def write_from_path(
        self, workspace_id: str, path: str, source: Path, content_type: str | None
    ) -> None:
        """Like `write`, but stream the content from the on-disk file `source`
        instead of taking it as in-memory `bytes` — so a big upload never
        materialises whole in RAM. `content_type` is an optional hint (None ⇒
        let the backend sniff/default)."""
        ...

    async def read(self, workspace_id: str, path: str) -> bytes:
        """Return the bytes at `path`; raise `FileNotFound` if it doesn't exist
        (or is a directory)."""
        ...

    async def read_to_file(self, workspace_id: str, path: str, dest: Path) -> None:
        """Like `read`, but stream the content out to the on-disk file `dest`
        instead of returning it — so restoring a big file into a sandbox never
        holds it whole in RAM. Raises `FileNotFound` if `path` is absent."""
        ...

    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
        """List file paths (not directories) in the workspace, optionally
        restricted to those under `prefix`."""
        ...

    async def exists(self, workspace_id: str, path: str) -> bool:
        """True if a file exists at `path` (directories are reported by
        `is_dir`, not here)."""
        ...

    async def delete(self, workspace_id: str, path: str) -> None:
        """Delete the file at `path`; raise `FileNotFound` if absent. Leaves the
        parent directories intact (use `rmdir` to remove a folder subtree)."""
        ...

    # Directories are first-class so empty folders can exist (no .keep
    # hack). write() auto-creates ancestor dirs; delete() of a file leaves
    # its parent dirs intact; rmdir() removes a dir and everything under it.
    async def mkdir(self, workspace_id: str, path: str) -> None:
        """Create an empty directory at `path` (and ancestors). Raise
        `FileExists` if a file already occupies the path. Idempotent for an
        existing directory."""
        ...

    async def rmdir(self, workspace_id: str, path: str) -> None:
        """Remove the directory at `path` and everything beneath it (files +
        subdirectories)."""
        ...

    async def is_dir(self, workspace_id: str, path: str) -> bool:
        """True if `path` is a directory (explicitly created or implied by a
        file beneath it)."""
        ...

    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]:
        """List directory paths (including empty ones), optionally under
        `prefix` — used to build the file tree."""
        ...
