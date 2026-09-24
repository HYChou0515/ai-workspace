"""`python -m workspace_app.view_plugin build` (#847/#848 PR1 P10).

Installs a plugin from its source folder: the web half through the ONE shared
script (`view-plugins/build-web.mjs`, which the image's stage runs too), and a
`sandbox-src/` uv project prebuilt into `<dest>/<name>/sandbox` — always
forced: this is an explicit build, so no stamp is trusted (a path dependency
outside `sandbox-src/` changes nothing the stamp hashes).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.view_plugin import cli


def _src(root: Path, name: str, *, sandbox_src: bool = False, bundle: str = "sandbox") -> Path:
    d = root / name
    (d / "web").mkdir(parents=True)
    manifest: dict = {"name": name, "sdk": "1", "kinds": [name]}
    if sandbox_src:
        manifest["sandbox"] = {"bundle": bundle}
        (d / "sandbox-src").mkdir()
        (d / "sandbox-src" / "pyproject.toml").write_text(
            f'[project]\nname = "{name}-sb"\nversion = "0"\n'
            f'[project.scripts]\n{name}-cmd = "x:main"\n'
        )
    (d / "plugin.json").write_text(json.dumps(manifest))
    return d


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, list] = {"node": [], "prebuild": []}

    def fake_run(argv, check):
        seen["node"].append(argv)

    def fake_build(*, name, source, dst, force):
        seen["prebuild"].append((name, source, dst, force))

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "build_package", fake_build)
    return seen


def test_builds_the_web_half_with_the_shared_script(tmp_path, calls):
    src = _src(tmp_path / "src", "csv-table")
    assert cli.main(["build", str(src), str(tmp_path / "out")]) == 0
    [argv] = calls["node"]
    assert argv[0] == "node"
    assert argv[1].endswith("view-plugins/build-web.mjs")
    assert argv[2:] == [str(src), str(tmp_path / "out")]
    assert calls["prebuild"] == []


def test_prebuilds_a_sandbox_src_forced_into_the_installed_bundle(tmp_path, calls):
    src = _src(tmp_path / "src", "chart", sandbox_src=True)
    assert cli.main(["build", str(src), str(tmp_path / "out")]) == 0
    assert calls["prebuild"] == [
        ("chart-cmd", src / "sandbox-src", tmp_path / "out" / "chart" / "sandbox", True)
    ]


def test_a_sandbox_src_needs_plugin_json_to_name_the_installed_bundle(tmp_path, calls, capsys):
    src = _src(tmp_path / "src", "chart", sandbox_src=True, bundle="elsewhere")
    assert cli.main(["build", str(src), str(tmp_path / "out")]) == 1
    assert '"bundle": "sandbox"' in capsys.readouterr().out


def test_all_builds_every_plugin_folder(tmp_path, calls):
    _src(tmp_path / "src", "a")
    _src(tmp_path / "src", "b")
    (tmp_path / "src" / "build-web.mjs").write_text("")  # a file, not a plugin
    (tmp_path / "src" / "notes").mkdir()  # a folder with no plugin.json
    assert cli.main(["build", "--all", str(tmp_path / "src"), str(tmp_path / "out")]) == 0
    assert [a[2] for a in calls["node"]] == [
        str(tmp_path / "src" / "a"),
        str(tmp_path / "src" / "b"),
    ]
