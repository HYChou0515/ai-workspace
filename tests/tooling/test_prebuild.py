"""§B.T3 prebuild — orchestrate uv venv + pip install + dump schemas +
copy portable python + write launch.

We split the orchestrator (`build_package`) from the testable units:

- `_dump_schemas(launch_path, dst)` — run launch's 3-stage contract to
  cache commands.json + schemas/<cmd>.json on disk. We exercise it with
  a *fake* launch binary (a tiny shell script that emits the JSON we
  expect) so the unit test stays deterministic.
- `_should_rebuild(source_dir, dst_dir)` — mtime check. Pure, easy.

`build_package` end-to-end (uv venv + pip install + copy python) is
exercised via a real-but-tiny sample package in tests further down.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from workspace_app.tooling.prebuild import _dump_schemas, _should_rebuild


def _fake_launch(path: Path, commands: dict[str, dict]) -> None:
    """Write a shell script at `path` that implements the 3-stage contract
    for the given commands (each value is the metadata dict the schema
    stage prints). It's just enough to drive `_dump_schemas`."""
    list_payload = json.dumps(
        [{"name": k, "description": v["description"]} for k, v in commands.items()]
    )
    per_cmd = "\n".join(
        f"  {k}) printf %s {json.dumps(json.dumps(v))!s} ;;" for k, v in commands.items()
    )
    script = f"""#!/bin/sh
if [ $# -eq 0 ]; then
  printf %s {json.dumps(list_payload)!s}
  exit 0
fi
case "$1" in
{per_cmd}
  *) echo "unknown: $1" >&2; exit 2 ;;
esac
"""
    path.write_text(script)
    path.chmod(0o755)


def test_dump_schemas_writes_commands_json_and_each_schema(tmp_path: Path):
    """Given a launch binary implementing the 3-stage contract,
    `_dump_schemas` writes `<dst>/commands.json` (the metadata array)
    and `<dst>/schemas/<cmd>.json` for each command."""
    launch = tmp_path / "launch"
    _fake_launch(
        launch,
        {
            "summarise": {
                "name": "summarise",
                "description": "Summarise a CSV.",
                "params_json_schema": {"type": "object", "properties": {"csv": {"type": "string"}}},
            },
            "plot": {
                "name": "plot",
                "description": "Plot a CSV.",
                "params_json_schema": {"type": "object", "properties": {"csv": {"type": "string"}}},
            },
        },
    )
    dst = tmp_path / "dst"
    dst.mkdir()
    _dump_schemas(launch, dst)

    cmds = json.loads((dst / "commands.json").read_text())
    assert sorted(c["name"] for c in cmds) == ["plot", "summarise"]
    sum_schema = json.loads((dst / "schemas" / "summarise.json").read_text())
    assert sum_schema["name"] == "summarise"
    assert sum_schema["params_json_schema"]["properties"]["csv"]["type"] == "string"
    plot_schema = json.loads((dst / "schemas" / "plot.json").read_text())
    assert plot_schema["name"] == "plot"


def test_dump_schemas_raises_when_launch_misbehaves(tmp_path: Path):
    """A launch binary that exits non-zero on bare invocation can't be
    cached; raise so the prebuild script halts loudly."""
    bad = tmp_path / "launch"
    bad.write_text("#!/bin/sh\nexit 1\n")
    bad.chmod(0o755)
    with pytest.raises(RuntimeError, match="commands"):
        _dump_schemas(bad, tmp_path / "dst")


def test_dump_schemas_raises_when_per_command_schema_fails(tmp_path: Path):
    """The bare-launch enumeration succeeds (returning one command), but
    `launch <cmd>` exits non-zero. Covers the per-command failure branch."""
    launch = tmp_path / "launch"
    # bare → [{"name": "broken", ...}]  ; broken → exit 1
    launch.write_text(
        "#!/bin/sh\n"
        "if [ $# -eq 0 ]; then\n"
        '  printf %s \'[{"name":"broken","description":"d"}]\'\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    launch.chmod(0o755)
    dst = tmp_path / "dst"
    dst.mkdir()
    with pytest.raises(RuntimeError, match="broken"):
        _dump_schemas(launch, dst)


def test_should_rebuild_true_when_dst_missing(tmp_path: Path):
    """No dst dir yet → must rebuild from scratch."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "anything.py").write_text("x")
    assert _should_rebuild(src, tmp_path / "dst") is True


def test_should_rebuild_false_when_src_older_than_dst(tmp_path: Path):
    """Dst built after every src file → already up to date. Explicit
    mtimes — see the comment in the rebuild-true test."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x")
    os.utime(src / "foo.py", (1000.0, 1000.0))
    dst = tmp_path / "dst"
    dst.mkdir()
    marker = dst / ".built"
    marker.write_text("ok")
    os.utime(marker, (2000.0, 2000.0))  # > src
    assert _should_rebuild(src, dst) is False


