"""`python` must be the WORKSPACE's interpreter when it declares one (#775).

App-side copy. See `sandbox-host/tests/` for the twin — the one production
runs. Duplicated rather than shared because the host deliberately shares no
modules with workspace_app, and a fix that lands in only one of the two copies
is exactly the failure this shim exists to prevent.

Without this tier, `uv sync` builds a venv nobody uses: `python` still resolves
to the carrier, the workspace's own dependency is missing from it, and the
symptom is a `ModuleNotFoundError` that names the package but not the reason.
That is #581 ("installed into A, running in B") arriving through a new door.

These tests RUN the shim rather than inspecting its shape. The first version
asserted `is_symlink()` and `resolve()`, which is how the shim shipped pointing
at a venv that CPython then resolved straight past: the link was exactly what
the assertions described, and `python` still could not import the package. What
a caller gets when they run it is the only thing that was ever in question.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

_FROM_VENV = "ROUTED-TO-PROJECT-VENV"
_FROM_CARRIER = "ROUTED-TO-PYTHON-STACK"


async def _sandbox(tmp_path: Path, tools: Path | None) -> tuple[LocalProcessSandbox, SandboxHandle]:
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    return sb, await sb.create(SandboxSpec())


def _plant_venv(root: Path) -> Path:
    """A project venv where `uv sync` puts it: beside the workspace, in the
    infra area, so it is never walked, synced, or charged to the quota."""
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!/bin/sh\necho {_FROM_VENV}\n")
    python.chmod(0o755)
    return python


def _carrier(tools: Path) -> None:
    """The provisioned `python-stack` bundle, where the shim actually probes
    for it: `<root>/.tools/python-stack/launch`.

    ⚠️ The two backends assemble `.tools` differently — app-side it symlinks
    straight at the tools dir, host-side it points at a per-sandbox view built
    from `<tools>/builtin/` — so THIS plant path is the one line that differs
    between this file and its host twin.

    The app copy shipped with the host's layout, and nothing failed:
    `has_carrier` was simply always False, so the test claiming the venv "wins
    over the carrier" was deciding an election with one candidate, and the pip
    test passed because there had never been a pip shim to remove.
    `test_the_carrier_is_actually_reachable_from_this_fixture` is the control
    that keeps that from coming back."""
    stack = tools / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text(f"#!/bin/sh\necho {_FROM_CARRIER}\n")
    (stack / "launch").chmod(0o755)


def _run(shim: Path) -> str:
    return subprocess.run([shim], capture_output=True, text=True, timeout=30).stdout.strip()


async def test_the_carrier_is_actually_reachable_from_this_fixture(tmp_path: Path) -> None:
    """The positive control for every test below.

    Each of them means "the venv beat the carrier", which says nothing unless
    the carrier could have won. Plant one with NO venv and it must answer."""
    tools = tmp_path / "prebuilt"
    _carrier(tools)
    sb, h = await _sandbox(tmp_path, tools)

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    assert _run(Path(env["SANDBOX_JAILBIN"]) / "python") == _FROM_CARRIER


async def test_the_workspaces_own_interpreter_wins_over_the_carrier(tmp_path: Path) -> None:
    """A workspace that declared its dependencies gets THEM — the carrier's
    stack is what a profile without a declaration falls back to, not something
    layered underneath one that has."""
    tools = tmp_path / "prebuilt"
    _carrier(tools)
    sb, h = await _sandbox(tmp_path, tools)
    _plant_venv(Path(sb._require(h)))

    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    jailbin = Path(env["SANDBOX_JAILBIN"])

    assert _run(jailbin / "python") == _FROM_VENV
    assert _run(jailbin / "python3") == _FROM_VENV, "agents type `python3` at least as often"


async def test_the_shim_does_not_resolve_past_the_venv(tmp_path: Path) -> None:
    """The defect the shape assertions could not see.

    A symlink from `.jailbin` into the venv is what CPython resolves to find
    `pyvenv.cfg` — and from outside, it resolves straight THROUGH to the base
    interpreter, so `python` ran with none of the installed packages. The shim
    for this tier must therefore keep argv[0] inside the venv, which a wrapper
    does and a link cannot. `tests/sandbox/test_project_env_e2e.py` proves the
    consequence against a real `uv sync`; this pins the mechanism.
    """
    sb, h = await _sandbox(tmp_path, None)
    venv_python = _plant_venv(Path(sb._require(h)))

    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    shim = Path(env["SANDBOX_JAILBIN"]) / "python"

    assert not shim.is_symlink(), "a link would resolve past the venv it points into"
    assert str(venv_python) in shim.read_text(), "and it must still be the venv it runs"


async def test_a_venv_brings_no_pip_so_none_is_shimmed(tmp_path: Path) -> None:
    """A `uv` venv ships python and no pip — verified, not assumed.

    So there is nothing correct to point `pip` at. Pointing it at the venv's
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
    _plant_venv(Path(sb._require(h)))

    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    jailbin = Path(env["SANDBOX_JAILBIN"])

    assert _run(jailbin / "python") == _FROM_VENV, "the interpreter is still shimmed"
    assert not (jailbin / "pip").exists(), "no pip shim beats one that cannot work"


