"""`python` must be the WORKSPACE's interpreter when it declares one (#775).

Host-side copy — the one production runs. See `tests/sandbox/` app-side for
the twin. Duplicated rather than shared because the host deliberately shares no
modules with workspace_app, and a fix that lands in only one of the two copies
is exactly the failure this shim exists to prevent.

Without this tier, `uv sync` builds a venv nobody uses: `python` still resolves
to the carrier, the workspace's own dependency is missing from it, and the
symptom is a `ModuleNotFoundError` that names the package but not the reason.
That is #581 ("installed into A, running in B") arriving through a new door.
"""

from __future__ import annotations

from pathlib import Path

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec


async def _sandbox(tmp_path: Path, tools: Path | None) -> tuple[LocalProcessSandbox, object]:
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    return sb, await sb.create(SandboxSpec())


def _plant_venv(root: Path) -> Path:
    """A project venv where `uv sync` puts it: beside the workspace, in the
    infra area, so it is never walked, synced, or charged to the quota."""
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho ROUTED-TO-PROJECT-VENV\n")
    python.chmod(0o755)
    return python


def _carrier(tools: Path) -> None:
    stack = tools / "builtin" / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)


async def test_the_workspaces_own_interpreter_wins_over_the_carrier(tmp_path: Path) -> None:
    """A workspace that declared its dependencies gets THEM — the carrier's
    stack is what a profile without a declaration falls back to, not something
    layered underneath one that has."""
    tools = tmp_path / "prebuilt"
    _carrier(tools)
    sb, h = await _sandbox(tmp_path, tools)
    venv_python = _plant_venv(Path(sb._require(h)))  # ty: ignore[invalid-argument-type]

    _argv, _cwd, env = sb._exec_argv(h, ["true"])  # ty: ignore[invalid-argument-type]
    jailbin = Path(env["SANDBOX_JAILBIN"])

    assert (jailbin / "python").resolve() == venv_python.resolve()
    assert (jailbin / "python3").resolve() == venv_python.resolve()


async def test_a_venv_brings_no_pip_so_none_is_shimmed(tmp_path: Path) -> None:
    """A `uv` venv ships python and no pip — verified, not assumed.

    So there is nothing correct to point `pip` at. Symlinking it to the venv's
    `python` would run `python install X`, which is not a command; that is the
    same "a shim that cannot work is worse than none" the no-carrier tier
    already decided, so `pip` is left to the image.

    ⚠️ The consequence is real and deliberate: in a declared workspace
    `pip install` reaches the IMAGE's interpreter, not the one `python` runs.
    The carrier tier does not have this problem (its launcher dispatches on the
    name it is called as), so declaring dependencies makes `pip` worse, not
    better. The answer is `uv add` — which the sandbox prompt says, because a
    person who types `pip` is owed a route rather than a refusal.
    """
    tools = tmp_path / "prebuilt"
    _carrier(tools)
    sb, h = await _sandbox(tmp_path, tools)
    _plant_venv(Path(sb._require(h)))  # ty: ignore[invalid-argument-type]

    _argv, _cwd, env = sb._exec_argv(h, ["true"])  # ty: ignore[invalid-argument-type]
    jailbin = Path(env["SANDBOX_JAILBIN"])

    assert (jailbin / "python").is_symlink(), "the interpreter is still shimmed"
    assert not (jailbin / "pip").exists(), "no pip shim beats one that cannot work"


async def test_uv_builds_the_env_where_the_shim_looks_for_it(tmp_path: Path) -> None:
    """The join between the sync and the shim, and the one place they can
    silently disagree.

    Left alone, uv puts the env at `.venv` beside `pyproject.toml` — inside the
    workspace, where the quota charges for it and the mirror refuses to persist
    it, and where this shim never looks. Both halves would pass their own tests
    and the feature would not work.
    """
    sb, h = await _sandbox(tmp_path, None)
    root = Path(sb._require(h))  # ty: ignore[invalid-argument-type]

    _argv, _cwd, env = sb._exec_argv(h, ["true"])  # ty: ignore[invalid-argument-type]

    built = Path(env["UV_PROJECT_ENVIRONMENT"])
    assert built == root / ".venv", "not the `.venv` beside pyproject.toml, which is the point"
    # The literal path `_install_python_shim` probes. Spelled out rather than
    # imported so the two cannot drift apart while both still pass.
    assert built / "bin" / "python" == root / ".venv" / "bin" / "python"
