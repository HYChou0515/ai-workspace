from __future__ import annotations

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
        auto-creating ancestor directories. Marks `path` dirty (see
        `dirty_paths`)."""
        ...

    async def read(self, workspace_id: str, path: str) -> bytes:
        """Return the bytes at `path`; raise `FileNotFound` if it doesn't exist
        (or is a directory)."""
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

    # Dirty-path tracking — drives SandboxSync.flush before each exec, so
    # the sandbox sees the latest FileStore writes the agent just made.
    def dirty_paths(self, workspace_id: str) -> set[str]:
        """The set of paths written/deleted since the last `clear_dirty` —
        SandboxSync pushes exactly these into the sandbox before each `exec`."""
        ...

    def clear_dirty(self, workspace_id: str) -> None:
        """Reset the dirty set (called after SandboxSync has flushed it)."""
        ...
