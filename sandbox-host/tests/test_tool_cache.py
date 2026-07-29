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
    monkeypatch.setattr(os, "chown", lambda p, u, g: owned.append((Path(p), u, g)))
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
