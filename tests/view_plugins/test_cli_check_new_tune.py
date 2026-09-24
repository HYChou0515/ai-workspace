"""`view_plugin check / new / tune` (#847/#848 PR1 P11)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_app.view_plugin import cli, scaffold
from workspace_app.view_plugin.check import check_plugin

WEB_REACT = Path(__file__).resolve().parents[2] / "web" / "node_modules" / "react" / "cjs"


def _installed(root: Path, name: str, js: str = "export {};\n", **manifest_extra) -> Path:
    d = root / name
    (d / "web").mkdir(parents=True)
    (d / "web" / "index.js").write_text(js)
    manifest = {"name": name, "sdk": "1", "kinds": [name], **manifest_extra}
    (d / "plugin.json").write_text(json.dumps(manifest))
    return d


def _config(tmp_path: Path, plugins: Path) -> str:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"view_plugins:\n  dir: {plugins}\n")
    return str(cfg)


# ── check ──────────────────────────────────────────────────────────────────


def test_a_production_build_with_externals_passes(tmp_path):
    js = (
        'import { jsx } from "react/jsx-runtime";\n'
        'import { registerViewKind } from "@aiws/view-sdk";\n'
    )
    assert check_plugin(_installed(tmp_path, "ok", js)).errors == []


@pytest.mark.parametrize("cjs", ["react.production.js", "react-jsx-runtime.production.js"])
def test_a_build_carrying_its_own_react_is_refused(tmp_path, cjs):
    """The fixture IS React's own shipped code, as a bundler would inline it."""
    js = (WEB_REACT / cjs).read_text()
    [err] = check_plugin(_installed(tmp_path, "leaky", js)).errors
    assert "own copy of React" in err and "external" in err.lower()


def test_a_development_build_is_refused(tmp_path):
    js = 'import { jsxDEV } from "react/jsx-dev-runtime";\n'
    [err] = check_plugin(_installed(tmp_path, "dev", js)).errors
    assert "development build" in err and "production" in err


def test_chunks_are_checked_too(tmp_path):
    d = _installed(tmp_path, "chunky")
    (d / "web" / "chunk-a.js").write_text("const x = 'react.transitional.element';")
    assert check_plugin(d).errors


def test_a_bad_manifest_is_reported_by_name(tmp_path):
    [err] = check_plugin(_installed(tmp_path, "bad", colour="red")).errors
    assert "'bad'" in err and "colour" in err


def test_validate_declared_needs_a_validate_command(tmp_path):
    d = _installed(tmp_path, "v", sandbox={"bundle": "sandbox", "validate": True})
    (d / "sandbox").mkdir()
    (d / "sandbox" / "launch").write_text("#!/bin/sh\n")
    (d / "sandbox" / "launch").chmod(0o755)
    (d / "sandbox" / "commands.json").write_text('[{"name": "query"}]')
    [err] = check_plugin(d).errors
    assert "validate" in err
    (d / "sandbox" / "commands.json").write_text('[{"name": "validate"}]')
    assert check_plugin(d).errors == []


def test_a_bundle_not_in_the_dir_is_only_a_note(tmp_path):
    r = check_plugin(_installed(tmp_path, "remote", sandbox={"bundle": "sandbox"}))
    assert r.errors == [] and "sandbox-host" in r.notes[0]


def test_check_command_exit_codes(tmp_path, capsys):
    plugins = tmp_path / "plugins"
    _installed(plugins, "good")
    cfg = _config(tmp_path, plugins)
    assert cli.main(["check", "--config", cfg]) == 0
    _installed(plugins, "dev", 'import "react/jsx-dev-runtime";')
    assert cli.main(["check", "--config", cfg]) == 1
    assert cli.main(["check", "good", "--config", cfg]) == 0
    assert "✗ dev" in capsys.readouterr().out


def test_check_catches_a_kind_claimed_by_two_plugins(tmp_path, capsys):
    plugins = tmp_path / "plugins"
    _installed(plugins, "a", kinds=["same"])
    _installed(plugins, "b", kinds=["same"])
    assert cli.main(["check", "--config", _config(tmp_path, plugins)]) == 1
    assert "claimed by two plugins" in capsys.readouterr().out


# ── new ────────────────────────────────────────────────────────────────────


def test_new_writes_the_web_half_and_a_manifest(tmp_path):
    paths = scaffold.scaffold_plugin(tmp_path, "heat")
    rel = sorted(str(p.relative_to(tmp_path / "heat")) for p in paths)
    assert rel == [
        "plugin.json",
        "web/package.json",
        "web/src/View.tsx",
        "web/src/index.tsx",
        "web/tsconfig.json",
        "web/vite.config.ts",
    ]
    manifest = json.loads((tmp_path / "heat" / "plugin.json").read_text())
    assert manifest["kinds"] == ["heat"] and "sandbox" not in manifest and "skill" not in manifest
    vite = (tmp_path / "heat" / "web" / "vite.config.ts").read_text()
    for ext in ("react", "react/jsx-runtime", "react-dom", "@aiws/view-sdk"):
        assert f'"{ext}"' in vite


