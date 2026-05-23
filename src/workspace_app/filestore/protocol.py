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
    async def write(self, workspace_id: str, path: str, data: bytes) -> None: ...
    async def read(self, workspace_id: str, path: str) -> bytes: ...
    async def ls(self, workspace_id: str, prefix: str = "") -> list[str]: ...
    async def exists(self, workspace_id: str, path: str) -> bool: ...
    async def delete(self, workspace_id: str, path: str) -> None: ...

    # Directories are first-class so empty folders can exist (no .keep
    # hack). write() auto-creates ancestor dirs; delete() of a file leaves
    # its parent dirs intact; rmdir() removes a dir and everything under it.
    async def mkdir(self, workspace_id: str, path: str) -> None: ...
    async def rmdir(self, workspace_id: str, path: str) -> None: ...
    async def is_dir(self, workspace_id: str, path: str) -> bool: ...
    async def listdir(self, workspace_id: str, prefix: str = "") -> list[str]: ...

    # Dirty-path tracking — drives SandboxSync.flush before each exec, so
    # the sandbox sees the latest FileStore writes the agent just made.
    def dirty_paths(self, workspace_id: str) -> set[str]: ...
    def clear_dirty(self, workspace_id: str) -> None: ...
