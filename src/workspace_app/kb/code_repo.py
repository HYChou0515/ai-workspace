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
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import msgspec
from specstar import QB, SpecStar

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

    def sync(self, *, collection_id: str, user: str, now_ms: int | None = None) -> None:
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
        # Stamp both the cloned HEAD and the wall-clock pull time so the
        # background sweeper can decide whether the Collection is due next.
        stamp = now_ms if now_ms is not None else int(time.time() * 1000)
        crm.update(
            collection_id,
            msgspec.structs.replace(coll, git_last_sha=sha, git_last_pulled_at=stamp),
        )

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


class CodeRepoSweeper:
    """Background-loop helper: every tick, re-sync any Collection whose
    `sync_interval_hours` has elapsed since `git_last_pulled_at`.

    `tick()` is what does one pass (caller drives the cadence). Per-collection
    sync failures are caught + logged so one bad remote never crashes the
    sweeper. The app's lifespan task awakens every
    ``Settings.sync_check_interval_sec`` and calls `tick()`."""

    _DEFAULT_USER = "default-user"  # v1: no auth on the sweeper either

    def __init__(self, spec: SpecStar, *, code_repo: CodeRepoIngestor) -> None:
        self._spec = spec
        self._code_repo = code_repo

    def tick(self, *, now_ms: int | None = None) -> list[str]:
        """Run one sweep pass. Returns the Collection ids that were
        successfully synced this tick (skipped + failed ones excluded)."""
        stamp = now_ms if now_ms is not None else int(time.time() * 1000)
        synced: list[str] = []
        rm = self._spec.get_resource_manager(Collection)
        for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            coll = r.data
            assert isinstance(coll, Collection)
            if not coll.git_url or coll.sync_interval_hours is None:
                continue
            # First-pull (last_pulled_at=None) is always due — we don't make
            # the user wait `sync_interval_hours` after creating a code
            # Collection before its initial clone. Subsequent ticks honour
            # the interval.
            interval_ms = coll.sync_interval_hours * 3600_000
            if (
                coll.git_last_pulled_at is not None
                and stamp - coll.git_last_pulled_at < interval_ms
            ):
                continue  # not yet due
            cid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            try:
                self._code_repo.sync(
                    collection_id=cid,
                    user=self._DEFAULT_USER,
                    now_ms=stamp,
                )
            except CodeRepoSyncError:
                # Don't take down the whole sweep over one bad remote.
                logger.exception("code-repo sweeper: sync failed for %s", cid)
                continue
            synced.append(cid)
        return synced


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
