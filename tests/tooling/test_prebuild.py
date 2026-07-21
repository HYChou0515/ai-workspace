"""§B.T3 prebuild — orchestrate uv venv + pip install + dump schemas +
copy portable python + write launch.

We split the orchestrator (`build_package`) from the testable units:

- `_dump_schemas(launch_path, dst)` — run launch's 3-stage contract to
  cache commands.json + schemas/<cmd>.json on disk. We exercise it with
  a *fake* launch binary (a tiny shell script that emits the JSON we
  expect) so the unit test stays deterministic.
- `_should_rebuild(source_dir, dst_dir)` — source content-hash check. Pure, easy.

`build_package` end-to-end (uv venv + pip install + copy python) is
exercised via a real-but-tiny sample package in tests further down.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from workspace_app.tooling.prebuild import (
    _build_stamp,
    _dump_schemas,
    _should_rebuild,
)


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


def test_should_rebuild_false_when_content_unchanged(tmp_path: Path):
    """Marker records the source content-hash → an unchanged source is
    up to date, even though the marker is older than the source files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / ".built").write_text(_build_stamp(src))
    assert _should_rebuild(src, dst) is False


def test_should_rebuild_true_when_content_changed_even_with_same_mtime(tmp_path: Path):
    """#64's exact shape: a source file's CONTENT changes but its mtime
    is unchanged (e.g. an editor that preserves it, or a same-second
    write). The old mtime check skipped the rebuild; the content hash
    must catch it."""
    src = tmp_path / "src"
    src.mkdir()
    foo = src / "foo.py"
    foo.write_text("old")
    os.utime(foo, (1000.0, 1000.0))
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / ".built").write_text(_build_stamp(src))
    # Edit content but pin mtime back to the original — mtime-based skip
    # would say "unchanged", content-hash must say "rebuild".
    foo.write_text("new code")
    os.utime(foo, (1000.0, 1000.0))
    assert _should_rebuild(src, dst) is True


def test_should_rebuild_ignores_dot_subdirs_in_src(tmp_path: Path):
    """A freshly-rebuilt `.venv` inside `src` doesn't count — the hash
    folds in author-edited files only, not anything starting with `.`."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / ".built").write_text(_build_stamp(src))
    # Drop a dotfile/dir in src that must NOT change the hash.
    (src / ".venv").mkdir()
    (src / ".venv" / "something").write_text("recent")
    assert _should_rebuild(src, dst) is False


def test_should_rebuild_true_when_builder_templates_change(tmp_path: Path, monkeypatch):
    """#393: editing a launcher TEMPLATE (builder code, not package source)
    must force a rebuild of an already-built bundle. `_source_hash(source)`
    can't see it — the template is baked INTO the bundle at build time, not
    read from the source — so the `.built` stamp folds in a builder
    fingerprint. Same stale-cache class as #64: without this, a changed
    `_PYTHON_LAUNCH` silently keeps the old `launch` in every cached bundle."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / ".built").write_text(prebuild._build_stamp(src))
    # Baseline: source AND builder unchanged → up to date.
    assert prebuild._should_rebuild(src, dst) is False
    # Simulate a launcher-template edit; the source is untouched.
    monkeypatch.setattr(prebuild, "_PYTHON_LAUNCH", prebuild._PYTHON_LAUNCH + "\n# edited\n")
    assert prebuild._should_rebuild(src, dst) is True


# ─── end-to-end build (real uv) ────────────────────────────────────────


def _has_uv() -> bool:
    return shutil.which("uv") is not None


