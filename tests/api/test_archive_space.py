"""docs/plan-archive-full.md P4: below `block_free_ratio` of its capacity, the
archive takes no new turn on ANY item (D3) — before it is full, not after.

The space is the archive's filesystem as this pod mounts it
(`sandbox.durable.nfs_root`); measuring is one `disk_usage` call, cached briefly
so a burst of sends does not each go to the NFS server."""

from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from workspace_app.api import create_app
from workspace_app.api.archive_health import ArchiveSpace
from workspace_app.api.events import RunDone
from workspace_app.config.loader import load
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


class _Disk:
    """`shutil.disk_usage`'s shape, with the free bytes under the test's control."""

    def __init__(self, free: int, total: int = 1000) -> None:
        self.free, self.total, self.calls = free, total, 0

    def __call__(self, _path: Any) -> Any:
        self.calls += 1
        return shutil._ntuple_diskusage(self.total, self.total - self.free, self.free)  # type: ignore[attr-defined]


class _Runner:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def run(self, prompt: str, ctx: Any):
        self.ran.append(prompt)
        yield RunDone()


async def test_it_measures_the_real_filesystem(tmp_path: Path):
    free, total = await ArchiveSpace(tmp_path).measure()
    assert total == shutil.disk_usage(tmp_path).total
    assert 0 < free <= total


async def test_a_measurement_is_reused_for_a_while(tmp_path: Path):
    disk, now = _Disk(500), [0.0]
    space = ArchiveSpace(tmp_path, usage=disk, clock=lambda: now[0], max_age_s=30)
    await space.measure()
    await space.measure()
    assert disk.calls == 1
    now[0] = 31.0
    await space.measure()
    assert disk.calls == 2


def _send(client: TestClient, iid: str) -> int:
    return client.post(f"/a/rca/items/{iid}/messages", json={"content": "go"}).status_code


def _app(disk: _Disk | None, tmp_path: Path, runner: _Runner, **kw: Any):
    spec = make_spec(default_user="u")
    items = [register_rca_item(spec), register_rca_item(spec)]
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        archive_space=(None if disk is None else ArchiveSpace(tmp_path, usage=disk, max_age_s=0)),
        **kw,
    )
    return app, items


def test_below_the_floor_every_item_is_refused_with_the_numbers(tmp_path: Path):
    runner = _Runner()
    app, items = _app(_Disk(free=50, total=1000), tmp_path, runner, archive_block_free_ratio=0.1)
    with TestClient(app) as client:
        for iid in items:
            resp = client.post(f"/a/rca/items/{iid}/messages", json={"content": "go"})
            assert resp.status_code == 507
            assert resp.json()["detail"] == {
                "error": "archive_space_low",
                "free": 50,
                "total": 1000,
                "block_free_ratio": 0.1,
            }
    assert runner.ran == []


def test_above_the_floor_turns_go_through(tmp_path: Path):
    runner = _Runner()
    app, items = _app(_Disk(free=101, total=1000), tmp_path, runner, archive_block_free_ratio=0.1)
    with TestClient(app) as client:
        assert (
            client.post(f"/a/rca/items/{items[0]}/messages", json={"content": "go"}).status_code
            == 202
        )


def test_a_zero_floor_switches_the_refusal_off(tmp_path: Path):
    runner = _Runner()
    app, items = _app(_Disk(free=0, total=1000), tmp_path, runner, archive_block_free_ratio=0.0)
    with TestClient(app) as client:
        assert (
            client.post(f"/a/rca/items/{items[0]}/messages", json={"content": "go"}).status_code
            == 202
        )


def test_with_no_archive_configured_nothing_is_measured_or_refused(tmp_path: Path):
    runner = _Runner()
    app, items = _app(None, tmp_path, runner)
    with TestClient(app) as client:
        assert (
            client.post(f"/a/rca/items/{items[0]}/messages", json={"content": "go"}).status_code
            == 202
        )


class _Stop(Exception):
    pass


@pytest.mark.parametrize(
    ("durable", "expect_root", "expect_block", "expect_warn"),
    [
        ("", None, 0.1, 0.2),
        ("  durable:\n    kind: nfs_tree\n    nfs_root: {root}\n", "root", 0.1, 0.2),
        (
            "  durable:\n    kind: nfs_tree\n    nfs_root: {root}\n"
            "    block_free_ratio: 0.05\n    warn_free_ratio: 0.3\n",
            "root",
            0.05,
            0.3,
        ),
    ],
)
def test_the_settings_reach_create_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    durable: str,
    expect_root: str | None,
    expect_block: float,
    expect_warn: float,
) -> None:
    """A setting the deploy reads but never passes is a knob that silently does
    nothing; this walks the real composition root."""
    import workspace_app.__main__ as main_mod

    root = tmp_path / "archive"
    root.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        dedent(f"""
            server:
              run_consumers: false
            filestore:
              kind: specstar
            skill_hub:
              git_root: {tmp_path / "skill-hub-git"}
            kb:
              embedder:
                model: ""
        """)
        + f"sandbox:\n  root: {tmp_path / 'sandbox'}\n"
        + durable.format(root=root),
        encoding="utf-8",
    )
    seen: dict[str, Any] = {}

    def spy(**kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(main_mod, "PACKAGES", {})
    monkeypatch.setattr(main_mod, "create_app", spy)
    with pytest.raises(_Stop):
        main_mod.build_app(load(config_path=cfg, env={}), config_dir=None)

    space = seen["archive_space"]
    assert (None if space is None else "root") == expect_root
    if space is not None:
        assert space.root == root
    assert seen["archive_block_free_ratio"] == expect_block
    assert seen["archive_warn_free_ratio"] == expect_warn