def test_new_with_sandbox_and_skill(tmp_path, monkeypatch):
    locked: list = []
    monkeypatch.setattr(scaffold.subprocess, "run", lambda argv, check: locked.append(argv))
    scaffold.scaffold_plugin(tmp_path, "heat", with_sandbox=True, with_skill=True)
    d = tmp_path / "heat"
    manifest = json.loads((d / "plugin.json").read_text())
    assert manifest["sandbox"] == {"bundle": "sandbox", "validate": True}
    assert manifest["skill"] == "skill"
    assert 'launch = "isolated"' in (d / "sandbox-src" / "pyproject.toml").read_text()
    assert locked == [["uv", "lock", "--directory", str(d / "sandbox-src")]]
    names = sorted(p.name for p in (d / "scenarios").glob("*.json"))
    assert names == ["draws-the-view.json", "leaves-unrelated-work-alone.json"]
    expects = [
        json.loads(p.read_text())["expect"] for p in sorted((d / "scenarios").glob("*.json"))
    ]
    assert "must_call" in expects[0] and "must_not_call" in expects[1]


def test_new_refuses_a_bad_name_or_an_existing_folder(tmp_path, capsys):
    assert cli.main(["new", "Bad Name", "--root", str(tmp_path)]) == 1
    (tmp_path / "taken").mkdir()
    assert cli.main(["new", "taken", "--root", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().out


def test_the_scaffold_sandbox_cli_answers_the_three_stage_contract(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setattr(scaffold.subprocess, "run", lambda argv, check: None)
    scaffold.scaffold_plugin(tmp_path, "heat", with_sandbox=True)
    monkeypatch.undo()  # `scaffold.subprocess` IS `subprocess`
    cli_py = tmp_path / "heat" / "sandbox-src" / "src" / "heat_sandbox" / "cli.py"
    boot = f"import runpy; runpy.run_path({str(cli_py)!r}, run_name='x')['main']()"

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, "-c", boot, *args]
        return subprocess.run(argv, capture_output=True, text=True, cwd=tmp_path)

    listing = json.loads(run().stdout)
    assert listing == [{"name": "validate", "description": "Check a view file before it is shown."}]
    assert json.loads(run("validate").stdout)["name"] == "validate"
    (tmp_path / "v.ai.yaml").write_text("view: heat\n")
    ok = run("validate", '{"path": "v.ai.yaml"}')
    assert ok.returncode == 0 and ok.stdout.strip().startswith("v.ai.yaml")
    bad = run("validate", '{"path": "nope.ai.yaml"}')
    assert bad.returncode == 1 and "no such view file" in bad.stderr


# ── tune ───────────────────────────────────────────────────────────────────


def _tunable(plugins: Path, *, skill=True, scenarios=True) -> Path:
    d = _installed(plugins, "heat", **({"skill": "skill"} if skill else {}))
    if skill:
        (d / "skill").mkdir()
        (d / "skill" / "SKILL.md").write_text("---\nname: heat\ndescription: d\n---\nbody\n")
    if scenarios:
        (d / "scenarios").mkdir()
        (d / "scenarios" / "a.json").write_text("{}")
    return d


def test_tune_runs_skill_eval_on_the_installed_skill_with_control(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    d = _tunable(plugins)
    seen: list = []
    monkeypatch.setattr("workspace_app.skill_eval.__main__.main", lambda argv: seen.append(argv))
    cfg = _config(tmp_path, plugins)
    out = str(tmp_path / "out")
    argv_in = ["tune", "heat", "--config", cfg, "--preset", "p1", "-o", out, "--num-ctx", "16384"]
    assert cli.main(argv_in) == 0
    [argv] = seen
    assert argv[argv.index("--skill") + 1] == str(d / "skill" / "SKILL.md")
    assert argv[argv.index("--scenarios") + 1] == str(d / "scenarios")
    assert "--control" in argv
    assert argv[argv.index("--preset") + 1] == "p1"
    assert argv[argv.index("--config") + 1] == cfg
    assert argv[argv.index("--num-ctx") + 1] == "16384"


def test_tune_reports_skill_eval_failures_as_its_exit_code(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    _tunable(plugins)

    def failing(argv):
        raise SystemExit(1)

    monkeypatch.setattr("workspace_app.skill_eval.__main__.main", failing)
    assert cli.main(["tune", "heat", "--config", _config(tmp_path, plugins)]) == 1


@pytest.mark.parametrize(
    ("skill", "scenarios", "says"), [(False, True, "no skill"), (True, False, "no scenarios")]
)
def test_tune_fails_loudly_by_name(tmp_path, capsys, skill, scenarios, says):
    plugins = tmp_path / "plugins"
    _tunable(plugins, skill=skill, scenarios=scenarios)
    assert cli.main(["tune", "heat", "--config", _config(tmp_path, plugins)]) == 1
    out = capsys.readouterr().out
    assert "'heat'" in out and says in out


@pytest.mark.integration
def test_a_scaffolded_plugin_builds_and_passes_check(tmp_path):
    """The scaffold's promise, for real: node + pnpm + vite for the web half,
    uv + the portable python for the isolated sandbox half, then `check`."""
    assert cli.main(["new", "heat", "--with-sandbox", "--with-skill", "--root", str(tmp_path)]) == 0
    assert cli.main(["build", str(tmp_path / "heat"), str(tmp_path / "installed")]) == 0
    installed = tmp_path / "installed" / "heat"
    assert (installed / "web" / "index.js").is_file()
    assert "-s" in (installed / "sandbox" / "launch").read_text()  # the isolated template
    commands = json.loads((installed / "sandbox" / "commands.json").read_text())
    assert [c["name"] for c in commands] == ["validate"]
    assert check_plugin(installed).errors == []