def test_build_package_short_circuits_when_source_unchanged(tmp_path: Path, monkeypatch):
    """When `_should_rebuild` says no (marker hash == source hash) →
    `build_package` returns early without touching uv. Covers the
    idempotent skip path."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x")
    (src / "uv.lock").write_text("# fake")  # required by build_package preamble
    dst = tmp_path / "dst"
    dst.mkdir()
    # Marker holds the current source hash → unchanged → skip.
    (dst / ".built").write_text(prebuild._build_stamp(src))

    # If subprocess.run gets called the test fails — short-circuit means
    # uv venv / uv sync never run.
    called: list = []
    monkeypatch.setattr(prebuild.subprocess, "run", lambda *a, **kw: called.append(a))
    prebuild.build_package(name="pkg", source=src, dst=dst)
    assert called == []


def test_build_package_force_rebuilds_even_when_source_unchanged(tmp_path: Path, monkeypatch):
    """`force=True` (the `--force` flag) rebuilds even when the source
    content is unchanged — bypasses the short-circuit."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\n')
    (src / "uv.lock").write_text("# fake")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / ".built").write_text(prebuild._build_stamp(src))  # unchanged

    invocations: list[list[str]] = []
    monkeypatch.setattr(prebuild.subprocess, "run", lambda cmd, **kw: invocations.append(list(cmd)))
    monkeypatch.setattr(prebuild.shutil, "copytree", lambda *a, **kw: None)
    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)
    bundled = tmp_path / "bin" / "python3.99"
    bundled.parent.mkdir()
    bundled.write_text("")
    monkeypatch.setattr(
        prebuild.Path, "resolve", lambda self: bundled if self.name == "python" else self
    )

    prebuild.build_package(name="pkg", source=src, dst=dst, force=True)
    assert any(cmd[0:2] == ["uv", "sync"] for cmd in invocations), invocations


def test_build_package_forces_uv_to_refresh_the_local_wheel(tmp_path: Path, monkeypatch):
    """#64: `uv sync` must reinstall + refresh the LOCAL package so a
    same-version source edit lands. Without it uv restores the stale
    cached wheel and the change is silently ignored. The flags target the
    distribution name read from the source's pyproject."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text('[project]\nname = "data-fetch"\nversion = "0"\n')
    (src / "uv.lock").write_text("# fake")
    dst = tmp_path / "dst"

    invocations: list[list[str]] = []
    monkeypatch.setattr(prebuild.subprocess, "run", lambda cmd, **kw: invocations.append(list(cmd)))
    monkeypatch.setattr(prebuild.shutil, "copytree", lambda *a, **kw: None)
    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)
    bundled = tmp_path / "bin" / "python3.99"
    bundled.parent.mkdir()
    bundled.write_text("")
    monkeypatch.setattr(
        prebuild.Path, "resolve", lambda self: bundled if self.name == "python" else self
    )

    prebuild.build_package(name="data-fetch", source=src, dst=dst)

    sync = next(cmd for cmd in invocations if cmd[0:2] == ["uv", "sync"])
    # Targeted at the local distribution, not a blanket --reinstall (which
    # would needlessly rebuild every dep).
    assert "--reinstall-package" in sync and "--refresh-package" in sync, sync
    assert sync[sync.index("--reinstall-package") + 1] == "data-fetch", sync
    assert sync[sync.index("--refresh-package") + 1] == "data-fetch", sync


# ─── uv-run debug bundles (#63) ──────────────────────────────────────


def test_uvrun_bundle_softlinks_source_and_runs_via_project(tmp_path, monkeypatch):
    """#63 debug mode: the live source is SYMLINKED into the bundle as
    `project`, and the launch runs it via `uv run --project "$here/project"`.
    `--project` (NOT `--directory`) keeps the caller's cwd, so a tool's
    workspace-relative write lands in the sandbox workspace, not the source."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src" / "data-fetch"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        '[project]\nname = "data-fetch"\nversion = "0"\n[project.scripts]\ndata-fetch = "x:y"\n'
    )
    # Skip the real 3-stage schema dump (would invoke uv); we only assert the
    # launch script + the symlink here.
    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)
    dst = tmp_path / "dst"
    prebuild.build_package_uvrun(name="data-fetch", source=src, dst=dst)

    launch = (dst / "launch").read_text()
    assert "uv run --project" in launch
    assert "--directory" not in launch  # --directory would cd out of the workspace
    assert '"$here/project"' in launch  # reaches the source via the in-bundle symlink
    assert "data-fetch" in launch  # the [project.scripts] entrypoint
    assert os.access(dst / "launch", os.X_OK)
    # The source is softlinked in (no copy) — resolves to the live source.
    link = dst / "project"
    assert link.is_symlink()
    assert link.resolve() == src.resolve()
    # No copied python / venv — that's the whole point.
    assert not (dst / "python").exists()
    assert not (dst / ".venv").exists()


