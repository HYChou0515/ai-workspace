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
from collections.abc import Callable
from datetime import datetime
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

    def sync(
        self,
        *,
        collection_id: str,
        user: str,
        now_ms: int | None = None,
        on_phase: Callable[[str], None] | None = None,
    ) -> None:
        """Clone the Collection's `git_url` and ingest each tracked file.

        No-op when the Collection has no `git_url` set (so a scheduler can
        walk every Collection blindly). Raises `CodeRepoSyncError` on git
        failure (bad URL, auth, branch missing).

        ``on_phase`` (#355) is called with ``"cloning"`` then ``"ingesting"`` so
        the caller (the ``code_sync`` job handler) can surface live progress."""
        crm = self._spec.get_resource_manager(Collection)
        coll = crm.get(collection_id).data
        assert isinstance(coll, Collection)
        if not coll.git_url:
            return  # not a code collection
        url = _splice_token(coll.git_url, coll.git_token)
        # Stamp the wall-clock pull time on EVERY attempt — success or failure —
        # so the daily sweeper (#355) treats this collection as "tried today" and
        # doesn't re-fire it every tick after the daily-sync time. On success we
        # also advance ``git_last_sha``; a failed clone keeps the prior sha (its
        # error is surfaced separately, via the wiki build state's last_error).
        # This is the collection's own sync bookkeeping, not a user edit, so write
        # it AS THE OWNER: #262's write ACL (perm.checker) gates every Collection
        # update on `write_meta`, and the syncer (a non-owner editor, or the
        # sweeper running as the default user) need not hold it.
        stamp = now_ms if now_ms is not None else int(time.time() * 1000)
        owner = crm.get_meta(collection_id).created_by
        with tempfile.TemporaryDirectory(prefix="code-repo-") as raw:
            checkout = Path(raw) / "repo"
            try:
                if on_phase is not None:
                    on_phase("cloning")
                self._clone(url, coll.git_branch, checkout)
                sha = self._head_sha(checkout)
                if on_phase is not None:
                    on_phase("ingesting")
                self._ingest_tree(collection_id, user, checkout)
            except subprocess.CalledProcessError as e:
                with crm.using(owner):
                    crm.update(
                        collection_id,
                        msgspec.structs.replace(coll, git_last_pulled_at=stamp),
                    )
                msg = (e.stderr or b"").decode("utf-8", errors="replace").strip()
                raise CodeRepoSyncError(f"git failed: {msg or e}") from e
            finally:
                # TemporaryDirectory cleans on exit; this is belt-and-braces
                # for the case where git itself wrote .git permissions that
                # block rmtree on some filesystems.
                shutil.rmtree(checkout, ignore_errors=True)
        with crm.using(owner):
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
    """Background-loop helper: every tick, enqueue a ``code_sync`` job for any
    code Collection that is due for its daily wall-clock sync (#355).

    `tick()` is what does one pass (caller drives the cadence). It is a pure
    *producer*: it does NOT clone/ingest itself (that runs in the enqueued
    ``code_sync`` job on the wiki worker — #312 keeps heavy work off the API).
    The app's lifespan task awakens every ``Settings.sync_check_interval_sec``
    and calls `tick()`.

    The schedule is a single server-local time-of-day (``daily_sync``, ``HH:MM``)
    that applies to *every* code collection — there is no per-collection knob.
    The old per-collection ``sync_interval_hours`` interval is no longer read
    (it stays on the model as a dormant field). ``daily_sync=None`` ⇒ the daily
    auto-sync is off and `tick()` is a no-op (manual POST /sync only). ``enqueue``
    is the coordinator's ``enqueue_code_sync`` (which coalesces, so a still-running
    sync isn't re-queued — and the job's own clone stamps ``git_last_pulled_at``
    even on failure, so a due collection fires at most once a day: no retry
    storm)."""

    def __init__(
        self,
        spec: SpecStar,
        *,
        enqueue: Callable[[str], None],
        daily_sync: str | None = None,
    ) -> None:
        self._spec = spec
        self._enqueue = enqueue
        self._daily_sync = daily_sync

    def tick(self, *, now_ms: int | None = None) -> list[str]:
        """Run one sweep pass. Returns the Collection ids a ``code_sync`` was
        enqueued for this tick (collections not yet due are skipped)."""
        stamp = now_ms if now_ms is not None else int(time.time() * 1000)
        enqueued: list[str] = []
        rm = self._spec.get_resource_manager(Collection)
        for r in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
            coll = r.data
            assert isinstance(coll, Collection)
            if not coll.git_url:
                continue
            if not _due_for_daily_sync(
                now_ms=stamp,
                last_pulled_ms=coll.git_last_pulled_at,
                daily_sync=self._daily_sync,
            ):
                continue
            cid = r.info.resource_id  # ty: ignore[unresolved-attribute]
            self._enqueue(cid)
            enqueued.append(cid)
        return enqueued


def parse_daily_sync(value: str | None) -> tuple[int, int] | None:
    """Parse a ``daily_sync`` config string (``"HH:MM"``, 24-hour) into
    ``(hour, minute)``. Returns ``None`` for an unset / malformed value, which
    callers treat as "daily auto-sync off" (no crash on a typo'd config)."""
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if 0 <= hour < 24 and 0 <= minute < 60:
        return (hour, minute)
    return None


def _due_for_daily_sync(*, now_ms: int, last_pulled_ms: int | None, daily_sync: str | None) -> bool:
    """Is a code collection due for its daily auto-sync right now?

    Due when (a) ``daily_sync`` is set, (b) the current local time is at/after
    today's ``HH:MM`` target, and (c) the last pull/attempt was before today's
    target (or it has never synced). Because `CodeRepoIngestor.sync` stamps
    ``git_last_pulled_at`` on every attempt — success OR failure — a due
    collection fires at most once per day even if the clone keeps failing: no
    every-tick retry storm (the user's create-time-typo worry)."""
    hhmm = parse_daily_sync(daily_sync)
    if hhmm is None:
        return False
    hour, minute = hhmm
    now = datetime.fromtimestamp(now_ms / 1000)  # server-local time
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < target:
        return False  # today's daily-sync time not reached yet
    if last_pulled_ms is None:
        return True  # never synced and past today's time → due
    last = datetime.fromtimestamp(last_pulled_ms / 1000)
    return last < target  # already synced/attempted after today's time → not due


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
