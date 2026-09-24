"""`kind: local` — plugin sandbox bundles join the tools root (#847/#848 P6).

The jail bind-mounts ONE root at `/.tools`, and a symlink out of it breaks
inside the chroot, so the plugin bundles are COPIED into a merged root beside
the prebuilt packages (which are hard-linked in, not duplicated).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from workspace_app.view_plugins import ViewPluginError, discover_view_plugins
from workspace_app.view_plugins.sandbox_half import merge_tools_root


def _package(prebuilt: Path, name: str) -> None:
    d = prebuilt / name
    (d / "schemas").mkdir(parents=True)
    (d / "commands.json").write_text("[]")
    (d / "launch").write_text("#!/bin/sh\necho tool\n")
    (d / "launch").chmod(0o755)


def _plugin(root: Path, name: str, *, bundle: bool = True, launch: bool = True) -> None:
    d = root / name
    (d / "web").mkdir(parents=True)
    (d / "web" / "index.js").write_text("export {};\n")
    manifest: dict = {"name": name, "sdk": "1", "kinds": [name]}
    if bundle:
        manifest["sandbox"] = {"bundle": "sandbox"}
        (d / "sandbox").mkdir()
        if launch:
            (d / "sandbox" / "launch").write_text(f"#!/bin/sh\necho {name}\n")
            (d / "sandbox" / "launch").chmod(0o755)
    (d / "plugin.json").write_text(json.dumps(manifest))


def test_no_bundle_plugins_leaves_the_prebuilt_root_alone(tmp_path: Path):
    prebuilt = tmp_path / "prebuilt"
    _package(prebuilt, "data-fetch")
    _plugin(tmp_path / "plugins", "table-only", bundle=False)
    plugins = discover_view_plugins(tmp_path / "plugins")
    got = merge_tools_root(prebuilt, ["data-fetch"], plugins, tmp_path / "merged")
    assert got == prebuilt
    assert not (tmp_path / "merged").exists()


def test_no_packages_and_no_bundles_is_no_tools_root(tmp_path: Path):
    assert merge_tools_root(tmp_path / "prebuilt", [], [], tmp_path / "merged") is None


def test_merges_packages_and_plugin_bundles_into_one_real_root(tmp_path: Path):
    prebuilt = tmp_path / "prebuilt"
    _package(prebuilt, "data-fetch")
    _plugin(tmp_path / "plugins", "chart")
    plugins = discover_view_plugins(tmp_path / "plugins")
    merged = merge_tools_root(prebuilt, ["data-fetch"], plugins, tmp_path / "merged")

    assert merged is not None and merged == tmp_path / "merged"
    for name in ("data-fetch", "chart"):
        launch = merged / name / "launch"
        assert launch.is_file() and not launch.is_symlink(), name
        assert os.access(launch, os.X_OK), name
    # No path inside may lead out of the root: the jail bind-mounts only this.
    assert not any(p.is_symlink() for p in merged.rglob("*"))
    # The package is linked, not duplicated.
    assert (merged / "data-fetch" / "launch").stat().st_ino == (
        prebuilt / "data-fetch" / "launch"
    ).stat().st_ino


def test_plugins_only_still_gets_a_tools_root(tmp_path: Path):
    """The `__main__` bug this replaces: discovery was skipped entirely when
    PACKAGES was empty, so a plugin-only deployment had no `/.tools` at all."""
    _plugin(tmp_path / "plugins", "chart")
    plugins = discover_view_plugins(tmp_path / "plugins")
    merged = merge_tools_root(None, [], plugins, tmp_path / "merged")
    assert merged is not None and (merged / "chart" / "launch").is_file()


def test_unchanged_inputs_leave_the_merged_root_untouched(tmp_path: Path):
    """The API and the blob-gc worker both boot `build_app`: the second boot
    must not tear down the tree the first one's live sandboxes are using."""
    _plugin(tmp_path / "plugins", "chart")
    plugins = discover_view_plugins(tmp_path / "plugins")
    merged = merge_tools_root(None, [], plugins, tmp_path / "merged")
    assert merged is not None
    (merged / "in-use").write_text("a sandbox's view of this tree")
    before = merged.stat().st_ino
    assert merge_tools_root(None, [], plugins, tmp_path / "merged") == merged
    assert merged.stat().st_ino == before
    assert (merged / "in-use").exists()