def test_build_package_uvrun_carrier_writes_python_launch_and_empty_commands(tmp_path):
    """A venv carrier (no [project.scripts]) gets a `uv run --project … python`
    launcher plus the empty commands.json/schemas the discover walker needs."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src" / "python-stack"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text('[project]\nname = "python-stack"\nversion = "0"\n')
    dst = tmp_path / "dst"
    prebuild.build_package_uvrun(name="python-stack", source=src, dst=dst)

    launch = (dst / "launch").read_text()
    assert "uv run --project" in launch
    assert "python" in launch
    assert (dst / "project").is_symlink()
    assert json.loads((dst / "commands.json").read_text()) == []
    assert (dst / "schemas").is_dir()


def test_provision_uvrun_builds_present_sources_and_skips_missing(tmp_path, monkeypatch):
    """provision_uvrun builds a bundle per existing source and skips a
    missing source dir (keeps the gitignored in-house rca-tools optional)."""
    from workspace_app.tooling import prebuild

    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)
    src = tmp_path / "src" / "present"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        '[project]\nname = "present"\nversion = "0"\n[project.scripts]\npresent = "x:y"\n'
    )
    dst = tmp_path / "tools"
    out = prebuild.provision_uvrun({"present": src, "absent": tmp_path / "nope"}, dst)

    assert out == dst
    assert (dst / "present" / "launch").is_file()
    assert not (dst / "absent").exists()


@pytest.mark.skipif(not _has_uv(), reason="uv not available")
@pytest.mark.integration
def test_build_package_uvrun_launch_runs_from_live_source_and_writes_in_callers_cwd(
    tmp_path: Path,
):
    """End-to-end: the uv-run launch executes the tool through `uv run` and

    1. a tool's RELATIVE write lands in the CALLER's cwd (the sandbox
       workspace), NOT the source dir — the #63 bug where `--directory`
       cd'd into the source so `./step2-data/...` writes missed the
       workspace; and
    2. a source EDIT is reflected on the next call with NO rebuild."""
    src = tmp_path / "echotool"
    src.mkdir()
    (src / "pyproject.toml").write_text(
        "[project]\n"
        'name = "echotool"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = []\n"
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
    cli = pkg / "cli.py"

    def write_cli(transform: str) -> None:
        cli.write_text(
            "import json, sys\n"
            "DESC = 'Echo back.'\n"
            "def main() -> None:\n"
            "    if len(sys.argv) == 1:\n"
            "        print(json.dumps([{'name': 'echo', 'description': DESC}]))\n"
            "        return\n"
            "    if sys.argv[1] != 'echo':\n"
            "        print('unknown', file=sys.stderr); sys.exit(2)\n"
            "    if len(sys.argv) == 2:\n"
            "        print(json.dumps({'name': 'echo', 'description': DESC,\n"
            "            'params_json_schema': {'type': 'object'}}))\n"
            "        return\n"
            "    msg = json.loads(sys.argv[2])['msg']\n"
            "    open('echoed.txt', 'w').write(msg)\n"  # RELATIVE write — must hit cwd
            f"    print({transform})\n"
        )

    write_cli("msg")

    import subprocess

    from workspace_app.tooling.prebuild import build_package_uvrun

    dst = tmp_path / "dst"
    build_package_uvrun(name="echotool", source=src, dst=dst)
    launch = dst / "launch"
    assert launch.is_file()

    # Invoke from a SEPARATE working dir (stands in for the sandbox workspace).
    work = tmp_path / "work"
    work.mkdir()
    out = subprocess.run(
        [str(launch), "echo", '{"msg": "hi"}'], cwd=work, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "hi"
    # The #63 regression: the relative write lands in the CALLER's cwd, NOT the
    # source — proving `uv run --project` kept cwd (vs --directory's cd).
    assert (work / "echoed.txt").read_text() == "hi"
    assert not (src / "echoed.txt").exists()

    # Edit the source — NO rebuild — and confirm the change is live.
    write_cli("msg.upper()")
    out2 = subprocess.run(
        [str(launch), "echo", '{"msg": "hi"}'], cwd=work, capture_output=True, text=True
    )
    assert out2.returncode == 0, out2.stderr
    assert out2.stdout.strip() == "HI"


def test_build_package_uses_uv_sync_with_frozen_lockfile_when_present(tmp_path: Path, monkeypatch):
    """When the package source ships a `uv.lock`, the prebuild MUST
    install via `uv sync --frozen` so the bundled venv contains the
    exact versions the package author committed — never re-resolving
    on the host with `uv pip install`, which would silently drift
    pandas / scipy / scikit-learn versions between operators."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (src / "uv.lock").write_text("# fake lock — presence is what matters here")

    dst = tmp_path / "dst"

    invocations: list[list[str]] = []

    def fake_run(cmd, **kw):
        invocations.append(list(cmd))

        class _R:
            returncode = 0
            stdout = b"[]"
            stderr = b""

        return _R()

    monkeypatch.setattr(prebuild.subprocess, "run", fake_run)
    monkeypatch.setattr(prebuild.shutil, "copytree", lambda *a, **kw: None)
    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)
    bundled = tmp_path / "bin" / "python3.99"
    bundled.parent.mkdir()
    bundled.write_text("")
    monkeypatch.setattr(
        prebuild.Path, "resolve", lambda self: bundled if self.name == "python" else self
    )

    prebuild.build_package(name="x", source=src, dst=dst)

    # First call is `uv venv --relocatable <dst-venv>`; second is the
    # locked install (`uv sync --frozen ...`). The combination is what
    # matters: relocatable venv + frozen sync against the package's
    # own lockfile.
    assert any(cmd[0:2] == ["uv", "venv"] and "--relocatable" in cmd for cmd in invocations), (
        invocations
    )
    assert any(cmd[0:2] == ["uv", "sync"] and "--frozen" in cmd for cmd in invocations), invocations
    # uv pip install must NOT be used — that would re-resolve on host
    # and ignore the source's pinned uv.lock versions.
    assert not any(cmd[0:3] == ["uv", "pip", "install"] for cmd in invocations), invocations


