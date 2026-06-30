"""#355 daily auto-sync: CodeRepoSweeper.tick() re-syncs every code Collection
once a day at a server-local wall-clock time (``daily_sync`` = ``HH:MM``),
replacing the old per-collection ``sync_interval_hours`` interval.

The sweeper is driven by the app's lifespan loop on ``sync_check_interval_sec``;
tests call ``tick(now_ms=…)`` directly so we don't have to wait real seconds.
``now_ms`` is built from a *local* ``datetime`` so the gate's local-time maths
round-trips identically regardless of the machine's timezone.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import msgspec
import pytest
from specstar import SpecStar

from workspace_app.kb.code_repo import (
    CodeRepoIngestor,
    CodeRepoSweeper,
    _due_for_daily_sync,
    parse_daily_sync,
)
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


def _ms(dt: datetime) -> int:
    """A local-datetime → epoch-ms helper, so tests are timezone-agnostic."""
    return int(dt.timestamp() * 1000)


def _make_sweeper(spec: SpecStar, *, daily_sync: str | None = "03:00") -> CodeRepoSweeper:
    embedder = HashEmbedder(dim=EMBED_DIM)
    pipeline = build_doc_pipeline(embedder=embedder)
    ingestor = Ingestor(spec, pipeline=pipeline, embedder=embedder)
    return CodeRepoSweeper(
        spec, code_repo=CodeRepoIngestor(spec, ingestor=ingestor), daily_sync=daily_sync
    )


# ── pure gate helper ──────────────────────────────────────────────────────


def test_parse_daily_sync_valid_and_invalid():
    assert parse_daily_sync("03:00") == (3, 0)
    assert parse_daily_sync("23:59") == (23, 59)
    assert parse_daily_sync(None) is None
    assert parse_daily_sync("") is None
    assert parse_daily_sync("3am") is None  # not HH:MM
    assert parse_daily_sync("24:00") is None  # hour out of range
    assert parse_daily_sync("03:60") is None  # minute out of range
    assert parse_daily_sync("a:b") is None  # non-numeric


def test_due_gate_fires_once_past_the_daily_time():
    target = datetime(2026, 1, 2, 3, 0)
    before = _ms(datetime(2026, 1, 2, 2, 30))  # before today's 03:00
    at = _ms(datetime(2026, 1, 2, 3, 5))  # just after 03:00
    # Never synced, past today's time → due.
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=None, daily_sync="03:00") is True
    # Last pull was before today's time → due.
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=before, daily_sync="03:00") is True
    # Already synced after today's time → not due again today.
    assert (
        _due_for_daily_sync(now_ms=at, last_pulled_ms=_ms(target), daily_sync="03:00") is False
    )


def test_due_gate_not_before_the_daily_time():
    early = _ms(datetime(2026, 1, 2, 2, 0))  # before 03:00
    assert _due_for_daily_sync(now_ms=early, last_pulled_ms=None, daily_sync="03:00") is False


def test_due_gate_off_when_daily_sync_unset():
    at = _ms(datetime(2026, 1, 2, 12, 0))
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=None, daily_sync=None) is False
    assert _due_for_daily_sync(now_ms=at, last_pulled_ms=None, daily_sync="") is False


# ── tick integration ──────────────────────────────────────────────────────


def test_tick_syncs_code_collection_due_today(spec: SpecStar, remote: str):
    """A code collection never synced (git_last_pulled_at=None), past the daily
    time, is synced once: tick stamps git_last_sha + git_last_pulled_at."""
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="repo", git_url=remote, embedder_id=1))
        .resource_id
    )
    sweeper = _make_sweeper(spec, daily_sync="03:00")
    now = _ms(datetime(2026, 1, 2, 3, 5))
    assert sweeper.tick(now_ms=now) == [cid]
    after = spec.get_resource_manager(Collection).get(cid).data
    assert after.git_last_sha and len(after.git_last_sha) == 40
    assert after.git_last_pulled_at == now
    # A second tick the same day is a no-op (already pulled after 03:00).
    assert sweeper.tick(now_ms=now + 60_000) == []


def test_tick_skips_before_daily_time(spec: SpecStar, remote: str):
    """Before today's daily-sync time, nothing is swept."""
    spec.get_resource_manager(Collection).create(
        Collection(name="r", git_url=remote, embedder_id=1)
    )
    sweeper = _make_sweeper(spec, daily_sync="03:00")
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 1, 0))) == []


def test_tick_resyncs_next_day(spec: SpecStar, remote: str):
    """A collection pulled yesterday is due again past today's daily time."""
    rm = spec.get_resource_manager(Collection)
    cid = rm.create(Collection(name="r", git_url=remote, embedder_id=1)).resource_id
    coll = rm.get(cid).data
    rm.update(
        cid,
        msgspec.structs.replace(
            coll, git_last_pulled_at=_ms(datetime(2026, 1, 1, 3, 0)), git_last_sha="abc"
        ),
    )
    sweeper = _make_sweeper(spec, daily_sync="03:00")
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 3, 5))) == [cid]


def test_tick_off_when_daily_sync_none(spec: SpecStar, remote: str):
    """daily_sync=None ⇒ the sweeper never auto-syncs (manual /sync only)."""
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="m", git_url=remote, embedder_id=1))
        .resource_id
    )
    sweeper = _make_sweeper(spec, daily_sync=None)
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 12, 0))) == []
    assert spec.get_resource_manager(Collection).get(cid).data.git_last_sha is None


def test_tick_skips_non_code_collections(spec: SpecStar):
    """A plain (no git_url) collection is ignored entirely."""
    spec.get_resource_manager(Collection).create(Collection(name="plain", embedder_id=1))
    sweeper = _make_sweeper(spec, daily_sync="03:00")
    assert sweeper.tick(now_ms=_ms(datetime(2026, 1, 2, 4, 0))) == []


def test_tick_swallows_failure_and_stamps_attempt(spec: SpecStar, tmp_path: Path):
    """A failing clone is logged + skipped (no crash). The attempt still stamps
    git_last_pulled_at — so it won't re-fire every tick the same day (#355: no
    create-time-typo retry storm) — while leaving git_last_sha unchanged."""
    bogus = (tmp_path / "no-such").as_uri()
    rm = spec.get_resource_manager(Collection)
    bad = rm.create(Collection(name="bad", git_url=bogus, embedder_id=1)).resource_id
    sweeper = _make_sweeper(spec, daily_sync="03:00")
    now = _ms(datetime(2026, 1, 2, 3, 5))
    assert sweeper.tick(now_ms=now) == []  # nothing succeeded
    after = rm.get(bad).data
    assert after.git_last_sha is None  # sha untouched by a failed clone
    assert after.git_last_pulled_at == now  # but the attempt was stamped
    # Same day, a minute later: not due again (no storm).
    assert sweeper.tick(now_ms=now + 60_000) == []
