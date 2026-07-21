"""`pip` must be the carrier's pip — host-side copy (the one production runs).

See `tests/sandbox/test_pip_shim.py` app-side for the full rationale. Duplicated
rather than shared because the host deliberately shares no modules with
workspace_app, and a fix that lands in only one of the two copies is exactly the
failure mode this whole change is about.
"""

from __future__ import annotations

from pathlib import Path

from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.protocol import SandboxSpec


async def _shim_dir(tmp_path: Path, tools: Path | None) -> Path:
    sb = LocalProcessSandbox(root_dir=tmp_path / "sb", isolate=False, tools_dir=tools)
    h = await sb.create(SandboxSpec())
    _argv, _cwd, env = sb._exec_argv(h, ["true"])
    return Path(env["SANDBOX_JAILBIN"])


async def test_pip_resolves_to_the_same_carrier_python_does(tmp_path: Path) -> None:
    tools = tmp_path / "prebuilt"
    stack = tools / "python-stack"
    stack.mkdir(parents=True)
    (stack / "launch").write_text("#!/bin/sh\necho ROUTED-TO-PYTHON-STACK\n")
    (stack / "launch").chmod(0o755)
    jailbin = await _shim_dir(tmp_path, tools)
    assert (jailbin / "pip").resolve() == (jailbin / "python").resolve()


async def test_without_a_carrier_pip_is_left_to_the_image(tmp_path: Path) -> None:
    """A `pip` pointing at the /usr/bin/python3 fallback would run
    `python3 install X` — not a command. No shim beats a broken one."""
    jailbin = await _shim_dir(tmp_path, None)
    assert (jailbin / "python").is_symlink()
    assert not (jailbin / "pip").exists()