def test_build_package_raises_when_source_has_no_uv_lock(tmp_path: Path, monkeypatch):
    """A source without `uv.lock` is a misconfigured package — there's
    no way to honour 'pinned deps' if there are none. Raise loud
    instead of silently falling back to `uv pip install`."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    # No uv.lock — that's the misconfig under test.

    dst = tmp_path / "dst"

    with pytest.raises(RuntimeError, match="uv.lock"):
        prebuild.build_package(name="x", source=src, dst=dst)


def test_build_package_removes_existing_dst_before_rebuilding(tmp_path: Path, monkeypatch):
    """When dst already exists (stale build), rebuild starts by rm'ing
    it. Covers the `if dst.exists(): shutil.rmtree(dst)` branch without
    needing a real uv venv."""
    from workspace_app.tooling import prebuild

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x")  # newer than dst's built marker → rebuild
    (src / "uv.lock").write_text("# fake")  # required by build_package preamble
    # `build_package` reads pyproject.toml to decide carrier vs CLI;
    # this fixture is the CLI flavour (has `[project.scripts]`).
    (src / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0"\n[project.scripts]\npkg = "x:y"\n'
    )
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

    class _FakePath(type(dst / ".venv" / "bin" / "python")):  # ty: ignore[unsupported-base]
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
@pytest.mark.integration
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

    # build_package now requires the source to ship `uv.lock` (so the
    # bundled deps match the package author's pinned versions, not
    # whatever uv resolves fresh on the host). Generate one here from
    # the fixture's pyproject.toml.
    import subprocess

    subprocess.run(["uv", "lock", "--directory", str(src)], check=True)

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


@pytest.mark.integration
def test_build_package_handles_venv_carrier_without_project_scripts(tmp_path: Path):
    """A package whose pyproject.toml has NO `[project.scripts]` table is
    a *venv carrier* — its sole job is to ship a uv-locked venv into the
    sandbox so raw `python` calls see those deps. `build_package` must:

      - emit a pure-`python` launcher (no command-dispatch wrapper), so
        the jail bootstrap can route `python` to it,
      - still produce `commands.json` (empty array) and `schemas/` (empty
        dir) so `discover_packages` doesn't choke on the half-built shape,
      - never invoke `_dump_schemas` (there's nothing to enumerate).

    This is the prebuild-side half of #pandas-in-sandbox; the other half
    is `_JAIL_BOOTSTRAP`'s `python` shim selection.
    """
    src = tmp_path / "src" / "depscarrier"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        "[project]\n"
        'name = "depscarrier"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        # Tiny pure-python dep so `uv sync` is fast + offline-friendly.
        'dependencies = ["packaging>=20"]\n'
        # No [project.scripts] — that's the venv-carrier signal.
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/depscarrier"]\n'
    )
    pkg = src / "src" / "depscarrier"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")

    import subprocess

    subprocess.run(["uv", "lock", "--directory", str(src)], check=True)

    from workspace_app.tooling.prebuild import build_package

    dst = tmp_path / "dst"
    build_package(name="depscarrier", source=src, dst=dst)

    # Skeleton present and empty — discover_packages walks every PREBUILT
    # subdir and requires this shape even for carriers.
    assert (dst / "commands.json").is_file()
    assert json.loads((dst / "commands.json").read_text()) == []
    assert (dst / "schemas").is_dir()
    assert list((dst / "schemas").iterdir()) == []

    # Launcher exists + executable + forwards argv straight to bundled python.
    launch = dst / "launch"
    assert launch.is_file()
    assert os.access(launch, os.X_OK)
    # `<launch> -c "<expr>"` runs the *carrier* python; the dep packaged
    # by uv must be importable, proving the venv site-packages were
    # plumbed in via PYTHONPATH.
    result = subprocess.run(
        [str(launch), "-c", "import packaging; print(packaging.__name__)"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "packaging" in result.stdout


@pytest.mark.integration
def test_build_package_venv_carrier_launcher_resolves_invocation_symlink(tmp_path: Path):
    """The jail bootstrap shims raw `python` via a symlink:
    `/tmp/.jailbin/python` → `/.tools/python-stack/launch`. The launcher
    must locate its OWN dir (where `python/bin/python3.X` and `.venv/`
    sit), not the symlink's dir. Without resolving the symlink, `$here`
    would be `/tmp/.jailbin`, and the loader would fail to find
    `$here/python/bin/python3.X` → 'cannot open shared object file:
    Error 20' (ENOTDIR).

    Regression lock for the May 31 sandbox failure: invoke the launcher
    via a symlink and confirm it runs the bundled python from the real
    bundle dir.
    """
    src = tmp_path / "src" / "carrier"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        "[project]\n"
        'name = "carrier"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = []\n"
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/carrier"]\n'
    )
    pkg = src / "src" / "carrier"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")

    import subprocess

    subprocess.run(["uv", "lock", "--directory", str(src)], check=True)

    from workspace_app.tooling.prebuild import build_package

    dst = tmp_path / "dst"
    build_package(name="carrier", source=src, dst=dst)

    # Create a symlink elsewhere that points at the carrier's launcher.
    # The launcher must follow the symlink to find its bundle dir.
    elsewhere = tmp_path / "shim"
    elsewhere.mkdir()
    (elsewhere / "python").symlink_to(dst / "launch")

    result = subprocess.run(
        [str(elsewhere / "python"), "-c", "import sys; print(sys.version_info.major)"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip() == "3"


@pytest.mark.integration
def test_build_package_venv_carrier_launcher_does_not_dispatch_a_command(tmp_path: Path):
    """The venv-carrier launcher is *not* a 3-stage dispatcher: calling
    it bare (no args) does NOT print a commands list — it drops the user
    into an interactive Python REPL the same way bare `python` would.
    Regression lock for the design choice that this launcher is `python`,
    not `tool-cli`.
    """
    src = tmp_path / "src" / "bare"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        "[project]\n"
        'name = "bare"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = []\n"
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/bare"]\n'
    )
    pkg = src / "src" / "bare"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")

    import subprocess

    subprocess.run(["uv", "lock", "--directory", str(src)], check=True)

    from workspace_app.tooling.prebuild import build_package

    dst = tmp_path / "dst"
    build_package(name="bare", source=src, dst=dst)

    # Bare launch + `-c "print(1+1)"` produces "2", proving it's a real
    # python interpreter and not a JSON dispatcher.
    result = subprocess.run(
        [str(dst / "launch"), "-c", "print(1+1)"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2"


def _build_bare_carrier(tmp_path: Path) -> Path:
    """Build a tiny dep-less venv carrier and return its `launch` path."""
    import subprocess

    src = tmp_path / "src" / "carrier"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        "[project]\n"
        'name = "carrier"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = []\n"
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
        "\n"
        "[tool.hatch.build.targets.wheel]\n"
        'packages = ["src/carrier"]\n'
    )
    pkg = src / "src" / "carrier"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    subprocess.run(["uv", "lock", "--directory", str(src)], check=True)

    from workspace_app.tooling.prebuild import build_package

    dst = tmp_path / "dst"
    build_package(name="carrier", source=src, dst=dst)
    return dst / "launch"


def _launch_home(launch: Path, env: dict[str, str]) -> dict[str, str]:
    """Run the carrier launcher and report the HOME/XDG it exports."""
    import subprocess

    result = subprocess.run(
        [
            str(launch),
            "-c",
            "import os, json; print(json.dumps({"
            "'HOME': os.environ.get('HOME'),"
            "'XDG': os.environ.get('XDG_CACHE_HOME')}))",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.skipif(not _has_uv(), reason="uv not available")
@pytest.mark.integration
def test_carrier_launcher_routes_home_to_sandbox_home(tmp_path: Path):
    """#393: the carrier launcher must route HOME (and its caches) to the
    per-sandbox dir the exec path passes as `SANDBOX_HOME` — so a user's
    `pip install --user` fallback lands there, private to the sandbox,
    instead of a shared location."""
    launch = _build_bare_carrier(tmp_path)
    home = tmp_path / "sandboxhome"
    home.mkdir()
    reported = _launch_home(launch, {**os.environ, "SANDBOX_HOME": str(home)})
    assert reported["HOME"] == str(home)
    assert reported["XDG"] == str(home / ".cache")


@pytest.mark.skipif(not _has_uv(), reason="uv not available")
@pytest.mark.integration
def test_carrier_launcher_home_is_private_not_shared_tmp_when_unset(tmp_path: Path):
    """#393 fail-safe: with SANDBOX_HOME unset the launcher must NOT fall
    back to the shared pod `/tmp`. A caller that forgets to set it degrades
    to a fresh PRIVATE dir (mktemp -d, 0700) — non-persistent, but never a
    location other sandboxes can read/write. The default must not be the
    footgun."""
    launch = _build_bare_carrier(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "SANDBOX_HOME"}
    reported = _launch_home(launch, env)
    home = reported["HOME"]
    assert home != "/tmp"  # never the shared pod /tmp
    assert os.path.isdir(home)
    # A private per-invocation dir: mktemp -d creates it 0700 (owner-only).
    assert oct(os.stat(home).st_mode & 0o777) == "0o700"


# --- the carrier launcher is multi-call: `python` runs python, `pip` runs pip ---
#
# `pip` was never shimmed, so a bare `pip install X` in `exec` resolved down the
# rest of PATH to the IMAGE's python (jailed: /usr/bin/pip; unjailed: the host
# service's own venv) — a different interpreter AND a different HOME from the
# carrier the agent's `python` actually is. pip reported success and the import
# then failed, with nothing anywhere connecting the two.
#
# The launcher dispatches on the name it was invoked as, so the shims stay plain
# symlinks (one file, one build fingerprint) instead of growing a second script.


def _echo_carrier(tmp_path: Path) -> Path:
    """A carrier bundle whose bundled interpreter is `/bin/echo`, so the
    launcher's final exec PRINTS the argv it built rather than running it.
    Keeps this a fast unit test: no uv, no real interpreter, no network."""
    bundle = tmp_path / "python-stack"
    (bundle / "python" / "bin").mkdir(parents=True)
    (bundle / "python" / "bin" / "python3.12").symlink_to("/bin/echo")
    (bundle / ".venv" / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    launch = bundle / "launch"
    launch.write_text(prebuild_module()._PYTHON_LAUNCH.format(ver="3.12"))
    launch.chmod(0o755)
    return launch


def prebuild_module():
    from workspace_app.tooling import prebuild

    return prebuild


def _argv_built_for(launch: Path, called_as: str, args: list[str]) -> str:
    """Run the launcher through a `called_as`-named symlink (what the sandbox's
    shim dir holds) and return the argv it handed the interpreter.

    The shims live in their own dir, exactly as `.jailbin` does in a real
    sandbox — the bundle root already has a `python/` DIRECTORY, so a shim named
    `python` could not sit beside it anyway."""
    import subprocess

    shim_dir = launch.parent.parent / "jailbin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / called_as
    shim.symlink_to(launch)
    r = subprocess.run([str(shim), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def test_invoked_as_pip_the_carrier_launcher_runs_the_carriers_own_pip(tmp_path: Path):
    """`pip install X` must become the carrier's `python -m pip install X` — the
    one interpreter whose HOME the launcher has already rewritten to
    SANDBOX_HOME, so the `--user` fallback lands where the carrier reads."""
    launch = _echo_carrier(tmp_path)
    assert _argv_built_for(launch, "pip", ["install", "cowsay"]) == "-m pip install cowsay"


def test_invoked_as_python_the_launcher_is_unchanged(tmp_path: Path):
    """Regression lock for the dispatch itself: `python` must still forward its
    argv verbatim. `exec(["python", "-c", …])` is the agent's most common call,
    and a `case` arm that leaked into it would break every one of them."""
    launch = _echo_carrier(tmp_path)
    assert _argv_built_for(launch, "python", ["-c", "print(1)"]) == "-c print(1)"


def test_the_pip_flavour_names_dispatch_too(tmp_path: Path):
    """`pip3` is what agents type at least as often as `pip`, and the
    major-minor flavours mirror the `python3.N` shims that already exist. A name
    that merely STARTS with pip (`pipx`) must not be swallowed — it is a
    different tool, and silently running it as pip would be worse than the
    command simply not being found."""
    launch = _echo_carrier(tmp_path)
    assert _argv_built_for(launch, "pip3", ["list"]) == "-m pip list"
    assert _argv_built_for(launch, "pip3.12", ["list"]) == "-m pip list"
    assert _argv_built_for(launch, "pipx", ["list"]) == "list"


# --- the bundled interpreter must not claim to be distro-managed ---
#
# uv's CPython builds ship an EXTERNALLY-MANAGED marker, and the bundle copies
# the whole interpreter tree, marker included. pip then REFUSES `pip install X`
# outright ("error: externally-managed-environment") unless it is handed
# --break-system-packages. The marker means "an OS package manager owns this
# interpreter, install into a venv instead" — which is simply not true of a
# self-contained bundle that no packager tracks, and there is no venv for the
# agent to install into either. Dropping it is not loosening a safety rail; it
# is deleting a claim that was never about this environment.
#
# With it gone, pip's own behaviour does the right thing unaided: the bundle's
# site-packages is root-owned and read-only in production, so pip reports
# "Defaulting to user installation because normal site-packages is not writeable"
# and installs into $HOME/.local — and the launcher has already pointed HOME at
# SANDBOX_HOME, which is exactly where this interpreter looks. No flag to teach,
# and nothing injected into the user's command line on their behalf.


def test_the_bundled_interpreter_is_not_left_marked_externally_managed(tmp_path: Path):
    """The marker travels with uv's interpreter; the bundle must not inherit it.
    Every X.Y under lib/ is cleared — the tree is copied wholesale, so which
    directory the marker lands in is uv's choice, not ours."""
    py = tmp_path / "python"
    (py / "lib" / "python3.12").mkdir(parents=True)
    (py / "lib" / "python3.13").mkdir(parents=True)
    markers = [
        py / "lib" / "python3.12" / "EXTERNALLY-MANAGED",
        py / "lib" / "python3.13" / "EXTERNALLY-MANAGED",
    ]
    for m in markers:
        m.write_text("[externally-managed]\nError=managed by the distro\n")

    prebuild_module()._drop_externally_managed(py)

    assert [m for m in markers if m.exists()] == []


def test_dropping_the_marker_is_fine_when_there_is_none(tmp_path: Path):
    """Interpreter builds that never shipped the marker must not make the
    prebuild explode — the bundle is still perfectly valid without it."""
    py = tmp_path / "python"
    (py / "lib" / "python3.12").mkdir(parents=True)
    prebuild_module()._drop_externally_managed(py)  # must not raise


def test_a_change_to_what_the_builder_strips_forces_a_rebuild(tmp_path: Path, monkeypatch):
    """Same stale-cache class as #64 and #393's launcher edit: clearing
    EXTERNALLY-MANAGED is builder behaviour that is BAKED INTO the bundle, so
    `_source_hash(source)` cannot see a change to it. Without folding it into
    the stamp, editing the strip step would leave every already-built bundle
    exactly as it was — still refusing `pip install` — while the code claimed
    to have fixed it."""
    prebuild = prebuild_module()
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / ".built").write_text(prebuild._build_stamp(src))
    assert prebuild._should_rebuild(src, dst) is False

    def _drop_externally_managed(python_dir: Path) -> None:
        """A different rule about what the bundle strips."""

    monkeypatch.setattr(prebuild, "_drop_externally_managed", _drop_externally_managed)
    assert prebuild._should_rebuild(src, dst) is True


