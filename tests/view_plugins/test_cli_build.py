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


def _src(
    root: Path,
    name: str,
    *,
    sandbox_src: bool = False,
    bundle: str = "sandbox",
    isolated: bool = True,
) -> Path:
    d = root / name
    (d / "web").mkdir(parents=True)
    manifest: dict = {"name": name, "sdk": "1", "kinds": [name]}
    if sandbox_src:
        manifest["sandbox"] = {"bundle": bundle}
        (d / "sandbox-src").mkdir()
        (d / "sandbox-src" / "pyproject.toml").write_text(
            f'[project]\nname = "{name}-sb"\nversion = "0"\n'
            f'[project.scripts]\n{name}-cmd = "x:main"\n'
            + ('[tool.workspace-tool]\nlaunch = "isolated"\n' if isolated else "")
        )
    (d / "plugin.json").write_text(json.dumps(manifest))
    return d


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, list] = {"node": [], "prebuild": []}

    def fake_run(argv, check):
        # Stands in for build-web.mjs: what it installs is what `check` reads.
        seen["node"].append(argv)
        src, dest = Path(argv[2]), Path(argv[3])
        name = json.loads((src / "plugin.json").read_text())["name"]
        (dest / name / "web").mkdir(parents=True, exist_ok=True)
        (dest / name / "web" / "index.js").write_text("export {};\n")
        (dest / name / "plugin.json").write_text((src / "plugin.json").read_text())

    def fake_build(*, name, source, dst, force):
        seen["prebuild"].append((name, source, dst, force))
        dst.mkdir(parents=True)
        (dst / "launch").write_text("#!/bin/sh\n")
        (dst / "launch").chmod(0o755)
        (dst / "commands.json").write_text("[]")

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


def test_a_build_that_installs_a_broken_plugin_fails(tmp_path, monkeypatch, capsys):
    """`build` checks what it installed — a web half carrying React fails here,
    not in a user's panel."""
    src = _src(tmp_path / "src", "leaky")

    def leaky_node(argv, check):
        out = Path(argv[3]) / "leaky"
        (out / "web").mkdir(parents=True)
        (out / "web" / "index.js").write_text('const x = "react.transitional.element";\n')
        (out / "plugin.json").write_text((src / "plugin.json").read_text())

    monkeypatch.setattr(cli.subprocess, "run", leaky_node)
    assert cli.main(["build", str(src), str(tmp_path / "out")]) == 1
    assert "own copy of React" in capsys.readouterr().out


def test_a_sandbox_src_must_opt_into_the_isolated_launcher(tmp_path, calls, capsys):
    """A plugin's commands are the platform's: a user's `pip install --upgrade`
    must not change what they compute (#581's user-site-first launcher is for
    tools, not plugins)."""
    src = _src(tmp_path / "src", "chart", sandbox_src=True, isolated=False)
    assert cli.main(["build", str(src), str(tmp_path / "out")]) == 1
    assert 'launch = "isolated"' in capsys.readouterr().out
    assert calls["prebuild"] == []
