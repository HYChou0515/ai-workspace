"""Which files make up a job, and what makes two jobs the same.

A job is a folder of the workspace (docs/plan-job-queue.md D3). What it runs,
which packages it installs, its variables — all of it is files in the folder,
read by the backend by its own rules; the tool reads none of them. It only
decides which files are sent (D13) and derives the job's identity from their
content (D11).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pathspec

#: Folders never sent, wherever they sit: version control, environments the
#: other side rebuilds, and caches. Each is large, rebuilt, or both — what
#: turns a folder of a few KB into gigabytes.
SKIPPED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".ipynb_checkpoints",
        ".tox",
    }
)
SKIPPED_SUFFIXES = (".pyc",)

_CHUNK = 1 << 20


@dataclass(frozen=True)
class PackedFile:
    path: str
    """Relative to the folder, with `/` between parts."""
    size: int
    sha256: str


@dataclass(frozen=True)
class Packed:
    files: tuple[PackedFile, ...]
    """Sorted by path."""
    skipped: tuple[str, ...]
    """Links in the folder, which are not followed — reported so the model
    knows they did not go."""

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _gitignore(root: Path) -> pathspec.GitIgnoreSpec | None:
    """The folder's own `.gitignore`. Only the top one: a nested `.gitignore`
    is read by git, not here."""
    path = root / ".gitignore"
    if not path.is_file() or path.is_symlink():
        return None
    return pathspec.GitIgnoreSpec.from_lines(path.read_text("utf-8", errors="replace").splitlines())


def pack(workdir: Path, *, never: Path | None = None) -> Packed:
    """The files of `workdir` that a job sends. `never` is a folder that is not
    sent even when it lies inside (the workspace's own job records)."""
    root = workdir.resolve()
    excluded = never.resolve() if never is not None else None
    ignore = _gitignore(root)
    files: list[PackedFile] = []
    skipped: list[str] = []

    for current, dirs, names in os.walk(root, followlinks=False):
        here = Path(current)
        rel_here = here.relative_to(root)
        kept_dirs = []
        for name in sorted(dirs):
            full = here / name
            rel = (rel_here / name).as_posix()
            if full.is_symlink():
                skipped.append(rel)
            elif name in SKIPPED_DIRS or full == excluded:
                continue
            elif ignore is not None and ignore.match_file(rel + "/"):
                continue
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs

        for name in sorted(names):
            full = here / name
            rel = (rel_here / name).as_posix()
            if full.is_symlink():
                skipped.append(rel)
            elif name.endswith(SKIPPED_SUFFIXES) or not full.is_file():
                continue
            elif ignore is not None and ignore.match_file(rel):
                continue
            else:
                files.append(PackedFile(rel, full.stat().st_size, _sha256(full)))

    files.sort(key=lambda f: f.path)
    return Packed(tuple(files), tuple(sorted(skipped)))


def job_hash(packed: Packed, resources: dict[str, Any], item_id: str | None) -> str:
    """What makes two requests the same job (D11): the item, the resources
    asked for, and every file's path and content — never when it was written.
    With no item (the MCP runner) the rest decides, and None is not ""."""
    canonical = json.dumps(
        [item_id, resources, [[f.path, f.size, f.sha256] for f in packed.files]],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
