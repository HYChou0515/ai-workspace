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

A fourth had the same shape without needing the sandbox: `ProjectEnvError` read
uv's `stdout`, and uv writes everything to `stderr`, so the error an operator
was promised had an empty body — behind nine unit tests whose doubles put uv's
words on stdout and therefore agreed with the bug.

None of those is visible from either end alone. So this file enters where the
agent enters — `ensure_project_env`, then `exec(["python", …])` — and asserts
what the user would see.

**Most of it runs in CI.** A project with no dependencies locks and syncs with
the network fully off, and that is enough to pin the venv's location, the
interpreter's identity and the failure path — the three that were fatal. Only
the two tests that must actually install a package need an index, and those
carry the `integration` marker. Keeping the whole file behind that marker would
have left the one test that catches this defect class outside every routine
gate, which is how the class got here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from workspace_app.agent.python_env import ProjectEnvError, ensure_project_env
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

_needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="needs a real uv on PATH")

#: Content, not a version number: a package that merely imports proves nothing
#: about WHICH copy answered.
_MARK = "the-workspace-got-its-own-package"

_BARE_MANIFEST = """\
[project]
name = "declared"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []
"""

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


def _declare_nothing_in_particular(workspace: Path, *, lock: bool = True) -> None:
    """A profile that declares a project but no packages. Locks and syncs with
    no index access at all, so this runs anywhere the repo's own tooling does."""
    (workspace / "pyproject.toml").write_text(_BARE_MANIFEST)
    if lock:
        subprocess.run(["uv", "lock"], cwd=workspace, check=True, capture_output=True)


def _declare_a_dependency(workspace: Path) -> None:
    """What a profile ships when it actually wants a package: a `pyproject.toml`
    and a `uv.lock`. Built from a directory inside the workspace so the result
    is identical on every run."""
    src = workspace / "dep" / "src" / "tinydep"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(f'MARK = "{_MARK}"\n')
    (workspace / "dep" / "pyproject.toml").write_text(_DEP_MANIFEST)
    (workspace / "pyproject.toml").write_text(_PROJECT_MANIFEST)
    subprocess.run(["uv", "lock"], cwd=workspace, check=True, capture_output=True)


@_needs_uv
async def test_the_interpreter_reports_the_venv_it_is_actually_in(tmp_path: Path) -> None:
    """`sys.prefix` is how the venv is lost.

    A symlinked shim passes "python runs" and every argv assertion while
    reporting the BASE interpreter here — so the packages a profile declared
    were installed into somewhere nothing ever ran. One assertion, and it is
    the one that catches both of this feature's fatal defects: the venv built
    where the shim does not look, and the shim resolving past the venv.
    """
    sb, h = await _sandbox(tmp_path)
    _declare_nothing_in_particular(sb._workspace(h))

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
async def test_a_sync_that_fails_says_why_and_leaves_no_interpreter_to_adopt(
    tmp_path: Path,
) -> None:
    """Two properties of the failure path, both of which shipped broken.

    uv creates the environment BEFORE it fails, and the shim probes exactly
    that directory — left behind it would be adopted on the next exec, an
    interpreter with none of the packages anyone asked for, permanently,
    because the manifest outlives the sandbox.

    And the whole reason this raises instead of degrading is that the person
    who can act — whoever runs the deployment — gets uv's own reason. That
    reason was missing: uv writes errors and progress to stderr and nothing to
    stdout, and the error formatted stdout, so its body was empty.

    A manifest with no lock is the cheapest real way to make `--frozen` fail.
    """
    sb, h = await _sandbox(tmp_path)
    _declare_nothing_in_particular(sb._workspace(h), lock=False)

    with pytest.raises(ProjectEnvError) as caught:
        await ensure_project_env(sb, h)

    assert "uv.lock" in str(caught.value), "uv's own reason must survive into the error"

    # The invariant is "nothing for the shim to adopt", not "the directory is
    # gone". The cleanup runs INSIDE the sandbox as the item uid, and in
    # production `<root>` belongs to the service — so `rm -rf` empties the venv
    # but cannot unlink the directory itself (measured: `rm: cannot remove
    # …/.venv: Permission denied`). Both outcomes satisfy what matters, and uv
    # accepts the empty directory next time round, so this asserts the property
    # rather than the artefact of whichever path ran it.
    assert not (Path(sb._require(h)) / ".venv" / "bin" / "python").exists(), (
        "a failed sync must not leave an interpreter behind"
    )
    res = await sb.exec(h, ["python", "-c", "print('fell back')"])
    assert res.exit_code == 0, "and the sandbox must still have an interpreter"


@_needs_uv
@pytest.mark.integration
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
@pytest.mark.integration
async def test_a_package_the_person_installed_survives_the_next_turn(tmp_path: Path) -> None:
    """Preparation is keyed to the `AgentToolContext`, which is built once per
    TURN — so `ensure_project_env` runs again on a warm sandbox. A plain
    `uv sync` makes the environment match the lock EXACTLY, which means it
    uninstalls whatever the person put there themselves:

        uv pip install idna   ->  idna OK
        uv sync --frozen      ->  Uninstalled 1 package  - idna==3.19

    `uv add` is the route we recommend. It is not a rule we enforce by deleting
    the alternative behind someone's back one turn later, with nothing said.
    """
    sb, h = await _sandbox(tmp_path)
    ws = sb._workspace(h)
    _declare_a_dependency(ws)
    await ensure_project_env(sb, h)

    extra = ws / "extra" / "src" / "extradep"
    extra.mkdir(parents=True)
    (extra / "__init__.py").write_text("OK = True\n")
    (ws / "extra" / "pyproject.toml").write_text(_DEP_MANIFEST.replace("tinydep", "extradep"))
    installed = await sb.exec(h, ["uv", "pip", "install", "./extra"])
    assert installed.exit_code == 0, installed.stderr.decode()

    await ensure_project_env(sb, h)  # the next turn

    res = await sb.exec(h, ["python", "-c", "import extradep; print('survived')"])
    assert res.exit_code == 0, res.stdout.decode() + res.stderr.decode()
