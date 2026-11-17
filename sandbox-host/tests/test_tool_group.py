"""Third-party bundles are readable by the tool group only
(docs/plan-tool-identity-secrets.md P2).

Before: `root:root`, group/other may read — so the agent, running as the
sandbox uid, could `cat` a tool's source and any credential written into it.
After: `root:<tool group>`, and the group gets exactly the read/traverse bits
"other" used to have — so the tool's uid reaches precisely what a sandbox could
reach before, and "other" (the agent) reaches nothing.

First-party tools (`builtin/`) and the engineer's MCP runner are untouched.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

from sandbox_host.config import SandboxHostSettings
from sandbox_host.service import build_sandbox, build_tool_resolver
from sandbox_host.tool_cache import EXT_DIR, ToolCache, _GroupHardener

GROUP = 4242


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755 if name == "launch" else 0o644
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _record(monkeypatch) -> tuple[list[tuple[Path, int, int]], dict[Path, int]]:
    owned: list[tuple[Path, int, int]] = []
    modes: dict[Path, int] = {}
    monkeypatch.setattr(os, "lchown", lambda p, u, g: owned.append((Path(p), u, g)))
    monkeypatch.setattr(os, "chmod", lambda p, m: modes.__setitem__(Path(p), m))
    return owned, modes


def test_the_group_gets_what_other_had_and_other_gets_nothing(tmp_path, monkeypatch):
    tree = tmp_path / "staging"
    (tree / "lib").mkdir(parents=True)
    (tree / "launch").write_text("#!/bin/sh\n")
    (tree / "launch").chmod(0o755)
    (tree / "lib" / "creds.py").write_text("PASSWORD = 'x'\n")
    (tree / "lib" / "creds.py").chmod(0o644)
    (tree / "lib" / "private").write_text("")
    (tree / "lib" / "private").chmod(0o600)
    tree.chmod(0o755)
    (tree / "lib").chmod(0o755)
    owned, modes = _record(monkeypatch)

    _GroupHardener(GROUP)(tree)

    assert {p for p, _, _ in owned} == {
        tree,
        tree / "lib",
        tree / "launch",
        tree / "lib" / "creds.py",
        tree / "lib" / "private",
    }
    assert all(uid == 0 and gid == GROUP for _, uid, gid in owned)
    assert modes[tree] == 0o750
    assert modes[tree / "lib"] == 0o750
    assert modes[tree / "launch"] == 0o750
    assert modes[tree / "lib" / "creds.py"] == 0o640
    # Nobody but root could read it before; the group gains nothing either.
    assert modes[tree / "lib" / "private"] == 0o600


def test_group_hardening_never_follows_a_link(tmp_path, monkeypatch):
    """Same rule as `_harden`: `lchown`, and no `chmod` through a symlink — a
    dangling relative one is something the safe tar filter lets through."""
    tree = tmp_path / "staging"
    tree.mkdir()
    (tree / "dangling").symlink_to("nowhere")
    owned, modes = _record(monkeypatch)

    _GroupHardener(GROUP)(tree)

    assert (tree / "dangling", 0, GROUP) in owned
    assert tree / "dangling" not in modes


def test_bundles_already_on_the_host_are_hardened_again(tmp_path):
    """A host that unpacked bundles before this change keeps them on disk, and
    `ensure` returns early for a sha it already has — so without this, every
    tool installed before the upgrade would stay readable by the agent."""
    sha_a, sha_b = "a" * 64, "b" * 64
    plain = ToolCache(tmp_path, harden=lambda _p: None)
    plain.ensure(sha_a, _tar({"launch": b"#!/bin/sh\n"}))
    plain.ensure(sha_b, _tar({"launch": b"#!/bin/sh\n"}))
    (tmp_path / f".{sha_a}.half").mkdir()  # an interrupted unpack: not a bundle

    hardened: list[Path] = []
    ToolCache(tmp_path, harden=hardened.append).reharden_installed()

    assert sorted(hardened) == [tmp_path / sha_a, tmp_path / sha_b]


def test_the_production_cache_hardens_for_the_group_the_tools_run_with(tmp_path, monkeypatch):
    """One number, two consumers: the bundles' group and the group a tool
    command holds. Derived in one place, or a host could unpack bundles for a
    group no tool ever joins — and then no third-party tool runs at all."""
    monkeypatch.setenv("TOOL_BUILDER_ID", "tool-builder:2026.07")
    settings = SandboxHostSettings(
        uid_min=300000,
        uid_max=300009,
        tools_dir=str(tmp_path / "tools"),
        cgroup_root=str(tmp_path / "cg"),
        root=str(tmp_path / "sb"),
    )
    resolver = build_tool_resolver(settings)
    assert resolver is not None
    hardener = resolver._cache._harden
    assert isinstance(hardener, _GroupHardener)
    assert hardener.group == build_sandbox(settings)._tool_gid == 300000


def test_the_production_cache_rehardens_what_it_finds_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_BUILDER_ID", "tool-builder:2026.07")
    ext = tmp_path / "tools" / EXT_DIR
    ToolCache(ext, harden=lambda _p: None).ensure("c" * 64, _tar({"launch": b"x"}))
    settings = SandboxHostSettings(
        uid_min=300000, uid_max=300009, tools_dir=str(tmp_path / "tools")
    )
    owned, _modes = _record(monkeypatch)

    build_tool_resolver(settings)

    assert (ext / ("c" * 64), 0, 300000) in owned
