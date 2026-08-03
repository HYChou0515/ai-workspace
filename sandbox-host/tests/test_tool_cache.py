"""P4 — the host-local, content-addressed store for third-party tools (#674).

Third-party bundles cannot live on the shared NFS the workspaces use: NFS
cannot hold the `root:root 755` invariant that keeps a sandbox's own uid from
rewriting the tools it runs. So the bytes travel over the network and the
RUNNABLE tree is always local, keyed by the sha that identifies it.

Everything here unpacks a stranger's tarball, so the tests are mostly about
what happens when that tarball is hostile.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from sandbox_host.tool_cache import ToolCache, ToolCacheError, _harden

_SHA = "b" * 64


def _tar(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_ensure_lays_the_bundle_down_under_its_own_sha(tmp_path: Path) -> None:
    cache = ToolCache(tmp_path, harden=lambda _p: None)

    installed = cache.ensure(_SHA, _tar({"launch": b"#!/bin/sh\n", "commands.json": b"[]"}))

    assert installed == tmp_path / _SHA
    assert (installed / "launch").read_bytes() == b"#!/bin/sh\n"
    assert (installed / "commands.json").read_bytes() == b"[]"


def test_ensure_is_a_no_op_when_that_sha_is_already_installed(tmp_path: Path) -> None:
    # Content-addressed: the same sha is the same bytes, so a second sandbox
    # asking for it must cost a stat, not a 150MB unpack.
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    cache.ensure(_SHA, _tar({"launch": b"first"}))

    installed = cache.ensure(_SHA, _tar({"launch": b"second"}))

    assert (installed / "launch").read_bytes() == b"first"


def test_a_sha_is_the_only_thing_that_can_name_a_directory(tmp_path: Path) -> None:
    # The sha arrives inside a manifest, which is written by whoever can push
    # the artifact — so it is untrusted input that is about to become a path.
    cache = ToolCache(tmp_path, harden=lambda _p: None)

    for hostile in ("../escape", "a" * 63, "/etc/passwd", "b" * 63 + "Z"):
        with pytest.raises(ToolCacheError):
            cache.ensure(hostile, _tar({"launch": b"x"}))

    assert list(tmp_path.iterdir()) == []


def _tar_raw(build) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        build(tar)
    return buf.getvalue()


def test_a_tarball_that_writes_outside_its_own_tree_installs_nothing(tmp_path: Path) -> None:
    cache = ToolCache(tmp_path / "cache", harden=lambda _p: None)
    victim = tmp_path / "victim"
    victim.write_text("original")

    def build(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("../victim")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"OWNED"))

    with pytest.raises(ToolCacheError):
        cache.ensure(_SHA, _tar_raw(build))

    assert victim.read_text() == "original"
    assert not (tmp_path / "cache" / _SHA).exists()


def test_a_tarball_that_links_out_of_its_own_tree_installs_nothing(tmp_path: Path) -> None:
    # A symlink is the same escape wearing a different hat — and it would be
    # followed later, when something reads the "tool" through the mount.
    cache = ToolCache(tmp_path / "cache", harden=lambda _p: None)

    def build(tar: tarfile.TarFile) -> None:
        info = tarfile.TarInfo("launch")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    with pytest.raises(ToolCacheError):
        cache.ensure(_SHA, _tar_raw(build))

    assert not (tmp_path / "cache" / _SHA).exists()


def test_a_bundle_only_becomes_visible_once_it_is_whole(tmp_path: Path) -> None:
    # `has()` is what decides whether a sandbox mounts this tree, so a
    # half-unpacked bundle appearing under its sha would be mounted and run.
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    seen: list[bool] = []

    def harden_that_fails(path: Path) -> None:
        seen.append(cache.has(_SHA))
        raise OSError("chown: operation not permitted")

    cache = ToolCache(tmp_path, harden=harden_that_fails)
    with pytest.raises(OSError, match="not permitted"):
        cache.ensure(_SHA, _tar({"launch": b"x"}))

    assert seen == [False]  # not visible while it was being written
    assert not cache.has(_SHA)  # and not left behind afterwards
    assert list(tmp_path.iterdir()) == []


def test_the_tree_is_hardened_before_anyone_can_reach_it(tmp_path: Path) -> None:
    hardened: list[Path] = []
    cache = ToolCache(tmp_path, harden=lambda p: hardened.append(p))

    installed = cache.ensure(_SHA, _tar({"launch": b"x"}))

    # Hardening runs on the staging tree, i.e. before the rename that puts the
    # bundle at its sha — never on a directory a sandbox could already be
    # mounting.
    assert hardened and hardened[0] != installed
    assert hardened[0].parent == tmp_path


def test_hardening_makes_the_tree_root_owned_and_unwritable_by_anyone_else(
    tmp_path: Path, monkeypatch
) -> None:
    # The real thing needs root, so assert the two syscalls it makes. This IS
    # the protection: sandboxes run as unprivileged per-item uids, and if one
    # of them could write here it would be rewriting a tool every other
    # sandbox on this host runs.
    tree = tmp_path / "staging"
    (tree / "schemas").mkdir(parents=True)
    (tree / "launch").write_text("#!/bin/sh\n")
    (tree / "launch").chmod(0o777)

    owned: list[tuple[Path, int, int]] = []
    modes: dict[Path, int] = {}
    # lchown, not chown: hardening must not follow a symlink, so it is the
    # one it calls.
    monkeypatch.setattr(os, "lchown", lambda p, u, g: owned.append((Path(p), u, g)))
    monkeypatch.setattr(os, "chmod", lambda p, m: modes.__setitem__(Path(p), m))

    _harden(tree)

    assert {p for p, _, _ in owned} == {tree, tree / "schemas", tree / "launch"}
    assert all(uid == 0 and gid == 0 for _, uid, gid in owned)
    # 0o777 -> 0o755: the owner keeps everything, group and other lose write.
    assert modes[tree / "launch"] & 0o022 == 0
    assert modes[tree / "launch"] & 0o700 == 0o700


def test_the_default_cache_hardens_for_real(tmp_path: Path) -> None:
    # Nothing injected: the production wiring must reach `_harden`, not a
    # no-op that happens to make tests pass.
    assert ToolCache(tmp_path)._harden is _harden


def _install(cache: ToolCache, sha: str, body: bytes = b"x") -> Path:
    return cache.ensure(sha, _tar({"launch": body}))


def test_a_ceiling_is_what_makes_the_reaper_reclaim(tmp_path: Path) -> None:
    """The other half of the default's meaning: eviction is opt-in, and asking
    for it is naming a number. Until then the cache only grows, which is the
    trade this repo makes for every other limit — unset is unlimited."""
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    cache.ensure("b" * 64, _tar({"launch": b"x" * 400}))

    assert cache.sweep(in_use=set()) == []  # no ceiling: kept
    assert cache.sweep(in_use=set(), max_bytes=1) == ["b" * 64]  # a ceiling: reclaimed


def test_an_unreferenced_bundle_is_kept_while_there_is_room(tmp_path: Path) -> None:
    """It is the version somebody may roll back to. Keeping it is what makes a
    rollback a remount instead of a 150MB download — which is the whole reason
    this cache is addressed by content."""
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    previous, current = "a" * 64, "b" * 64
    _install(cache, previous)
    _install(cache, current)

    assert cache.sweep(in_use={current}, max_bytes=10_000_000) == []
    assert cache.has(previous)


def test_sweep_never_touches_a_bundle_a_live_sandbox_has_mounted(tmp_path: Path) -> None:
    # Content-addressed, so "in use" is the only thing that makes a directory
    # unsafe to delete — pulling one out from under a running sandbox would
    # break a tool mid-turn.
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    sha = "a" * 64
    _install(cache, sha)

    assert cache.sweep(in_use={sha}) == []
    assert cache.has(sha)


def test_sweep_evicts_the_oldest_first_when_the_cache_is_over_its_ceiling(
    tmp_path: Path,
) -> None:
    # Rollback stays cheap while there is room, so eviction is by age rather
    # than by anything cleverer: the version someone might roll back to is
    # usually the one they were on until recently.
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    old, mid, new = "a" * 64, "b" * 64, "c" * 64
    for i, sha in enumerate((old, mid, new)):
        path = _install(cache, sha, body=b"x" * 1000)
        os.utime(path, (i, i))

    removed = cache.sweep(in_use=set(), max_bytes=1)

    assert removed == [old, mid, new]  # nothing referenced, so all of it goes
    assert list(tmp_path.iterdir()) == []


def test_the_ceiling_cannot_evict_something_in_use(tmp_path: Path) -> None:
    # A cache over its ceiling is a capacity problem; deleting a bundle a
    # sandbox is running would be a correctness one.
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    live, dead = "a" * 64, "b" * 64
    os.utime(_install(cache, live, body=b"x" * 1000), (1, 1))
    os.utime(_install(cache, dead, body=b"x" * 1000), (2, 2))

    removed = cache.sweep(in_use={live}, max_bytes=1)

    assert removed == [dead]
    assert cache.has(live)


def test_a_cache_over_its_ceiling_with_everything_in_use_keeps_everything(
    tmp_path: Path, caplog
) -> None:
    # There is nothing safe left to delete. Saying "this host needs more disk"
    # is the honest answer; evicting a running tool would trade a capacity
    # problem for a correctness one.
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    sha = "a" * 64
    _install(cache, sha, body=b"x" * 5000)

    with caplog.at_level("WARNING"):
        removed = cache.sweep(in_use={sha}, max_bytes=1)

    assert removed == []
    assert cache.has(sha)
    assert "needs more disk" in caplog.text


def test_sweeping_a_host_that_has_never_installed_anything_is_a_no_op(tmp_path: Path) -> None:
    # The reaper ticks from the moment a host starts, long before any app has
    # asked for a third-party tool.
    assert ToolCache(tmp_path / "never-created").sweep(in_use=set()) == []


def test_a_bundle_with_a_venv_shaped_link_installs(tmp_path: Path) -> None:
    """#674 follow-up: the guard that refuses links OUT of the tree must not
    also refuse the ordinary ones a real bundle is full of — `uv` leaves over a
    thousand of them. The first version of this feature could not unpack a
    single real bundle, and no test said so because every double wrote plain
    files only."""
    cache = ToolCache(tmp_path, harden=lambda _p: None)

    def build(tar: tarfile.TarFile) -> None:
        body = b"#!/bin/sh\n"
        info = tarfile.TarInfo("python/bin/python3.12")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
        link = tarfile.TarInfo(".venv/bin/python")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../python/bin/python3.12"
        tar.addfile(link)

    installed = cache.ensure(_SHA, _tar_raw(build))

    assert (installed / ".venv" / "bin" / "python").is_symlink()
    assert (installed / ".venv" / "bin" / "python").resolve().is_file()


def test_an_installed_tool_can_be_entered_by_the_uid_that_will_run_it(tmp_path: Path) -> None:
    """The staging directory comes from `mkdtemp`, which is 0700 — private,
    correct while a tree is half-written, and wrong the moment it is renamed
    into place.

    Nothing downstream fixes it: the host only symlinks a tool into a
    sandbox's view, and `_harden` clears write bits without granting the read
    and traverse a runner needs. So a third-party tool installed here was
    root-owned 0700, and the unprivileged uid a sandbox runs as could not
    enter it — `PermissionError` on exec, which reads as a broken tool.

    Found by dropping privileges in the MCP runner, where the same cache is
    read by a non-root process."""
    cache = ToolCache(tmp_path, harden=lambda _p: None)

    installed = cache.ensure("a" * 64, _tar({"launch": b"#!/bin/sh\n"}))

    mode = installed.stat().st_mode
    assert mode & 0o055 == 0o055, oct(mode)


def test_hardening_leaves_the_tree_enterable(tmp_path: Path) -> None:
    """`_harden` runs as root on the host and removes what others may WRITE.
    Removing what they may ENTER would stop every sandbox running the tool."""
    root = tmp_path / "tree"
    (root / "inner").mkdir(parents=True)
    (root / "inner" / "launch").write_text("#!/bin/sh\n")
    root.chmod(0o755)

    try:
        _harden(root)
    except PermissionError:  # pragma: no cover - only root can chown
        import pytest

        pytest.skip("chown needs root")

    assert root.stat().st_mode & 0o055 == 0o055
    assert (root / "inner").stat().st_mode & 0o055 == 0o055


def test_hardening_does_not_follow_a_dangling_symlink(tmp_path: Path, monkeypatch) -> None:
    """`data` accepts a dangling RELATIVE symlink — it only refuses absolute
    paths and `..` escapes. `_harden` then followed it: `chown` and `stat` both
    do, so it operated on a path that is not there and raised
    `FileNotFoundError` — out through `ensure`, which catches `TarError` and
    nothing else.

    This module says it is written as if the tarball is hostile. The one thing
    the safe filter lets through is where that has to hold.

    Only the privileged call is replaced. Leaving it real would stop the walk
    at the root directory, as a non-root process, before it ever reached the
    link — a test that passes without having looked."""
    import os

    import pytest

    from sandbox_host.tool_cache import _harden

    root = tmp_path / "tree"
    root.mkdir()
    (root / "dangling").symlink_to("nowhere")
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "lchown", lambda *a, **k: None)

    try:
        _harden(root)
    except FileNotFoundError:
        pytest.fail("followed the dangling link instead of operating on the link itself")


def test_a_dangling_symlink_is_refused_as_a_bad_bundle(tmp_path: Path) -> None:
    """`data` accepts a dangling RELATIVE symlink — it only refuses absolute
    paths and `..` escapes. `_harden` then followed it: `chown` and `stat` both
    do, so the tree it was hardening was not the tree it read.

    The result was a bare `FileNotFoundError` out of `ensure`, which catches
    `TarError` and nothing else. This module says it is written as if the
    tarball is hostile; a crash on the one thing the safe filter lets through
    is where that has to hold."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("dangling")
        info.type = tarfile.SYMTYPE
        info.linkname = "nowhere"
        tar.addfile(info)
        body = b"#!/bin/sh\n"
        launch = tarfile.TarInfo("launch")
        launch.size = len(body)
        tar.addfile(launch, io.BytesIO(body))

    cache = ToolCache(tmp_path)

    # Hardening a real tree needs root; what matters is that it walks the
    # dangling entry without following it.
    try:
        installed = cache.ensure("d" * 64, buf.getvalue())
    except PermissionError:  # pragma: no cover - non-root cannot chown
        import pytest

        pytest.skip("chown needs root")

    assert (installed / "dangling").is_symlink()


