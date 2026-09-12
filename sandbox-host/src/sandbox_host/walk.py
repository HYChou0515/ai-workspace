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
            # A root that names nothing lists nothing — what the recursive walk
            # answered, and what a user-typed prefix must get, not a 500. (A
            # real-directory lister handles its own errors per entry; this is
            # for a flat lister asked about a path it has no record of.)
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

    def classify(e: os.DirEntry[str]) -> Entry:
        if e.is_dir():
            return Entry(e.name, "linkdir" if e.is_symlink() else "dir")
        if e.is_file():
            st = e.stat()
            # mtime(ns)+size — the change stamp the mirror keys on; unchanged.
            return Entry(e.name, "file", st.st_size, f"{st.st_mtime_ns}-{st.st_size}")
        return Entry(e.name, "other")

    def list_dir(rel: str) -> Iterable[Entry]:
        # A directory we cannot open — gone, not a directory, unreadable — is
        # still on the tree (its parent listed it); there is simply nothing to
        # show under it. `os.walk` and `Path.walk` do the same, and the walk
        # this replaced rode on them: one unreadable folder never stopped it.
        try:
            it = os.scandir(cwd / rel.lstrip("/"))
        except OSError:
            return
        with it:
            for e in it:
                # Per ENTRY, never per directory: a cyclic link (ELOOP) or a
                # file unlinked between the readdir and its stat drops that
                # one entry, exactly as `Path.is_dir()` swallowing the error
                # did. Dropping the whole listing instead would answer "this
                # folder is empty" — and the mirror would delete its durable
                # copies of every sibling.
                try:
                    yield classify(e)
                except OSError:
                    continue

    return list_dir


def flat_lister(files: Mapping[str, tuple[int, str]], dirs: Iterable[str]) -> ListDir:
    """A `list_dir` over a flat listing — ``{"/a/b.txt": (size, version)}`` plus
    the directory paths that may hold no files — for the implementations whose
    own primitive already returned everything (an in-memory double, a `find`
    run, a durable store's rows). They save no round trips this way, but they
    answer the same question the same way. Every ancestor of a file counts as
    a directory whether or not it was recorded."""
    # Index the listing ONCE: each directory's own subdirectories and files.
    # Every ancestor of a file OR a recorded directory is a directory too — a
    # store that recorded `/a/b` without `/a` (an orphan row) must still let
    # the walk reach `/a/b`, as the old listing (which never walked) did.
    # Scanning the whole listing per directory instead made a 12k-file tree
    # cost a second and a 50k one fifteen — on the request path of the cold
    # store branch.
    subdirs: dict[str, set[str]] = {"/": set()}
    subfiles: dict[str, list[str]] = {}
    # Canonical path -> the key the listing spelled it with. An in-memory
    # sandbox stores whatever it was handed (`pyproject.toml`, `//x`); the
    # old per-directory scan silently skipped such keys, an index must not
    # KeyError on them — and a `//` directory must not become a child named
    # "" whose path is the root again, walked forever.
    entry_key: dict[str, str] = {}

    def canonical(path: str) -> str:
        return "/" + "/".join(seg for seg in path.split("/") if seg)

    def record_dir(path: str) -> None:
        # Hang `path` under its parent, then the parent under ITS parent, and
        # stop at the first ancestor already hung — everything above it is.
        while path and path != "/":
            parent, _, name = path.rpartition("/")
            parent = parent or "/"
            siblings = subdirs.setdefault(parent, set())
            subdirs.setdefault(path, set())
            if name in siblings:
                break
            siblings.add(name)
            path = parent

    for d in dirs:
        record_dir(canonical(d))
    for key in files:
        path = canonical(key)
        if path == "/":
            continue
        parent, _, name = path.rpartition("/")
        record_dir(parent)
        if path not in entry_key:
            subfiles.setdefault(parent or "/", []).append(name)
        entry_key[path] = key

    def list_dir(rel: str) -> Iterable[Entry]:
        if rel not in subdirs:
            raise FileNotFoundError(rel)
        for name in sorted(subdirs[rel]):
            yield Entry(name, "dir")
        base = "" if rel == "/" else rel
        for name in sorted(subfiles.get(rel, ())):
            size, version = files[entry_key[f"{base}/{name}"]]
            yield Entry(name, "file", size, version)

    return list_dir
