"""Runtime view-plugin discovery (#847/#848 PR1 P2).

Strict, like tools discovery: a malformed plugin refuses boot with a message
naming the plugin and the field. A missing dir is "no plugins", not an error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.view_plugins import (
    BUILTIN_VIEW_KINDS,
    ViewPluginError,
    discover_view_plugins,
)


def _plugin(root: Path, name: str, manifest: dict | None = None, *, web: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    body = {"name": name, "sdk": "1", "kinds": [f"{name}-kind"]} if manifest is None else manifest
    (d / "plugin.json").write_text(json.dumps(body))
    if web:
        (d / "web").mkdir()
        (d / "web" / "index.js").write_text("export {};\n")
    return d


def test_missing_dir_means_no_plugins(tmp_path: Path):
    assert discover_view_plugins(tmp_path / "nope") == []


def test_discovers_a_well_formed_plugin(tmp_path: Path):
    d = _plugin(
        tmp_path,
        "chart",
        {
            "name": "chart",
            "sdk": "1",
            "kinds": ["chart"],
            "views": [{"kind": "chart", "when": "numbers over a category"}],
            "skill": "skill",
            "sandbox": {"bundle": "sandbox"},
        },
    )
    (d / "skill").mkdir()
    (d / "skill" / "SKILL.md").write_text("---\nname: chart\n---\n")
    [p] = discover_view_plugins(tmp_path)
    assert p.name == "chart"
    assert p.dir == d
    assert p.manifest.kinds == ["chart"]
    assert p.manifest.views[0].when == "numbers over a category"
    assert p.manifest.sandbox is not None and p.manifest.sandbox.bundle == "sandbox"
    assert p.skill_dir == d / "skill"


def test_non_dir_entries_are_skipped(tmp_path: Path):
    (tmp_path / "README.md").write_text("hi")
    _plugin(tmp_path, "a")
    assert [p.name for p in discover_view_plugins(tmp_path)] == ["a"]


def _refusal(tmp_path: Path) -> str:
    with pytest.raises(ViewPluginError) as e:
        discover_view_plugins(tmp_path)
    return str(e.value)


def test_unknown_key_refuses_boot_naming_plugin_and_field(tmp_path: Path):
    _plugin(tmp_path, "bad", {"name": "bad", "sdk": "1", "kinds": ["k"], "colour": "red"})
    msg = _refusal(tmp_path)
    assert "'bad'" in msg and "colour" in msg


def test_missing_manifest_refuses_boot(tmp_path: Path):
    (tmp_path / "half").mkdir()
    msg = _refusal(tmp_path)
    assert "'half'" in msg and "plugin.json" in msg


def test_malformed_json_refuses_boot(tmp_path: Path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "plugin.json").write_text("{not json")
    assert "'broken'" in _refusal(tmp_path)


def test_name_must_match_the_folder(tmp_path: Path):
    _plugin(tmp_path, "folder", {"name": "other", "sdk": "1", "kinds": ["k"]})
    msg = _refusal(tmp_path)
    assert "'folder'" in msg and "name" in msg


@pytest.mark.parametrize("name", ["Bad", "a b", "../x", "-lead", ""])
def test_name_must_be_a_url_safe_slug(tmp_path: Path, name: str):
    d = tmp_path / "p"
    d.mkdir()
    (d / "plugin.json").write_text(json.dumps({"name": name, "sdk": "1", "kinds": ["k"]}))
    assert "name" in _refusal(tmp_path)


def test_needs_at_least_one_kind(tmp_path: Path):
    _plugin(tmp_path, "p", {"name": "p", "sdk": "1", "kinds": []})
    msg = _refusal(tmp_path)
    assert "'p'" in msg and "kinds" in msg


def test_duplicate_kind_within_a_plugin(tmp_path: Path):
    _plugin(tmp_path, "p", {"name": "p", "sdk": "1", "kinds": ["k", "k"]})
    assert "kinds" in _refusal(tmp_path)


def test_a_kind_claimed_by_two_plugins_names_both(tmp_path: Path):
    _plugin(tmp_path, "a", {"name": "a", "sdk": "1", "kinds": ["shared"]})
    _plugin(tmp_path, "b", {"name": "b", "sdk": "1", "kinds": ["shared"]})
    msg = _refusal(tmp_path)
    assert "'a'" in msg and "'b'" in msg and "shared" in msg


@pytest.mark.parametrize("kind", sorted(BUILTIN_VIEW_KINDS))
def test_a_kind_may_not_shadow_a_builtin_or_reserved_kind(tmp_path: Path, kind: str):
    _plugin(tmp_path, "p", {"name": "p", "sdk": "1", "kinds": [kind]})
    msg = _refusal(tmp_path)
    assert "'p'" in msg and kind in msg


def test_a_views_entry_must_name_one_of_its_own_kinds(tmp_path: Path):
    _plugin(
        tmp_path,
        "p",
        {"name": "p", "sdk": "1", "kinds": ["k"], "views": [{"kind": "other", "when": "x"}]},
    )
    msg = _refusal(tmp_path)
    assert "views" in msg and "other" in msg


def test_a_views_entry_needs_a_when(tmp_path: Path):
    views = [{"kind": "k", "when": " "}]
    _plugin(tmp_path, "p", {"name": "p", "sdk": "1", "kinds": ["k"], "views": views})
    assert "when" in _refusal(tmp_path)


def test_sandbox_names_exactly_one_source(tmp_path: Path):
    _plugin(tmp_path, "p", {"name": "p", "sdk": "1", "kinds": ["k"], "sandbox": {}})
    assert "sandbox" in _refusal(tmp_path)


def test_sandbox_may_not_name_both_sources(tmp_path: Path):
    _plugin(
        tmp_path,
        "p",
        {
            "name": "p",
            "sdk": "1",
            "kinds": ["k"],
            "sandbox": {"bundle": "s", "artifact": "https://x/a"},
        },
    )
    assert "sandbox" in _refusal(tmp_path)


def test_sandbox_artifact_is_accepted(tmp_path: Path):
    _plugin(
        tmp_path,
        "p",
        {
            "name": "p",
            "sdk": "1",
            "kinds": ["k"],
            "sandbox": {"artifact": "https://reg/x@sha256:ab"},
        },
    )
    [p] = discover_view_plugins(tmp_path)
    assert (
        p.manifest.sandbox is not None and p.manifest.sandbox.artifact == "https://reg/x@sha256:ab"
    )


def test_a_declared_skill_must_have_a_skill_md(tmp_path: Path):
    _plugin(tmp_path, "p", {"name": "p", "sdk": "1", "kinds": ["k"], "skill": "skill"})
    msg = _refusal(tmp_path)
    assert "'p'" in msg and "skill" in msg and "SKILL.md" in msg


def test_a_skill_path_may_not_leave_the_plugin(tmp_path: Path):
    _plugin(tmp_path, "p", {"name": "p", "sdk": "1", "kinds": ["k"], "skill": "../elsewhere"})
    assert "skill" in _refusal(tmp_path)


def test_a_plugin_with_no_built_web_entry_refuses_boot(tmp_path: Path):
    _plugin(tmp_path, "p", web=False)
    msg = _refusal(tmp_path)
    assert "'p'" in msg and "web/index.js" in msg


def test_sdk_must_be_a_major_version_string(tmp_path: Path):
    _plugin(tmp_path, "p", {"name": "p", "sdk": "one", "kinds": ["k"]})
    assert "sdk" in _refusal(tmp_path)
