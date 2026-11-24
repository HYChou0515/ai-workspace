"""docs/plan-archive-full.md P1: a write-back the archive refuses comes back to
the app with rsync's own words.

The production incident: the archive PVC filled up, every write-back failed, and
the only trace was a bare `Internal Server Error` — the reason ("No space left
on device") stayed in this host's log. The app could not tell anyone why.

`_REAL_ENOSPC` is not invented: it is what rsync printed, exit 11, when
`rsync -rlptD --delete` wrote a 300 KB file into a 64 KB tmpfs — captured
2026-11-24 in a throwaway image `FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
with `apt-get install rsync` (`rsync --version`: 3.5.0), run with
`docker run --tmpfs /tiny:size=64k`. Other rsync versions print another
`receiver.c(N) [receiver=X]`; the message and the exit code are the same. The
integration test next door reproduces it for real."""

from __future__ import annotations

from pathlib import Path

import httpx
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.nfs_archive import NfsArchive, failure_reason

_REAL_ENOSPC = (
    b'rsync: [receiver] write failed on "/tiny/item/big.bin": No space left on device (28)\n'
    b"rsync error: error in file IO (code 11) at receiver.c(657) [receiver=3.5.0]\n"
)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


def test_the_reason_is_rsyncs_own_words():
    assert failure_reason(11, _REAL_ENOSPC) == (
        'rsync: [receiver] write failed on "/tiny/item/big.bin": No space left on device (28)\n'
        "rsync error: error in file IO (code 11) at receiver.c(657) [receiver=3.5.0]"
    )


def test_a_failure_on_many_files_keeps_the_first_few_and_the_summary():
    """A full disk fails every file after the first: rsync prints one line per
    file. The reason keeps the first three, says how many more, and the summary."""
    lines = [
        f'rsync: [receiver] write failed on "/a/f{i}": No space left on device (28)'
        for i in range(40)
    ]
    stderr = (
        "\n".join(lines) + "\nrsync error: error in file IO (code 11) at receiver.c(657)\n"
    ).encode()
    reason = failure_reason(11, stderr)
    assert reason.splitlines() == [
        *lines[:3],
        "… 37 more",
        "rsync error: error in file IO (code 11) at receiver.c(657)",
    ]


def test_a_silent_failure_still_says_something():
    assert failure_reason(23, b"") == "rsync exited 23 with no message"


async def test_persist_answers_with_the_reason_when_the_archive_refuses(tmp_path: Path):
    """Through the real `/persist` route and the real `NfsArchive.persist`: only
    the rsync process is replaced, by one that fails the way a full disk does."""

    async def full_disk(argv: list[str]) -> tuple[int, bytes]:
        return (11, _REAL_ENOSPC) if argv[0] == "rsync" else (0, b"")

    archive = NfsArchive(tmp_path / "nfs", runner=full_disk)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "live", isolate=False)
    app = make_host_app(sandbox, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-1"})).json()["remote_id"]
        r = await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
    assert r.status_code == 500
    assert r.json() == {
        "error": "ArchiveWriteFailed",
        "detail": failure_reason(11, _REAL_ENOSPC),
    }


async def test_persist_answers_with_the_reason_when_the_archive_refuses_before_rsync(
    tmp_path: Path, monkeypatch
):
    """Review round 1 (D-1): on a full disk the FIRST failure is not rsync's.
    `persist` writes a small generation file onto the archive before it runs
    rsync; with no space left that write raises ENOSPC, rsync never runs, and
    the route answered a bare 500 — so after the first failed write-back the
    reason was gone for the rest of the incident."""
    import errno

    async def ok(argv: list[str]) -> tuple[int, bytes]:
        return 0, b""

    archive = NfsArchive(tmp_path / "nfs", runner=ok)

    def full(_item_id: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "nfs" / "x.gen"))

    monkeypatch.setattr(archive, "_new_generation", full)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "live", isolate=False)
    app = make_host_app(sandbox, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-1"})).json()["remote_id"]
        r = await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
    assert r.status_code == 500
    assert r.json()["error"] == "ArchiveWriteFailed"
    assert "No space left on device" in r.json()["detail"]


async def test_files_vanishing_mid_copy_are_not_a_refused_write_back(tmp_path: Path):
    """Review round 2: an agent's `pip install` or test run creates and deletes
    temp files while a checkpoint copies; rsync then exits 24 ("some files
    vanished before they could be transferred"). The archive took everything that
    still exists — a vanished file had nothing left to save — so this is a
    write-back that landed, not one the archive refused (which stops the item)."""

    async def vanished(argv: list[str]) -> tuple[int, bytes]:
        if argv[0] != "rsync":
            return 0, b""
        return 24, (
            b'file has vanished: "/live/x/root/.pytest_cache/tmp1"\n'
            b"rsync warning: some files vanished before they could be transferred "
            b"(code 24) at main.c(1338) [sender=3.5.0]\n"
        )

    archive = NfsArchive(tmp_path / "nfs", runner=vanished)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "live", isolate=False)
    app = make_host_app(sandbox, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-1"})).json()["remote_id"]
        additive = await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
        reconcile = await c.post(f"/sandboxes/{rid}/persist", json={"delete": True})
    assert additive.status_code == 204
    assert reconcile.status_code == 204


async def test_an_item_dir_that_cannot_be_made_says_why(tmp_path: Path):
    """Review round 2: a brand-new item's first write-back starts by making its
    directory on the archive — on a full or broken archive that is the first
    thing to fail, and it must name the reason like rsync would. (A plain file
    where the archive root should be makes `mkdir` fail without needing a
    full disk.)"""
    root = tmp_path / "nfs"
    root.write_text("not a directory")

    async def ok(argv: list[str]) -> tuple[int, bytes]:
        return 0, b""

    archive = NfsArchive(root, runner=ok)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "live", isolate=False)
    app = make_host_app(sandbox, advertise_url="http://h", archive=archive)
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "item-1"})).json()["remote_id"]
        r = await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
    assert r.status_code == 500
    assert r.json()["error"] == "ArchiveWriteFailed"
    assert "Not a directory" in r.json()["detail"] or "File exists" in r.json()["detail"]


async def test_a_refused_archive_is_logged_as_an_error(tmp_path: Path, caplog):
    """Review round 2: unhandled, a failed persist was logged at ERROR by
    uvicorn; handled, it must stay an ERROR — alerting keyed on it must still
    fire when the archive fills."""
    import logging

    async def full_disk(argv: list[str]) -> tuple[int, bytes]:
        return (11, _REAL_ENOSPC) if argv[0] == "rsync" else (0, b"")

    archive = NfsArchive(tmp_path / "nfs", runner=full_disk)
    sandbox = LocalProcessSandbox(root_dir=tmp_path / "live", isolate=False)
    app = make_host_app(sandbox, advertise_url="http://h", archive=archive)
    with caplog.at_level(logging.WARNING, logger="sandbox_host.app"):
        async with _client(app) as c:
            rid = (await c.post("/sandboxes", json={"item_id": "item-1"})).json()["remote_id"]
            await c.post(f"/sandboxes/{rid}/persist", json={"delete": False})
    refused = [r for r in caplog.records if "archive operation failed" in r.getMessage()]
    assert refused and all(r.levelno == logging.ERROR for r in refused)
