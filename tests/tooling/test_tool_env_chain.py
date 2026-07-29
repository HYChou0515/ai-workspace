"""The whole chain, with nothing faked in the middle.

`item.env_vars` → the tool dispatch → a real `LocalProcessSandbox.exec` → a
launcher built from the real template → the tool's own `os.environ`.

Each half has unit tests, and two halves can agree with each other while
disagreeing with reality — the seam between them is where this feature already
failed once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agents.tool_context import ToolContext
from agents.usage import Usage

from workspace_app.agent import AgentToolContext
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxSpec
from workspace_app.tooling.prebuild import _LAUNCH
from workspace_app.tooling.registry import CommandInfo, PackageInfo, _to_function_tool

pytestmark = pytest.mark.integration


def _stub_bundle(root: Path) -> str:
    """A bundle whose `launch` is the REAL template. The bundled interpreter is
    `/bin/sh` and the entry point prints its environment, so what comes back is
    exactly what the tool would have seen."""
    bundle = root / ".tools" / "pkg"
    (bundle / "python" / "bin").mkdir(parents=True)
    (bundle / "python" / "bin" / "python3.12").symlink_to("/bin/sh")
    (bundle / ".venv" / "bin").mkdir(parents=True)
    # The "tool": prints the environment it was handed, which is the whole
    # question this test asks.
    entry = bundle / ".venv" / "bin" / "pkg"
    entry.write_text("#!/bin/sh\nexec env\n")
    entry.chmod(0o755)
    launch = bundle / "launch"
    launch.write_text(_LAUNCH.format(ver="3.12", tool="pkg"))
    launch.chmod(0o755)
    return "../.tools/pkg"


async def test_an_items_variable_reaches_the_tools_environment(tmp_path):
    sandbox = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    handle = await sandbox.create(SandboxSpec(), sandbox_id="item-1")
    install_dir = _stub_bundle(tmp_path / "item-1")

    seen: list[bytes] = []
    actx = AgentToolContext(
        investigation_id="item-1",
        sandbox=sandbox,
        handle=handle,
        user_env={"API_KEY": "sk-1", "PIP_USER": "0"},
        on_exec_output=seen.append,
    )
    pkg = PackageInfo(name="pkg", install_dir=install_dir, commands=())
    cmd = CommandInfo(name="do", description="d", params_json_schema={})
    tool = _to_function_tool(pkg, cmd)
    tctx: ToolContext[Any] = ToolContext(
        context=actx, tool_name="do", tool_call_id="id", tool_arguments="{}", usage=Usage()
    )
    await tool.on_invoke_tool(tctx, "{}")

    env = dict(line.split("=", 1) for line in b"".join(seen).decode().splitlines() if "=" in line)
    assert env["API_KEY"] == "sk-1"
    # The launcher sets PIP_USER=1 for itself; the user's value still wins.
    assert env["PIP_USER"] == "0"
