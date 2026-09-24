"""The isolated launch template for view-plugin sandbox bundles (#847/#848 P6).

An ordinary tool bundle puts the user site (`$HOME/.local/...`) FIRST on
`PYTHONPATH` on purpose (#581: a user may `pip install --upgrade pandas`). A
view plugin's sandbox half must not: its commands are the platform's, run on
every open of a view, and a user's upgrade must not change what they compute.

These run the REAL templates against a stub bundle whose interpreter is this
test's own python, with a fake `pandas` in both the user site and the bundle.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from workspace_app.tooling.prebuild import _ISOLATED_LAUNCH, _LAUNCH, _builder_fingerprint

VER = f"{sys.version_info.major}.{sys.version_info.minor}"
LD = next(
    (p for p in ("/lib64/ld-linux-x86-64.so.2", "/lib/ld-linux-aarch64.so.1") if Path(p).exists()),
    None,
)
pytestmark = pytest.mark.skipif(LD is None, reason="no glibc dynamic loader to launch through")


def _bundle(root: Path, template: str) -> Path:
    b = root / "bundle"
    b.mkdir()
    # The whole interpreter tree, so `python/bin/pythonX.Y` finds its `../lib`.
    (b / "python").symlink_to(sys.base_prefix, target_is_directory=True)
    site = b / ".venv" / "lib" / f"python{VER}" / "site-packages" / "pandas"
    site.mkdir(parents=True)
    (site / "__init__.py").write_text("WHERE = 'bundle'\n")
    (b / ".venv" / "bin").mkdir(parents=True)
    (b / ".venv" / "bin" / "probe").write_text(
        "import pandas, sys\n"
        "user = any('/.local/' in p for p in sys.path)\n"
        "print(pandas.WHERE, 'user-on-path' if user else 'no-user-path')\n"
    )
    launch = b / "launch"
    launch.write_text(template.format(ver=VER, tool="probe"))
    launch.chmod(0o755)
    return launch


def _home_with_user_pandas(root: Path) -> Path:
    home = root / "home"
    user = home / ".local" / "lib" / f"python{VER}" / "site-packages" / "pandas"
    user.mkdir(parents=True)
    (user / "__init__.py").write_text("WHERE = 'user-site'\n")
    return home


def _run(launch: Path, home: Path, **extra: str) -> str:
    env = {"PATH": os.environ["PATH"], "SANDBOX_HOME": str(home), **extra}
    out = subprocess.run([str(launch)], env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_an_ordinary_tool_bundle_still_prefers_the_user_site(tmp_path: Path):
    """#581's intent, unchanged: the user's upgrade wins in a tool bundle."""
    got = _run(_bundle(tmp_path, _LAUNCH), _home_with_user_pandas(tmp_path))
    assert got == "user-site user-on-path"


def test_a_plugin_bundle_ignores_the_user_site(tmp_path: Path):
    got = _run(_bundle(tmp_path, _ISOLATED_LAUNCH), _home_with_user_pandas(tmp_path))
    # Not merely "the bundle's copy wins": the user site is not on the path at
    # all, so a package the bundle lacks cannot be picked up from it either.
    assert got == "bundle no-user-path"


def test_a_plugin_bundle_ignores_a_user_pythonpath(tmp_path: Path):
    home = _home_with_user_pandas(tmp_path)
    user_site = home / ".local" / "lib" / f"python{VER}" / "site-packages"
    launch = _bundle(tmp_path, _ISOLATED_LAUNCH)
    got = _run(launch, home, PYTHONPATH=str(user_site), SANDBOX_USER_ENV_KEYS="PYTHONPATH")
    assert got == "bundle no-user-path"


def test_the_isolated_template_rebuilds_cached_bundles(monkeypatch: pytest.MonkeyPatch):
    """It is baked into bundles, so it is part of the builder fingerprint."""
    import workspace_app.tooling.prebuild as prebuild

    before = _builder_fingerprint()
    monkeypatch.setattr(prebuild, "_ISOLATED_LAUNCH", prebuild._ISOLATED_LAUNCH + "# edited\n")
    assert _builder_fingerprint() != before


def _fake_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pyproject: str) -> Path:
    """Run `build_package` with uv / the interpreter copy stubbed out, the way
    `test_prebuild.py` does, and return the written `launch`."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text(pyproject)
    (src / "uv.lock").write_text("# fake")
    monkeypatch.setattr(prebuild.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(prebuild.shutil, "copytree", lambda *a, **kw: None)
    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)
    bundled = tmp_path / "bin" / "python3.99"
    bundled.parent.mkdir()
    bundled.write_text("")
    monkeypatch.setattr(
        prebuild.Path, "resolve", lambda self: bundled if self.name == "python" else self
    )
    prebuild.build_package(name="pkg", source=src, dst=tmp_path / "dst", force=True)
    return tmp_path / "dst" / "launch"


PYPROJECT = '[project]\nname = "pkg"\nversion = "0"\n[project.scripts]\npkg = "pkg:main"\n'


def test_a_package_declaring_isolated_launch_gets_the_isolated_template(tmp_path, monkeypatch):
    toml = PYPROJECT + '[tool.workspace-tool]\nlaunch = "isolated"\n'
    launch = _fake_build(tmp_path, monkeypatch, toml)
    assert launch.read_text() == _ISOLATED_LAUNCH.format(ver="3.99", tool="pkg")


def test_a_package_declaring_nothing_keeps_the_ordinary_template(tmp_path, monkeypatch):
    launch = _fake_build(tmp_path, monkeypatch, PYPROJECT)
    assert launch.read_text() == _LAUNCH.format(ver="3.99", tool="pkg")


def test_an_unknown_launch_mode_is_refused(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="launch = 'sandboxed'"):
        _fake_build(
            tmp_path, monkeypatch, PYPROJECT + '[tool.workspace-tool]\nlaunch = "sandboxed"\n'
        )