async def test_a_sandbox_that_loses_its_venv_stops_pointing_at_it(tmp_path: Path) -> None:
    """The shim is rebuilt per exec, and the two tiers are different SHAPES —
    a wrapper for the venv, a symlink for the carrier. So the rewrite has to
    replace one shape with the other, not skip because "something is already
    there".

    A failed `uv sync` removes the venv it half-built, which is exactly this
    transition, arriving on the very next command."""
    tools = tmp_path / "prebuilt"
    _carrier(tools)
    sb, h = await _sandbox(tmp_path, tools)
    root = Path(sb._require(h))
    _plant_venv(root)
    sb._exec_argv(h, ["true"])

    shutil.rmtree(root / ".venv")
    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    assert _run(Path(env["SANDBOX_JAILBIN"]) / "python") == _FROM_CARRIER


async def test_uv_builds_the_env_where_the_shim_looks_for_it(tmp_path: Path) -> None:
    """The join between the sync and the shim, and the one place they can
    silently disagree.

    Left alone, uv puts the env at `.venv` beside `pyproject.toml` — inside the
    workspace, where the quota charges for it and the mirror refuses to persist
    it, and where this shim never looks. Both halves would pass their own tests
    and the feature would not work.
    """
    sb, h = await _sandbox(tmp_path, None)
    root = Path(sb._require(h))

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    built = Path(env["UV_PROJECT_ENVIRONMENT"])
    assert built == root / ".venv", "not the `.venv` beside pyproject.toml, which is the point"
    # The literal path `_install_python_shim` probes. Spelled out rather than
    # imported so the two cannot drift apart while both still pass.
    assert built / "bin" / "python" == root / ".venv" / "bin" / "python"


async def test_uv_pip_is_pointed_at_the_project_venv_too(tmp_path: Path) -> None:
    """`UV_PROJECT_ENVIRONMENT` steers `uv sync`, `uv add` and `uv run`, and
    NOTHING else — measured. `uv pip install` ignores it and answers "No
    virtual environment found; run `uv venv`", and following that builds a
    `.venv` beside `pyproject.toml` that the shim never looks at. Our own error
    message walked people into the split this feature exists to close.

    VIRTUAL_ENV is what that half of the ecosystem reads."""
    sb, h = await _sandbox(tmp_path, None)
    root = Path(sb._require(h))
    _plant_venv(root)

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    assert env["VIRTUAL_ENV"] == str(root / ".venv")


async def test_the_servers_own_virtualenv_never_reaches_a_sandbox(tmp_path: Path) -> None:
    """The exec env starts as a copy of this process's, and a service run under
    `uv run` carries a VIRTUAL_ENV naming ITS OWN venv — which would point a
    sandbox's tooling at the server's interpreter. (This test process is such a
    server: pytest runs under `uv run`, so the value really is set here.)

    Production sets neither, so this is not a live leak — it is a value that
    must never be a property of how the server happened to be launched."""
    import os

    assert "VIRTUAL_ENV" in os.environ, "the control: this process really does carry one"
    sb, h = await _sandbox(tmp_path, None)

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    assert "VIRTUAL_ENV" not in env, "a workspace with no venv must be told there is none"


