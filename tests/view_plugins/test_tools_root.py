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


def test_a_rebuild_replaces_the_previous_merge(tmp_path: Path):
    _plugin(tmp_path / "plugins", "chart")
    plugins = discover_view_plugins(tmp_path / "plugins")
    merged = merge_tools_root(None, [], plugins, tmp_path / "merged")
    assert merged is not None
    (merged / "stale-plugin").mkdir()
    merge_tools_root(None, [], plugins, tmp_path / "merged")
    assert not (merged / "stale-plugin").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["merged", "plugins"]  # no temp left


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