def test_two_callers_installing_the_same_sha_do_not_fight(tmp_path: Path, monkeypatch) -> None:
    """Two sandboxes open the same tool at once. The loser's rename lands on a
    directory that is already there, and `ENOTEMPTY` came straight back out —
    one turn failing because another turn succeeded.

    The bytes are identical by construction: the sha is the name. So the loser
    has nothing to do but use what the winner installed."""
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    data = _tar({"launch": b"#!/bin/sh\n"})
    sha = "c" * 64
    target = cache.path_for(sha)

    def the_other_caller_got_there_first(self, dst):
        Path(dst).mkdir(parents=True, exist_ok=True)
        raise OSError(39, "Directory not empty")

    monkeypatch.setattr(Path, "rename", the_other_caller_got_there_first)

    assert cache.ensure(sha, data) == target


def test_a_rename_that_failed_for_any_other_reason_still_raises(
    tmp_path: Path, monkeypatch
) -> None:
    """Losing a race and a full disk look the same from here — one `OSError`.
    They are told apart by whether the bundle is actually there afterwards.
    Swallowing both would return a path to a directory that does not exist."""
    import pytest

    cache = ToolCache(tmp_path, harden=lambda _p: None)

    def no_space(self, dst):  # noqa: ARG001
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "rename", no_space)

    with pytest.raises(OSError, match="No space"):
        cache.ensure("e" * 64, _tar({"launch": b"#!/bin/sh\n"}))


def test_no_ceiling_keeps_everything(tmp_path: Path) -> None:
    """`None` meant "keep nothing", which is backwards twice over.

    It contradicted its caller — the sweeper's comment says unreferenced
    bundles are kept while there is room, because that is what makes a
    rollback a remount instead of a 150MB download — and it was the DEFAULT,
    so on an unconfigured deployment a bundle vanished within minutes of its
    sandbox being reaped and rollback was never a remount.

    It also inverted this repo's convention everywhere else: unset means no
    limit (`filestore.workspace_quota`, the cgroup `max`). One mental model
    flipping in one place is the next person's afternoon."""
    cache = ToolCache(tmp_path, harden=lambda _p: None)
    kept = cache.ensure("a" * 64, _tar({"launch": b"#!/bin/sh\n"}))

    removed = cache.sweep(in_use=set())

    assert removed == []
    assert kept.is_dir()
