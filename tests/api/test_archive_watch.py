"""docs/plan-archive-full.md P5 (D4): the superusers hear about the archive
filling BEFORE anyone is refused — once per level crossed (20%, 15%, 10% of
capacity free, by default), counted afresh after it recovers above 20%.

The bell gets the row; a deploy that wired `server.notification_channel` mails
it too (D5) — that relay reads every row `notify()` writes, so nothing here is
mail-specific."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.archive_health import (
    ArchiveSpace,
    ArchiveSpaceWatch,
    register_archive_health,
)
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import Notification, make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

_TOTAL = 1000


class _Disk:
    def __init__(self, free: int) -> None:
        self.free = free

    def __call__(self, _path: Any) -> Any:
        return shutil._ntuple_diskusage(_TOTAL, _TOTAL - self.free, self.free)  # type: ignore[attr-defined]


def _rows(spec) -> list[tuple[str, str]]:
    rm = spec.get_resource_manager(Notification)
    out = []
    for res in rm.list_resources():
        n = res.data
        assert isinstance(n, Notification)
        if n.kind == "archive_space_low":
            out.append((n.recipient, n.title))
    return sorted(out)


def _watch(spec, disk: _Disk, tmp_path: Path, now: list[int] | None = None) -> ArchiveSpaceWatch:
    register_archive_health(spec)
    clock = now if now is not None else [0]
    return ArchiveSpaceWatch(
        spec,
        ArchiveSpace(tmp_path, usage=disk, max_age_s=0),
        superusers=frozenset({"ops-a", "ops-b"}),
        warn_free_ratio=0.2,
        block_free_ratio=0.1,
        now_ms=lambda: clock[0],
    )


_MIN = 60_000


async def test_above_the_warning_floor_nobody_is_told(tmp_path: Path):
    spec = make_spec(default_user="u")
    await _watch(spec, _Disk(250), tmp_path).tick()
    assert _rows(spec) == []


async def test_each_level_is_told_once_to_every_superuser(tmp_path: Path):
    spec = make_spec(default_user="u")
    disk = _Disk(190)
    watch = _watch(spec, disk, tmp_path)

    await watch.tick()
    await watch.tick()  # still 19%: nothing new
    assert [r for r, _ in _rows(spec)] == ["ops-a", "ops-b"]

    disk.free = 140  # crosses 15%
    await watch.tick()
    disk.free = 90  # crosses 10%: the floor below which turns are refused
    await watch.tick()
    await watch.tick()

    rows = _rows(spec)
    assert len(rows) == 6  # three levels × two superusers
    assert sum("is 19.0% free" in t for _, t in rows) == 2
    assert sum("is 14.0% free" in t for _, t in rows) == 2
    assert sum("is 9.0% free" in t for _, t in rows) == 2


async def test_a_drop_straight_past_two_levels_is_told_for_both(tmp_path: Path):
    spec = make_spec(default_user="u")
    await _watch(spec, _Disk(120), tmp_path).tick()  # 12%: below 20% and 15%
    assert len(_rows(spec)) == 4


async def test_another_pod_does_not_tell_it_again(tmp_path: Path):
    spec = make_spec(default_user="u")
    await _watch(spec, _Disk(190), tmp_path).tick()  # pod A
    await _watch(spec, _Disk(190), tmp_path).tick()  # pod B, same shared store
    assert len(_rows(spec)) == 2


async def test_after_it_stays_recovered_a_new_drop_is_told_again(tmp_path: Path):
    spec = make_spec(default_user="u")
    disk, now = _Disk(190), [0]
    watch = _watch(spec, disk, tmp_path, now)
    await watch.tick()
    disk.free = 300  # extended
    for minute in range(0, 31):  # above the floor for half an hour
        now[0] = minute * _MIN
        await watch.tick()
    disk.free = 190  # filling again
    now[0] = 32 * _MIN
    await watch.tick()
    assert len(_rows(spec)) == 4


async def test_hovering_around_the_floor_is_told_once(tmp_path: Path):
    """Review round 1 (D-4): rsync writes and deletes nudge the free space either
    side of 20%. Ending the stretch on the first tick above it made every pair
    of ticks a fresh 'below 20%' to every superuser (and their mail)."""
    spec = make_spec(default_user="u")
    disk, now = _Disk(199), [0]
    watch = _watch(spec, disk, tmp_path, now)
    for tick in range(10):
        disk.free = 199 if tick % 2 == 0 else 201
        now[0] = tick * _MIN
        await watch.tick()
    assert len(_rows(spec)) == 2  # once, to each of the two superusers


def test_the_running_app_tells_the_superusers(tmp_path: Path):
    """Through `create_app`'s lifespan: the watch is wired, starts at once, and
    the bell has the row — not a component with no call site."""
    spec = make_spec(default_user="u", superusers=frozenset({"ops-a"}))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        superusers=frozenset({"ops-a"}),
        archive_space=ArchiveSpace(tmp_path, usage=_Disk(140), max_age_s=0),
    )
    with TestClient(app):
        deadline = time.monotonic() + 10
        while not _rows(spec) and time.monotonic() < deadline:
            time.sleep(0.05)
    rows = _rows(spec)
    assert [r for r, _ in rows] == ["ops-a", "ops-a"]  # 20% and 15% crossed


async def test_a_recovery_shorter_than_half_an_hour_is_the_same_stretch(tmp_path: Path):
    spec = make_spec(default_user="u")
    disk, now = _Disk(190), [0]
    watch = _watch(spec, disk, tmp_path, now)
    await watch.tick()
    disk.free = 300
    for minute in range(1, 30):  # above the floor, but only 28 minutes
        now[0] = minute * _MIN
        await watch.tick()
    disk.free = 190
    now[0] = 30 * _MIN
    await watch.tick()
    assert len(_rows(spec)) == 2  # not told again


def test_a_window_another_pod_has_claimed_is_not_watched_again(tmp_path: Path):
    """Review round 1: one pod per window measures and tells (`ScanLease`
    "archive-space") — N pods each measuring and racing to tell is what the
    lease exists to stop. Another pod already holds this window, so this one
    stays quiet."""
    from workspace_app.api.lifecycle import ARCHIVE_WATCH_INTERVAL_S
    from workspace_app.workflow.triggers import ScanLease, SpecstarTriggerStore

    spec = make_spec(default_user="u", superusers=frozenset({"ops-a"}))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=ScriptedAgentRunner([]),
        superusers=frozenset({"ops-a"}),
        archive_space=ArchiveSpace(tmp_path, usage=_Disk(140), max_age_s=0),
    )
    lease = ScanLease(
        SpecstarTriggerStore(spec), "archive-space", interval_s=ARCHIVE_WATCH_INTERVAL_S
    )
    assert lease.claim()  # pod A holds this window
    with TestClient(app):
        time.sleep(2)
    assert _rows(spec) == []
