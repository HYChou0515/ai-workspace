"""Clone a remote git repo into an ephemeral checkout and feed each
tracked source file into the Ingestor — the P3.0 code-QA entry point.

Design (see docs/plan-code-qa.md):

- **Ephemeral clone.** A shallow `git clone --depth=1 …` into a tempdir;
  removed before `sync()` returns. We never keep a working copy — the
  truth lives in the Collection's SourceDocs + DocChunks. A re-sync is a
  fresh clone (cheap for the typical self-hosted gitlab repo).
- **Auth.** When a PAT is set on the Collection, we splice
  `git_token` into the HTTPS URL (gitlab/github accept `…://oauth2:<TOKEN>@host/…`).
  Plain `https://` / `file://` clones use no creds.
- **Walking.** We walk the checkout via `git ls-files` (only tracked
  files; .gitignore is respected automatically) and ingest each entry.
  The Ingestor's `store()` already filters by mime/extension — code
  files and markdown are accepted, binaries/lock-files get dropped.
- **HEAD tracking.** Successful sync records the cloned HEAD SHA on the
  Collection so the FE can show "synced at commit …" and the scheduler
  can decide whether the remote has moved before re-cloning.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import msgspec
from specstar import SpecStar

from ..resources.kb import Collection
from .ingest import Ingestor

logger = logging.getLogger(__name__)


class CodeRepoSyncError(RuntimeError):
    """The clone, walk, or ingest step failed. The API layer maps this to
    a 502 with the underlying message — usually auth or unreachable host."""


class CodeRepoIngestor:
    """Sync a Collection's `git_url` into its SourceDocs.

    Stateless across calls: each `sync` clones into a fresh tempdir. The
    Ingestor (and through it the embedder + pipeline) is injected so tests
    can substitute deterministic stand-ins."""

    def __init__(self, spec: SpecStar, *, ingestor: Ingestor) -> None:
        self._spec = spec
        self._ingestor = ingestor

    def sync(self, *, collection_id: str, user: str) -> None:
        """Clone the Collection's `git_url` and ingest each tracked file.

        No-op when the Collection has no `git_url` set (so a scheduler can
        walk every Collection blindly). Raises `CodeRepoSyncError` on git
        failure (bad URL, auth, branch missing)."""
        crm = self._spec.get_resource_manager(Collection)
        coll = crm.get(collection_id).data
        assert isinstance(coll, Collection)
        if not coll.git_url:
            return  # not a code collection
        url = _splice_token(coll.git_url, coll.git_token)
        with tempfile.TemporaryDirectory(prefix="code-repo-") as raw:
            checkout = Path(raw) / "repo"
            try:
                self._clone(url, coll.git_branch, checkout)
                sha = self._head_sha(checkout)
                self._ingest_tree(collection_id, user, checkout)
            except subprocess.CalledProcessError as e:
                msg = (e.stderr or b"").decode("utf-8", errors="replace").strip()
                raise CodeRepoSyncError(f"git failed: {msg or e}") from e
            finally:
                # TemporaryDirectory cleans on exit; this is belt-and-braces
                # for the case where git itself wrote .git permissions that
                # block rmtree on some filesystems.
                shutil.rmtree(checkout, ignore_errors=True)
        crm.update(collection_id, msgspec.structs.replace(coll, git_last_sha=sha))

    # ─────────────────────── git wrappers ───────────────────────

    @staticmethod
    def _clone(url: str, branch: str | None, dest: Path) -> None:
        args = ["git", "clone", "--depth=1", "--single-branch"]
        if branch:
            args += ["--branch", branch]
        args += [url, str(dest)]
        subprocess.run(args, check=True, capture_output=True)

    @staticmethod
    def _head_sha(checkout: Path) -> str:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            check=True,
            capture_output=True,
        )
        return out.stdout.decode("utf-8", errors="replace").strip()

    def _ingest_tree(self, collection_id: str, user: str, checkout: Path) -> None:
        """Walk `git ls-files` (tracked, .gitignore-respecting) and feed
        each file into the Ingestor. The Ingestor decides accept/reject by
        mime + extension; unrecognised types get silently dropped."""
        ls = subprocess.run(
            ["git", "ls-files"],
            cwd=checkout,
            check=True,
            capture_output=True,
        )
        rel_paths = ls.stdout.decode("utf-8", errors="replace").splitlines()
        for rel in rel_paths:
            if not rel:
                continue
            full = checkout / rel
            try:
                data = full.read_bytes()
            except OSError:
                # symlink to nowhere / unreadable — skip rather than fail
                # the whole sync over one bad entry.
                logger.warning("code-repo: could not read %s — skipping", rel)
                continue
            self._ingestor.ingest(collection_id=collection_id, user=user, filename=rel, data=data)


def _splice_token(url: str, token: str | None) -> str:
    """Embed a PAT into an `https://` URL as the basic-auth user — gitlab
    accepts `oauth2:<token>` (and github accepts the token as the
    username). `file://` / `ssh://` URLs are returned untouched."""
    if not token:
        return url
    parts = urlparse(url)
    if parts.scheme not in {"http", "https"}:
        return url
    netloc = f"oauth2:{token}@{parts.hostname or ''}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunparse(parts._replace(netloc=netloc))