def test_should_rebuild_true_when_src_newer_than_dst(tmp_path: Path):
    """Any source file edited after the last build → rebuild. Use
    `os.utime` to set explicit mtimes — wall-clock sleep would depend
    on filesystem mtime resolution (some fs round to 1s)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    marker = dst / ".built"
    marker.write_text("ok")
    os.utime(marker, (1000.0, 1000.0))
    (src / "bar.py").write_text("y")
    os.utime(src / "bar.py", (2000.0, 2000.0))  # > marker
    assert _should_rebuild(src, dst) is True


def test_should_rebuild_ignores_dot_subdirs_in_src(tmp_path: Path):
    """A freshly-rebuilt `.venv` inside `src` doesn't count — we look
    at author-edited files only, not anything starting with `.`."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x")
    os.utime(src / "foo.py", (1000.0, 1000.0))
    dst = tmp_path / "dst"
    dst.mkdir()
    marker = dst / ".built"
    marker.write_text("ok")
    os.utime(marker, (2000.0, 2000.0))
    # Drop a freshly-touched dotfile/dir in src that would otherwise trip the check.
    (src / ".venv").mkdir()
    (src / ".venv" / "something").write_text("recent")
    os.utime(src / ".venv" / "something", (3000.0, 3000.0))  # newer than marker
    assert _should_rebuild(src, dst) is False


# ─── end-to-end build (real uv) ────────────────────────────────────────


def _has_uv() -> bool:
    return shutil.which("uv") is not None


def test_build_package_short_circuits_when_source_unchanged(tmp_path: Path, monkeypatch):
    """When `_should_rebuild` says no → `build_package` returns early
    without touching uv. Covers the idempotent skip path."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x")
    os.utime(src / "a.py", (1000.0, 1000.0))
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / ".built").write_text("ok")
    os.utime(dst / ".built", (2000.0, 2000.0))

    # If subprocess.run gets called the test fails — short-circuit means
    # uv venv / pip install never run.
    called: list = []
    monkeypatch.setattr(prebuild.subprocess, "run", lambda *a, **kw: called.append(a))
    prebuild.build_package(name="pkg", source=src, dst=dst)
    assert called == []


def test_build_package_removes_existing_dst_before_rebuilding(tmp_path: Path, monkeypatch):
    """When dst already exists (stale build), rebuild starts by rm'ing
    it. Covers the `if dst.exists(): shutil.rmtree(dst)` branch without
    needing a real uv venv."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x")  # newer than dst's built marker → rebuild
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "stale-file").write_text("old")
    # NO `.built` marker → _should_rebuild returns True (dst missing marker).

    # Replace the heavy lifting (uv venv / pip install / copytree / dump
    # schemas) with no-ops; we only care about the rmtree → recreate path.
    monkeypatch.setattr(prebuild.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(prebuild.shutil, "copytree", lambda *a, **kw: None)
    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)

    # Synthesize the "bundled python" target so `.resolve()` finds something.
    bundled = tmp_path / "bin" / "python3.99"
    bundled.parent.mkdir()
    bundled.write_text("")

    class _FakePath(type(dst / ".venv" / "bin" / "python")):
        def resolve(self):
            return bundled

    monkeypatch.setattr(
        prebuild.Path, "resolve", lambda self: bundled if self.name == "python" else self
    )

    prebuild.build_package(name="pkg", source=src, dst=dst)
    # Stale file is gone; dst was rebuilt + .built marker set.
    assert not (dst / "stale-file").exists()
    assert (dst / ".built").exists()


@pytest.mark.skipif(not _has_uv(), reason="uv not available")
def test_build_package_end_to_end_writes_full_prebuilt_tree(tmp_path: Path):
    """Real `uv venv + pip install` on a tiny self-contained source package
    that uses our dispatcher; verify `commands.json`, `schemas/*.json`,
    `launch`, and `python/` all end up in dst."""
    src = tmp_path / "src" / "echotool"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        "[project]\n"
        'name = "echotool"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        'dependencies = ["pydantic>=2"]\n'
        "\n"
        "[project.scripts]\n"
        'echotool = "echotool.cli:main"\n'
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/echotool"]\n'
    )
    pkg = src / "src" / "echotool"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    # Self-contained CLI implementing the 3-stage contract by hand — keeps
    # the test fixture independent of workspace_app's dispatcher (which
    # `uv pip install <src>` doesn't pull in along with the source pkg).
    (pkg / "cli.py").write_text(
        "import json, sys\n"
        "from pydantic import BaseModel, ValidationError\n"
        "class A(BaseModel):\n"
        "    msg: str\n"
        "DESC = 'Echo back.'\n"
        "def main() -> None:\n"
        "    if len(sys.argv) == 1:\n"
        "        print(json.dumps([{'name': 'echo', 'description': DESC}]))\n"
        "        return\n"
        "    if sys.argv[1] != 'echo':\n"
        "        print('unknown', file=sys.stderr); sys.exit(2)\n"
        "    if len(sys.argv) == 2:\n"
        "        print(json.dumps({\n"
        "            'name': 'echo', 'description': DESC,\n"
        "            'params_json_schema': A.model_json_schema(),\n"
        "        }))\n"
        "        return\n"
        "    try:\n"
        "        args = A.model_validate_json(sys.argv[2])\n"
        "    except ValidationError as e:\n"
        "        print(str(e), file=sys.stderr); sys.exit(2)\n"
        "    print(args.msg)\n"
    )

    from workspace_app.tooling.prebuild import build_package

    dst = tmp_path / "dst"
    build_package(name="echotool", source=src, dst=dst)

    assert (dst / "commands.json").is_file()
    cmds = json.loads((dst / "commands.json").read_text())
    assert any(c["name"] == "echo" for c in cmds)
    assert (dst / "schemas" / "echo.json").is_file()
    assert (dst / "launch").is_file()
    assert os.access(dst / "launch", os.X_OK)
    # The bundled python lives under dst/python.
    assert (dst / "python").is_dir()