async def test_the_cache_is_never_keyed_by_something_that_gets_recycled(
    tmp_path: Path,
) -> None:
    """The one property that makes a persistent cache safe.

    uv verifies a wheel's sha256 when it DOWNLOADS and then trusts its own
    unpacked archive: a tampered file in a cache is installed verbatim into a
    fresh venv with nothing raised (measured). So a cache may only ever be
    reachable by the tenant that filled it.

    That rules out keying by uid, which is what production RECYCLES —
    `_UidPool`: "Freed ids are reused", and `kill` frees them. The moment a uid
    is released is both when a sweeper would consider its cache collectable and
    when the next item is most likely to be handed it. The item id is not
    recycled; neither is the uuid a sandbox created without one gets.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    h = await sb.create(SandboxSpec(), sandbox_id="item-a")

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    cache = Path(env["UV_CACHE_DIR"])
    assert cache.name == "item-a", "keyed by the tenant, not by anything reassignable"
    assert cache.is_dir()


async def test_the_servers_python_choice_is_not_the_sandboxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`UV_PYTHON` inherited from this process makes uv install a MANAGED
    interpreter inside the sandbox to satisfy a version the SERVER was
    configured with. Measured: with it, `ensure_project_env` took 2.91s and the
    venv's python pointed into a freshly downloaded CPython under `.home`;
    without it, 0.63s and the machine's existing interpreter.

    Same argument as VIRTUAL_ENV: production sets neither, which is exactly why
    neither may be inherited — otherwise the sandbox's toolchain is a property
    of how the server happened to be launched.
    """
    # The variable has to be SET for the assertion to mean anything. Without
    # this the test passes on any machine that simply does not have it — which
    # it did, and a mutation probe that removed the pop stayed green.
    monkeypatch.setenv("UV_PYTHON", "3.99")
    sb, h = await _sandbox(tmp_path, None)

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    assert os.environ["UV_PYTHON"] == "3.99", "the control: this process really carries one"
    assert "UV_PYTHON" not in env


async def test_a_venv_built_on_the_shim_is_refused_rather_than_looped(tmp_path: Path) -> None:
    """`python` must never be able to exec itself.

    `uv sync` picks its base interpreter off PATH, and `_exec_argv` puts
    `.jailbin` FIRST on PATH — so uv can build the venv on top of the shim, and
    the shim then points into that venv. Running `python` would exec the
    wrapper, which execs the venv's python, which IS the wrapper: forever, with
    no output and no exit until something kills it. A timeout with two empty
    streams is all anyone downstream would ever see.

    So a project interpreter that resolves back into the shim dir is not usable,
    and the sandbox falls back to what a profile that declared nothing gets.
    """
    tools = tmp_path / "prebuilt"
    _carrier(tools)
    sb, h = await _sandbox(tmp_path, tools)
    root = Path(sb._require(h))
    sb._exec_argv(h, ["true"])  # build the shim dir first, as a real sync would

    venv_python = root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(root / ".jailbin" / "python")

    _argv, _cwd, env = sb._exec_argv(h, ["true"])

    assert _run(Path(env["SANDBOX_JAILBIN"]) / "python") == _FROM_CARRIER


async def test_a_caller_can_name_its_own_wall_clock_budget(tmp_path: Path) -> None:
    """`exec` had no per-call timeout, so every command took the instance
    default. `uv sync` needs a far larger one — a cold start downloads a whole
    dependency stack, and on a slow link 60s is a KILL where a wait belongs.

    Asserted the other way round because it is cheap and unambiguous: a budget
    SHORTER than the instance default must also be honoured. The control is the
    same command with no override, which the same instance lets run.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, exec_timeout=30.0)
    h = await sb.create(SandboxSpec())

    quick = await sb.exec(h, ["sleep", "5"], exec_timeout=0.4)
    assert quick.exit_code == 124, "the caller's budget must be the one that applies"
    assert b"0.4s" in quick.stderr, quick.stderr

    control = await sb.exec(h, ["sleep", "0.1"])
    assert control.exit_code == 0, "and without an override the instance default still rules"


async def test_the_cache_is_keyed_by_the_item_so_it_outlives_one_sandbox(tmp_path: Path) -> None:
    """A cold start re-fetching the whole stack every time is the price P20 paid
    for safety. It does not have to be: the unsafe thing was the KEY, not the
    persistence.

    `uid` is unusable — production pools and RECYCLES it (`_UidPool`: "Freed ids
    are reused"), so `.uv-cache/{uid}` means "whoever holds that uid now", and a
    poisoned cache would be inherited by the next tenant. The item id is never
    recycled, so a cache keyed by it can only ever be reached by the tenant that
    filled it.

    Two sandboxes for the same item must therefore land on the same cache, and
    two different items must not.
    """
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False)
    first = await sb.create(SandboxSpec(), sandbox_id="item-a")
    again = await sb.create(SandboxSpec(), sandbox_id="item-a")
    other = await sb.create(SandboxSpec(), sandbox_id="item-b")

    a1 = Path(sb._exec_argv(first, ["true"])[2]["UV_CACHE_DIR"])
    a2 = Path(sb._exec_argv(again, ["true"])[2]["UV_CACHE_DIR"])
    b = Path(sb._exec_argv(other, ["true"])[2]["UV_CACHE_DIR"])

    assert a1 == a2, "the same item must reuse what it already downloaded"
    assert a1 != b, "and must never reach another item's"
    root = Path(sb._require(first))
    assert root not in a1.parents, "it has to outlive the sandbox to be worth anything"
