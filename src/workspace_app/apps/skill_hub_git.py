"""The skill hub's version store: one bare git repo per entry
(``docs/plan-skill-hub-history.md`` §3.1, §4).

``refs/heads/master`` is the entry's current version; every revision of the
entry's specstar row is tagged ``r-<revision>`` at the commit it pointed at, so
a revision names its commit (``SkillHubEntry.commit``) and a commit names the
revisions it was current in. Binary types go to LFS by a FIXED list of path
patterns written into every commit's ``.gitattributes`` — the LFS way, by path,
never by size — with the objects kept beside the repo in ``lfs/objects``. No
``git-lfs`` binary is involved: the platform writes the pointer files and the
objects itself, in the layout ``git lfs`` reads.

This module is the only place that runs ``git``. Everything above it asks for
files, trees and commits; nothing above it knows a command line.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import posixpath
import re
import time
import uuid
from collections.abc import Collection, Mapping
from pathlib import Path
from urllib.parse import quote, unquote

from msgspec import Struct

#: The paths stored as LFS pointers, as `.gitattributes` patterns. A pattern
#: with no slash matches the file name at any depth, case-sensitively — git's
#: own rule, which `is_lfs_path` follows. ONE list: the attributes file and the
#: rule are both built from it.
LFS_PATTERNS: tuple[str, ...] = (
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.pdf",
    "*.docx",
    "*.xlsx",
    "*.pptx",
    "*.zip",
    "*.gz",
    "*.tar",
)

#: Written into every commit so the repo is a standard LFS repo: a later
#: `git clone` + `git lfs pull` reads it as one.
GITATTRIBUTES: bytes = "".join(
    f"{pattern} filter=lfs diff=lfs merge=lfs -text\n" for pattern in LFS_PATTERNS
).encode()

_POINTER_VERSION = b"version https://git-lfs.github.com/spec/v1\n"


def git_blob_id(data: bytes) -> str:
    """The id git gives these bytes as a blob (`git hash-object`)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def is_lfs_path(path: str) -> bool:
    """Whether the file at `path` (relative to the skill folder) is stored in LFS."""
    name = posixpath.basename(path)
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in LFS_PATTERNS)


def lfs_pointer(data: bytes) -> bytes:
    """The LFS pointer file that stands for `data` in a tree."""
    oid = hashlib.sha256(data).hexdigest()
    return _POINTER_VERSION + f"oid sha256:{oid}\nsize {len(data)}\n".encode()


def parse_lfs_pointer(blob: bytes) -> tuple[str, int] | None:
    """``(sha256, size)`` when `blob` is an LFS pointer, else ``None``."""
    if not blob.startswith(_POINTER_VERSION):
        return None
    fields: dict[str, str] = {}
    for line in blob[len(_POINTER_VERSION) :].decode("ascii", errors="replace").splitlines():
        key, _, value = line.partition(" ")
        fields[key] = value
    oid, size = fields.get("oid", ""), fields.get("size", "")
    if not oid.startswith("sha256:") or not size.isdigit():
        return None
    return oid.removeprefix("sha256:"), int(size)


# ── the repos ────────────────────────────────────────────────────────────────

#: An LFS pointer is ~130 bytes; anything a good deal bigger is never one, so
#: only blobs at or under this are read to ask (G16 — by the blob, not by
#: parsing `.gitattributes`).
_POINTER_MAX = 512
_ENTRY_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_TAG_PREFIX = "refs/tags/r-"


class GitError(RuntimeError):
    """A git command failed in a way the caller did not ask about."""


class TreeFile(Struct, frozen=True):
    """One file of a version, without its bytes: enough to tell whether some
    bytes ARE this file (`same_content`)."""

    blob_id: str
    size: int
    #: ``(sha256, size)`` of the content when the tree holds an LFS pointer.
    lfs: tuple[str, int] | None = None


def same_content(file: TreeFile, data: bytes) -> bool:
    """Whether `data` is the content `file` stands for — by blob id, or by the
    pointer's sha256 and size for an LFS file (G14, G16)."""
    if file.lfs is not None:
        oid, size = file.lfs
        return len(data) == size and hashlib.sha256(data).hexdigest() == oid
    return git_blob_id(data) == file.blob_id