@pytest.mark.skipif(not _has_uv(), reason="uv not available")
@pytest.mark.integration
def test_a_real_built_carrier_ships_no_externally_managed_marker(tmp_path: Path):
    """The end-to-end fact the unit tests stand in for: build a carrier with the
    real toolchain and the interpreter that comes out must not carry uv's
    PEP 668 marker — which is precisely the file pip checks before refusing
    `pip install`."""
    launch = _build_bare_carrier(tmp_path)
    assert list((launch.parent / "python").rglob("EXTERNALLY-MANAGED")) == []


def test_build_package_actually_strips_the_marker_from_the_bundle(tmp_path: Path, monkeypatch):
    """Pins the WIRING, not just the helper.

    The unit tests above call `_drop_externally_managed` directly, so deleting
    its call site in `build_package` left them all green — the only red was an
    `integration` + `skipif(uv)` test that CI does not run. That is precisely the
    hole this whole change exists to close, so the wiring gets a lock in the set
    CI runs: only the two external process boundaries (`uv venv` / `uv sync`) are
    faked, everything after them — the copytree, the strip, the launcher — is the
    real code path."""
    import subprocess as _sp

    prebuild = prebuild_module()
    src = tmp_path / "src"
    src.mkdir()
    # No [project.scripts] ⇒ a venv carrier, so no schema dump to stand in for.
    (src / "pyproject.toml").write_text('[project]\nname = "carrier"\nversion = "0.1.0"\n')
    (src / "uv.lock").write_text("")

    interp = tmp_path / "managed" / "cpython-3.12.8"
    (interp / "bin").mkdir(parents=True)
    (interp / "bin" / "python3.12").write_text("#!/bin/sh\n")
    (interp / "lib" / "python3.12").mkdir(parents=True)
    (interp / "lib" / "python3.12" / "EXTERNALLY-MANAGED").write_text("[externally-managed]\n")

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[-1])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").symlink_to(interp / "bin" / "python3.12")
        return _sp.CompletedProcess(cmd, 0)

    monkeypatch.setattr(prebuild.subprocess, "run", fake_run)

    dst = tmp_path / "dst"
    prebuild.build_package(name="carrier", source=src, dst=dst)

    assert (dst / "launch").is_file()  # the build really ran
    assert list((dst / "python").rglob("EXTERNALLY-MANAGED")) == []
