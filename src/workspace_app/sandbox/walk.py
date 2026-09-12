"""The ONE traversal every `walk` runs — breadth-first over directories, with
three reasons to list a directory and not enter it.

The file tree used to be drawn from a full recursive traversal, and a workspace
holding `node_modules/` has twelve thousand entries in it: the shipped walk
issued 2.81 stat calls per entry (measured), each one an NFS round trip, and
the tree took 50 s. The fix is not a faster full walk — a full walk has no
upper bound and a bigger workspace just moves the wait — it is a walk that can
STOP: at a pruned directory, at a depth, at an entry budget. Whatever it did
not enter is reported in `unwalked`, so the caller can draw the folder as a
collapsed node and fetch its contents on demand. Pruned is never hidden.

Directories are the unit of work (one `list_dir` = one `scandir`), so the
budget is checked BETWEEN directories and the overshoot is at most one
directory's worth of entries. `depth=1` therefore never truncates.

`list_dir` is injected. The real sandbox and the durable NFS tree hand in
`os.scandir` (that is where the round trips are saved); an in-memory double
hands in its dict. Same algorithm, same answer, one set of tests — the rule is
not re-decided per implementation.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .protocol import FileEntry, WalkResult


@dataclass(frozen=True)
class Entry:
    """One directory entry as `list_dir` reports it.

    `kind` is what decides the entry's fate:
    - ``file``: a regular file (or a symlink to one — `is_file()` follows links,
      exactly as the recursive walk this replaces did) → a `FileEntry`.
    - ``dir``: a real directory → listed, and entered unless pruned/deep/over budget.
    - ``linkdir``: a symlink to a directory → listed, NEVER entered. The old
      `rglob` did not descend into these either, and entering one on demand
      would follow the link wherever it points — possibly outside the workspace.
    - ``other``: socket / fifo / dangling link → skipped, as before.
    """

    name: str
    kind: str
    size: int = 0
    # The change stamp for a file — whatever the lister can produce cheaply:
    # mtime+size from a real stat, a content hash from an in-memory double.
    version: str = ""


ListDir = Callable[[str], Iterable[Entry]]
"""Given a workspace-relative directory path (``"/"`` or ``"/a/b"``), yield its
entries. Raise `FileNotFoundError` / `NotADirectoryError` for a path that is
not a directory — `walk_tree` reads that as 'nothing here'."""


def should_prune(name: str, prune: Sequence[str]) -> bool:
    """True when a directory called `name` is on the prune list. Patterns are
    directory NAMES, with or without the trailing ``/`` the mirror's ignore
    list spells them with. Only directories are ever tested against this, which
    is what makes 'prune' mean 'not entered' rather than 'not shown'."""
    return any(name == pat.rstrip("/") for pat in prune)


def walk_tree(
    list_dir: ListDir,
    root: str,
    *,
    depth: int | None = None,
    prune: Sequence[str] = (),
    max_entries: int | None = None,
) -> WalkResult:
    """Breadth-first over directories from `root`. See the module docstring."""
    files: list[FileEntry] = []
    dirs: list[str] = []
    unwalked: list[str] = []
    truncated = False
    queue: deque[tuple[str, int]] = deque([(root, 0)])
    seen = 0
    while queue:
        here, level = queue.popleft()
        if max_entries is not None and seen >= max_entries:
            # Over budget: this directory was listed by its parent, so the caller
            # knows it exists; its contents wait for an on-demand fetch.
            unwalked.append(here)
            truncated = True
            continue
        base = "" if here == "/" else here
        try:
            entries = list(list_dir(here))
        except (FileNotFoundError, NotADirectoryError):
            # A root that names nothing lists nothing (what the recursive walk
            # answered, and what a user-typed prefix must get — not a 500); a
            # child that vanished between its parent's listing and its own is
            # still on the tree, just empty.
            continue
        for entry in entries:
            seen += 1
            path = f"{base}/{entry.name}"
            if entry.kind == "file":
                files.append(FileEntry(path=path, size=entry.size, version=entry.version))
            elif entry.kind == "dir":
                dirs.append(path)
                if should_prune(entry.name, prune) or (depth is not None and level + 1 >= depth):
                    unwalked.append(path)
                else:
                    queue.append((path, level + 1))
            elif entry.kind == "linkdir":
                dirs.append(path)
    return WalkResult(files=files, dirs=dirs, unwalked=unwalked, truncated=truncated)


def scandir_lister(cwd: Path) -> ListDir:
    """A `list_dir` over a real directory tree rooted at `cwd`, one `scandir`
    per directory. `is_dir`/`is_file` on a `DirEntry` answer from `d_type` for
    anything that is not a symlink, so a regular file costs the one `stat` its
    size and mtime need, and a directory costs none."""

    def list_dir(rel: str) -> Iterable[Entry]:
        with os.scandir(cwd / rel.lstrip("/")) as it:
            for e in it:
                if e.is_dir():
                    yield Entry(e.name, "linkdir" if e.is_symlink() else "dir")
                elif e.is_file():
                    st = e.stat()
                    # mtime(ns)+size — the change stamp the mirror keys on; unchanged.
                    yield Entry(e.name, "file", st.st_size, f"{st.st_mtime_ns}-{st.st_size}")
                else:
                    yield Entry(e.name, "other")

    return list_dir


def flat_lister(files: Mapping[str, tuple[int, str]], dirs: Iterable[str]) -> ListDir:
    """A `list_dir` over a flat listing — ``{"/a/b.txt": (size, version)}`` plus
    the directory paths that may hold no files — for the implementations whose
    own primitive already returned everything (an in-memory double, a `find`
    run, a durable store's rows). They save no round trips this way, but they
    answer the same question the same way. Every ancestor of a file counts as
    a directory whether or not it was recorded."""
    known: set[str] = set(dirs)
    for path in files:
        parent = path.rpartition("/")[0]
        while parent:
            known.add(parent)
            parent = parent.rpartition("/")[0]

    def children_of(rel: str, paths: Iterable[str]) -> set[str]:
        prefix = "/" if rel == "/" else rel + "/"
        return {
            p[len(prefix) :]
            for p in paths
            if p.startswith(prefix) and "/" not in p[len(prefix) :] and p != prefix
        }

    def list_dir(rel: str) -> Iterable[Entry]:
        if rel != "/" and rel not in known:
            raise FileNotFoundError(rel)
        for name in sorted(children_of(rel, known)):
            yield Entry(name, "dir")
        base = "" if rel == "/" else rel
        for name in sorted(children_of(rel, files)):
            size, version = files[f"{base}/{name}"]
            yield Entry(name, "file", size, version)

    return list_dir