def _quote_path(path: str) -> bytes:
    """A path as fast-import's C-style quoted string — always quoted, so a
    space, a quote or a non-ASCII name needs no second rule."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'.encode()


def _who(name: str) -> bytes:
    """`name <>` for a fast-import ident line; `<`, `>` and newlines cannot appear."""
    clean = re.sub(r"[<>\n]", "", name).strip() or "skill-hub"
    return f"{clean} <> {int(time.time())} +0000".encode()


class SkillHubRepos:
    """The bare repos under ``skill_hub.git_root`` — one per entry, created on
    its first version. Every method is a git command run off the event loop."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def path(self, entry_id: str) -> Path:
        if not _ENTRY_ID.match(entry_id):
            raise ValueError(f"not an entry id: {entry_id!r}")
        return self._root / f"{entry_id}.git"

    async def _git(
        self, entry_id: str, *args: str, stdin: bytes | None = None, check: bool = True
    ) -> tuple[int, bytes]:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.path(entry_id)),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, err = await proc.communicate(stdin)
        code = proc.returncode or 0
        if check and code != 0:
            raise GitError(f"git {args[0]} failed ({code}): {err.decode(errors='replace').strip()}")
        return code, out

    async def _ensure(self, entry_id: str) -> None:
        repo = self.path(entry_id)
        if (repo / "HEAD").exists():
            return
        repo.parent.mkdir(parents=True, exist_ok=True)
        # `init` on a repo another pod just made re-initializes it harmlessly.
        proc = await asyncio.create_subprocess_exec(
            "git", "init", "--bare", "-q", str(repo),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        _out, err = await proc.communicate()
        if proc.returncode:
            raise GitError(f"git init failed: {err.decode(errors='replace').strip()}")

    def _lfs_object(self, entry_id: str, oid: str) -> Path:
        return self.path(entry_id) / "lfs" / "objects" / oid[:2] / oid[2:4] / oid

    def _store_lfs(self, entry_id: str, data: bytes) -> None:
        oid = hashlib.sha256(data).hexdigest()
        target = self._lfs_object(entry_id, oid)
        if target.exists():  # content-addressed: the same bytes are already there
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{oid}.{uuid.uuid4().hex}")
        tmp.write_bytes(data)
        tmp.replace(target)

    # ── write ────────────────────────────────────────────────────────────

    async def write_version(
        self,
        entry_id: str,
        payload: Mapping[str, bytes],
        *,
        parent: str | None,
        author: str,
        message: str,
    ) -> str:
        """Write `payload` as one commit on top of `parent` and return its id.
        Replace, not merge: a file the payload lacks is not in the commit.
        Master does NOT move — that is `move_master`, the lock (G5)."""
        await self._ensure(entry_id)
        staging = f"refs/skill-hub-staging/{uuid.uuid4().hex}"
        stream = bytearray()
        files: list[tuple[str, int]] = []
        for mark, (rel, data) in enumerate(sorted(payload.items()), start=1):
            if is_lfs_path(rel):
                await asyncio.to_thread(self._store_lfs, entry_id, data)
                blob = lfs_pointer(data)
            else:
                blob = data
            stream += b"blob\nmark :%d\ndata %d\n" % (mark, len(blob)) + blob + b"\n"
            files.append((rel, mark))
        attributes_mark = len(files) + 1
        stream += b"blob\nmark :%d\ndata %d\n" % (attributes_mark, len(GITATTRIBUTES))
        stream += GITATTRIBUTES + b"\n"
        msg = message.encode()
        stream += f"commit {staging}\n".encode()
        stream += b"author " + _who(author) + b"\ncommitter " + _who(author) + b"\n"
        stream += b"data %d\n" % len(msg) + msg + b"\n"
        if parent:
            stream += f"from {parent}\n".encode()
        stream += b"deleteall\n"
        stream += b"M 100644 :%d " % attributes_mark + _quote_path(".gitattributes") + b"\n"
        for rel, mark in files:
            stream += b"M 100644 :%d " % mark + _quote_path(rel) + b"\n"
        stream += b"\ndone\n"
        await self._git(entry_id, "fast-import", "--quiet", "--done", stdin=bytes(stream))
        _code, out = await self._git(entry_id, "rev-parse", staging)
        await self._git(entry_id, "update-ref", "-d", staging)
        return out.decode().strip()

    async def move_master(self, entry_id: str, commit: str, *, expected: str | None) -> bool:
        """Point master at `commit` IF it still is `expected` (``None``: master
        must not exist yet). ``False`` means someone moved it first — the lock
        was lost (G5). A push to the repo itself, with a lease."""
        lease = f"refs/heads/master:{expected or ''}"
        code, _out = await self._git(
            entry_id,
            "push",
            "--quiet",
            f"--force-with-lease={lease}",
            ".",
            f"{commit}:refs/heads/master",
            check=False,
        )
        return code == 0

    async def tag(self, entry_id: str, revision_id: str, commit: str) -> None:
        """Tag `commit` with the specstar revision it is current in (G7). A
        revision id may carry `:`, which a ref cannot; it is percent-encoded,
        so the tag reads back to exactly that id."""
        ref = _TAG_PREFIX + quote(revision_id, safe="-._")
        await self._git(entry_id, "update-ref", ref, commit)

    # ── read ─────────────────────────────────────────────────────────────

    async def master(self, entry_id: str) -> str | None:
        if not (self.path(entry_id) / "HEAD").exists():
            return None
        code, out = await self._git(
            entry_id, "rev-parse", "--verify", "-q", "refs/heads/master", check=False
        )
        return out.decode().strip() if code == 0 else None

    async def tagged_revisions(self, entry_id: str) -> dict[str, str]:
        """``{revision id: commit}`` for every revision tag."""
        _code, out = await self._git(
            entry_id, "for-each-ref", "--format=%(refname) %(objectname)", _TAG_PREFIX + "*"
        )
        tags: dict[str, str] = {}
        for line in out.decode().splitlines():
            ref, _, commit = line.partition(" ")
            tags[unquote(ref.removeprefix(_TAG_PREFIX))] = commit
        return tags

    async def _cat(self, entry_id: str, blob_ids: Collection[str]) -> dict[str, bytes]:
        """The bytes of each blob — one `cat-file --batch` for all of them."""
        wanted = list(dict.fromkeys(blob_ids))
        if not wanted:
            return {}
        _code, out = await self._git(
            entry_id, "cat-file", "--batch", stdin="".join(f"{b}\n" for b in wanted).encode()
        )
        blobs: dict[str, bytes] = {}
        at = 0
        for blob_id in wanted:
            header_end = out.index(b"\n", at)
            _oid, _kind, size = out[at:header_end].decode().split(" ")
            start = header_end + 1
            blobs[blob_id] = bytes(out[start : start + int(size)])
            at = start + int(size) + 1
        return blobs

    async def tree(self, entry_id: str, commit: str) -> dict[str, TreeFile]:
        """Every file of a version without reading its bytes — except the
        small blobs, read once together to tell an LFS pointer from a file
        (G16). The platform's `.gitattributes` is not the skill's and is left out."""
        _code, out = await self._git(entry_id, "ls-tree", "-r", "-l", "-z", commit)
        listed: dict[str, tuple[str, int]] = {}
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, _, raw_path = record.partition(b"\t")
            _mode, kind, blob_id, size = meta.decode().split()
            path = raw_path.decode()
            if kind == "blob" and path != ".gitattributes":
                listed[path] = (blob_id, int(size))
        small = await self._cat(
            entry_id, [blob for blob, size in listed.values() if size <= _POINTER_MAX]
        )
        return {
            path: TreeFile(
                blob_id=blob,
                size=size,
                lfs=parse_lfs_pointer(small[blob]) if blob in small else None,
            )
            for path, (blob, size) in listed.items()
        }

    async def read(
        self, entry_id: str, commit: str, *, paths: Collection[str] | None = None
    ) -> dict[str, bytes]:
        """The files of a version (all of them, or only `paths`), LFS files as
        their content rather than their pointers."""
        tree = await self.tree(entry_id, commit)
        chosen = {p: f for p, f in tree.items() if paths is None or p in paths}
        plain = await self._cat(entry_id, [f.blob_id for f in chosen.values() if f.lfs is None])
        out: dict[str, bytes] = {}
        for path, file in chosen.items():
            if file.lfs is None:
                out[path] = plain[file.blob_id]
            else:
                out[path] = await asyncio.to_thread(
                    self._lfs_object(entry_id, file.lfs[0]).read_bytes
                )
        return out


def resolve_git_root(git_root: str, *, durable: bool) -> Path:
    """The directory the repos live in (G2). Set → used as is. Unset → a
    throwaway dir, but only where nothing else survives a restart either
    (`durable` False); a durable deploy refuses, because every row's `commit`
    would name a repo the next boot no longer has."""
    if git_root:
        return Path(git_root)
    if durable:
        raise ValueError(
            "skill_hub.git_root is not set. Set it to a directory on durable storage "
            "every API pod mounts (and back it up) — it holds the skill hub's version history."
        )
    import tempfile

    return Path(tempfile.mkdtemp(prefix="skill-hub-git-"))
