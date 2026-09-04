"""The whole chain, once, for real: declare a dependency, import it (#775).

Every defect this feature's review found lived in the same gap. Each half was
tested against a double and **no test anywhere ran a real `uv sync` in a real
sandbox**, so two green halves bought an inert feature three separate times:

* the sync built the venv at uv's default `.venv` beside the manifest while the
  shim probed the infra area — both suites passed, nothing was connected;
* a failed sync left a half-built venv that the shim then adopted, handing the
  agent an interpreter with none of the packages *and* none of the carrier's;
* the shim SYMLINKED `python` at `<venv>/bin/python`, and CPython resolves its
  own path to find `pyvenv.cfg`, so a link from outside the venv resolves
  straight past it to the base interpreter. `uv sync` installed the package,
  `python` could not import it, and every unit test on both sides was green.

None of those is visible from either end alone. So this file enters where the
agent enters — `ensure_project_env`, then `exec(["python", …])` — and asserts
what the user would see.

The dependency is built from a directory inside the workspace, so the test
needs no package index and resolves identically on every run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from workspace_app.agent.python_env import ProjectEnvError, ensure_project_env
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

pytestmark = pytest.mark.integration

_needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="needs a real uv on PATH")

#: Content, not a version number: a package that merely imports proves nothing
#: about WHICH copy answered.
_MARK = "the-workspace-got-its-own-package"

_DEP_MANIFEST = """\
[project]
name = "tinydep"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = []
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

_PROJECT_MANIFEST = """\
[project]
name = "declared"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["tinydep"]
[tool.uv.sources]
tinydep = { path = "dep" }
"""


async def _sandbox(tmp_path: Path) -> tuple[LocalProcessSandbox, SandboxHandle]:
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    return sb, await sb.create(SandboxSpec())


def _declare_a_dependency(workspace: Path, *, lock: bool = True) -> None:
    """What a profile ships: a `pyproject.toml`, and a `uv.lock` beside it."""
    src = workspace / "dep" / "src" / "tinydep"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(f'MARK = "{_MARK}"\n')
    (workspace / "dep" / "pyproject.toml").write_text(_DEP_MANIFEST)
    (workspace / "pyproject.toml").write_text(_PROJECT_MANIFEST)
    if lock:
        subprocess.run(["uv", "lock"], cwd=workspace, check=True, capture_output=True)


@_needs_uv
async def test_a_declared_workspace_can_import_what_it_declared(tmp_path: Path) -> None:
    """The tracer, end to end. `python` here is the one an `exec` tool call
    gets — resolved through PATH exactly as the agent's own command resolves
    it, not the venv's interpreter named directly."""
    sb, h = await _sandbox(tmp_path)
    _declare_a_dependency(sb._workspace(h))

    await ensure_project_env(sb, h)

    res = await sb.exec(h, ["python", "-c", "import tinydep; print(tinydep.MARK)"])
    assert res.exit_code == 0, res.stdout.decode() + res.stderr.decode()
    assert _MARK in res.stdout.decode()


@_needs_uv
async def test_the_interpreter_reports_the_venv_it_is_actually_in(tmp_path: Path) -> None:
    """`sys.prefix` is how the venv is lost, and how anything else the agent
    installs finds its way home.

    A symlinked shim passes "python runs" and every argv assertion while
    reporting the BASE interpreter here — which is why the import above failed
    and nothing else did."""
    sb, h = await _sandbox(tmp_path)
    _declare_a_dependency(sb._workspace(h))

    await ensure_project_env(sb, h)

    res = await sb.exec(h, ["python", "-c", "import sys; print(sys.prefix)"])
    assert res.exit_code == 0, res.stdout.decode() + res.stderr.decode()
    prefix = Path(res.stdout.decode().strip())
    assert prefix == Path(sb._require(h)) / ".venv", "the shim must not resolve past the venv"


@_needs_uv
async def test_a_workspace_that_declares_nothing_still_gets_a_working_python(
    tmp_path: Path,
) -> None:
    """Every profile that predates this ships no manifest. Their sandboxes must
    keep the interpreter they always had, and no venv is built for them."""
    sb, h = await _sandbox(tmp_path)

    await ensure_project_env(sb, h)

    res = await sb.exec(h, ["python", "-c", "print('still here')"])
    assert res.exit_code == 0, res.stdout.decode() + res.stderr.decode()
    assert not (Path(sb._require(h)) / ".venv" / "bin" / "python").exists()


@_needs_uv
async def test_a_sync_that_fails_leaves_no_interpreter_behind_to_adopt(tmp_path: Path) -> None:
    """uv creates the environment BEFORE it fails, and the shim probes exactly
    that directory. Left behind it would be adopted on the next exec — an
    interpreter with none of the packages anyone asked for, permanently,
    because the manifest outlives the sandbox.

    A manifest with no lock is the cheapest real way to make `--frozen` fail.
    """
    sb, h = await _sandbox(tmp_path)
    _declare_a_dependency(sb._workspace(h), lock=False)

    with pytest.raises(ProjectEnvError):
        await ensure_project_env(sb, h)

    assert not (Path(sb._require(h)) / ".venv").exists(), "a failure must take its venv with it"
    res = await sb.exec(h, ["python", "-c", "print('fell back')"])
    assert res.exit_code == 0, "and the sandbox must still have an interpreter"
