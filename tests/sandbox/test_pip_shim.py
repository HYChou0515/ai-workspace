"""`pip` in a sandbox must be the carrier's pip.

`python` has been shimmed to the python-stack carrier since #350; `pip` never
was. So a bare `pip install X` fell down the rest of PATH to the IMAGE's python
— jailed `/usr/bin/pip`, unjailed the host service's own venv — which is both a
different interpreter and a different `HOME` from the carrier that the agent's
`python` actually is. Two independent misses: the install landed in a `.local`
the carrier does not read, under a version the carrier does not look for. pip
reported success, the import failed, and nothing in between said why.

The shim dir is what the exec path puts on PATH, so its contents ARE the
contract; these assert against the dir the exec path itself names
(`SANDBOX_JAILBIN`) rather than reaching for internals.

Plain unit tests on purpose: the sandbox's own shim coverage lives in an
all-`integration` module that CI does not run, which is how `pip` stayed missing
from the shim list without anything going red.
"""

from __future__ import annotations

from pathlib import Path

from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxSpec


def _carrier(tmp_path: Path) -> Path:
    tools = tmp_path / "prebuilt"
    stack = tools / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)
    return tools


async def _shim_dir(tmp_path: Path, tools: Path | None) -> Path:
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    return Path(env["SANDBOX_JAILBIN"])


async def test_pip_resolves_to_the_same_carrier_python_does(tmp_path: Path) -> None:
    """The point of the whole fix: one interpreter answers both names, so a
    package installed through `pip` is one the `python` beside it can import."""
    tools = _carrier(tmp_path)
    jailbin = await _shim_dir(tmp_path, tools)
    assert (jailbin / "pip").resolve() == (jailbin / "python").resolve()


async def test_without_a_carrier_pip_is_left_to_the_image(tmp_path: Path) -> None:
    """With no carrier the `python` shims fall back to `/usr/bin/python3` — and
    a `pip` symlink pointing THERE would run `python3 install X`, which is not a
    command at all. A shim that cannot work is worse than no shim: no shim lets
    the image's own pip answer, which is at least a working pip. So the pip
    names ride the carrier branch only."""
    jailbin = await _shim_dir(tmp_path, None)
    assert (jailbin / "python").is_symlink()  # the fallback still shims python
    assert not (jailbin / "pip").exists()


async def test_a_carrier_that_goes_away_takes_its_pip_shims_with_it(tmp_path: Path) -> None:
    """A dangling `pip` is the failure the carrier-only rule exists to avoid,
    reached from the other direction: the tools mount disappears, `python` is
    re-pointed at the /usr/bin/python3 fallback, and a leftover `pip` symlink
    now names a path that is not there. It must be removed, so the image's own
    pip — a working pip — answers instead of ENOENT."""
    tools = _carrier(tmp_path)
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    jailbin = Path(env["SANDBOX_JAILBIN"])
    assert (jailbin / "pip").is_symlink()

    (tools / "python-stack" / "launch").unlink()  # the carrier goes away
    sb._exec_argv(h, ["true"])

    assert not (jailbin / "pip").exists()
    assert (jailbin / "python").is_symlink()  # python still answers, via the fallback