def test_changed_inputs_rebuild_it_whole(tmp_path: Path):
    _plugin(tmp_path / "plugins", "chart")
    plugins = discover_view_plugins(tmp_path / "plugins")
    merged = merge_tools_root(None, [], plugins, tmp_path / "merged")
    assert merged is not None
    (merged / "stale").mkdir()
    (tmp_path / "plugins" / "chart" / "sandbox" / "launch").write_text("#!/bin/sh\necho v2\n")
    merge_tools_root(None, [], plugins, tmp_path / "merged")
    assert not (merged / "stale").exists()
    assert "v2" in (merged / "chart" / "launch").read_text()
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("merged."))
    assert leftovers == ["merged.lock", "merged.stamp"]  # no staging or retired tree left


def _merge_in_a_process(root: str, q) -> None:  # module-level: picklable for spawn
    try:
        plugins = discover_view_plugins(Path(root) / "plugins")
        merge_tools_root(None, [], plugins, Path(root) / "merged")
        q.put("ok")
    except Exception as e:  # noqa: BLE001 — reported to the parent
        q.put(f"{type(e).__name__}: {e}")


def test_two_processes_merging_at_once_both_boot(tmp_path: Path):
    import multiprocessing as mp

    _plugin(tmp_path / "plugins", "chart")
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_merge_in_a_process, args=(str(tmp_path), q)) for _ in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert sorted(q.get(timeout=5) for _ in procs) == ["ok", "ok", "ok"]
    assert (tmp_path / "merged" / "chart" / "launch").is_file()


def test_a_plugin_named_like_a_tool_package_refuses_boot_naming_both(tmp_path: Path):
    prebuilt = tmp_path / "prebuilt"
    _package(prebuilt, "chart")
    _plugin(tmp_path / "plugins", "chart")
    plugins = discover_view_plugins(tmp_path / "plugins")
    with pytest.raises(ViewPluginError, match=r"view plugin 'chart'.*tool package 'chart'"):
        merge_tools_root(prebuilt, ["chart"], plugins, tmp_path / "merged")


def test_a_bundle_without_launch_refuses_boot(tmp_path: Path):
    _plugin(tmp_path / "plugins", "chart", launch=False)
    plugins = discover_view_plugins(tmp_path / "plugins")
    with pytest.raises(ViewPluginError, match=r"'chart'.*launch"):
        merge_tools_root(None, [], plugins, tmp_path / "merged")


def test_a_prebuilt_bundle_is_stamped_by_its_build_marker(tmp_path: Path):
    """A rebuild rewrites `.built`; nothing else about a prebuilt package needs
    walking at boot (a python + venv is thousands of files)."""
    prebuilt = tmp_path / "prebuilt"
    _package(prebuilt, "data-fetch")
    (prebuilt / "data-fetch" / ".built").write_text("hash-1")
    _plugin(tmp_path / "plugins", "chart")
    plugins = discover_view_plugins(tmp_path / "plugins")
    merged = merge_tools_root(prebuilt, ["data-fetch"], plugins, tmp_path / "merged")
    assert merged is not None
    (merged / "marker").write_text("x")
    merge_tools_root(prebuilt, ["data-fetch"], plugins, tmp_path / "merged")
    assert (merged / "marker").exists()  # same stamp: untouched
    (prebuilt / "data-fetch" / ".built").write_text("hash-2")
    merge_tools_root(prebuilt, ["data-fetch"], plugins, tmp_path / "merged")
    assert not (merged / "marker").exists()  # rebuilt
