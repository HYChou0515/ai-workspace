"""P3.0 §2.9 background sync: CodeRepoSweeper.tick() re-syncs any
Collection whose `sync_interval_hours` has elapsed since `git_last_pulled_at`.

The sweeper is driven by the app's lifespan loop on `sync_check_interval_sec`;
tests call `tick(now_ms=…)` directly so we don't have to wait real seconds.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import msgspec
import pytest
from specstar import SpecStar

from workspace_app.kb.code_repo import CodeRepoIngestor, CodeRepoSweeper
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.resources.kb import EMBED_DIM, Collection


def _git(cwd: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        check=True,
        env=env,
        capture_output=True,
    )


@pytest.fixture
def remote(tmp_path: Path) -> str:
    work = tmp_path / "r"
    work.mkdir()
    (work / "x.py").write_text("def f():\n    return 1\n")
    _git(work, "init")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "i")
    return work.as_uri()


def _make_sweeper(spec: SpecStar) -> CodeRepoSweeper:
    embedder = HashEmbedder(dim=EMBED_DIM)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)
    return CodeRepoSweeper(spec, code_repo=CodeRepoIngestor(spec, ingestor=ingestor))


def test_tick_syncs_collection_due_for_first_pull(spec: SpecStar, remote: str):
    """A Collection with sync_interval_hours set and git_last_pulled_at=None
    is due immediately; the first tick runs `sync` and stamps git_last_sha
    + git_last_pulled_at."""
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="repo", git_url=remote, sync_interval_hours=1, embedder_id=1))
        .resource_id
    )
    sweeper = _make_sweeper(spec)
    synced = sweeper.tick(now_ms=1_000_000)
    assert synced == [cid]
    after = spec.get_resource_manager(Collection).get(cid).data
    assert after.git_last_sha and len(after.git_last_sha) == 40
    assert after.git_last_pulled_at == 1_000_000


def test_tick_skips_collection_not_yet_due(spec: SpecStar, remote: str):
    """A Collection pulled recently (< sync_interval) is skipped on the
    next tick — no clone, no churn."""
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="r", git_url=remote, sync_interval_hours=2, embedder_id=1))
        .resource_id
    )
    # Mark as just-pulled now.
    rm = spec.get_resource_manager(Collection)
    coll = rm.get(cid).data
    rm.update(cid, msgspec.structs.replace(coll, git_last_pulled_at=1_000_000, git_last_sha="abc"))
    sweeper = _make_sweeper(spec)
    # 1 hour later (< 2-hour interval) → not due.
    synced = sweeper.tick(now_ms=1_000_000 + 3600_000)
    assert synced == []


def test_tick_skips_collections_without_sync_interval(spec: SpecStar, remote: str):
    """Manual-sync-only Collection (sync_interval_hours=None) is never
    picked up by the sweeper — only POST /sync triggers it."""
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="m", git_url=remote, embedder_id=1))
        .resource_id
    )
    sweeper = _make_sweeper(spec)
    assert sweeper.tick(now_ms=10_000_000) == []
    # Confirm: nothing was synced.
    assert spec.get_resource_manager(Collection).get(cid).data.git_last_sha is None


def test_tick_swallows_per_collection_sync_failure(spec: SpecStar, tmp_path: Path):
    """If one Collection fails to clone, the sweeper logs and continues —
    a single bad remote can't take down the whole background job."""
    bogus = (tmp_path / "no-such").as_uri()
    rm = spec.get_resource_manager(Collection)
    bad = rm.create(
        Collection(name="bad", git_url=bogus, sync_interval_hours=1, embedder_id=1)
    ).resource_id
    sweeper = _make_sweeper(spec)
    # Returns the ids it *attempted*; does NOT raise.
    synced = sweeper.tick(now_ms=1_000_000)
    assert synced == []  # nothing succeeded
    # Bad Collection still has no sha.
    assert rm.get(bad).data.git_last_sha is None
