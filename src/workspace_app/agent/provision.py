"""Sandbox tool provisioning.

Extra analysis tools each live in their OWN repo with their OWN dependencies, so
the host app's dependency tree stays clean. At runtime we install them INTO the
sandbox (the isolation boundary) and expose each as a function tool the agent
calls — the tool runs via the sandbox's `exec`, so its files land in the
workspace and its deps never touch the app.

A `ToolDef` is declarative + data-only (so it can later be a specstar resource):

  - ``setup``  — argv commands run in the sandbox when it's provisioned
                 (``git clone`` / ``uv sync`` / ``pip install`` / ``cp`` …).
  - ``invoke`` — the base argv the function tool runs in the sandbox.
  - ``params`` — a JSON schema for the agent; call args map to argv as
                 *positional first, then ``--flag value``* (booleans → bare
                 ``--flag``). The schema is where an enum (e.g. a dataset name)
                 constrains the model so it can't invent a bad argument.
"""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import FunctionTool, RunContextWrapper

from ..sandbox.protocol import ExecResult, Sandbox, SandboxHandle
from .context import AgentToolContext
from .tools import _format_exec


class ProvisionError(RuntimeError):
    """A tool's setup step exited non-zero — the sandbox isn't usable for it."""

    def __init__(self, tool: str, cmd: list[str], result: ExecResult) -> None:
        self.tool = tool
        self.cmd = cmd
        self.result = result
        super().__init__(
            f"provisioning {tool!r} failed at `{' '.join(cmd)}` "
            f"(exit {result.exit_code}):\n{result.stdout}"
        )


def _empty_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    invoke: list[str]
    setup: list[list[str]] = field(default_factory=list)
    params_json_schema: dict[str, Any] = field(default_factory=_empty_schema)
    # param names that map to positional argv (in order); everything else in the
    # schema becomes a `--flag` (or bare `--flag` for booleans).
    positional: list[str] = field(default_factory=list)
    # A prebuilt, self-contained package on the host. At provision time it's
    # copied INTO the sandbox at `install_dir` (tar → upload → untar), so the
    # sandbox needs no uv / network / build step — `invoke` then runs an
    # entrypoint from the copied package (e.g. a launcher that starts a bundled
    # python). See `rca/sample_tools.py` + `scripts/prebuild_tools.py`.
    prebuilt: str | None = None
    install_dir: str | None = None  # sandbox dest; defaults to /opt/tools/<name>


def _tar_tree(src: Path) -> bytes:
    """A gz tar of `src`'s contents (children at the archive root), preserving
    permissions + symlinks — so an extracted relocatable venv still runs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for child in sorted(src.iterdir()):
            tar.add(child, arcname=child.name)
    return buf.getvalue()


def build_argv(tool: ToolDef, params: dict[str, Any]) -> list[str]:
    """`invoke` + the call's params: positional ones first (in declared order),
    the rest as `--flag value` (booleans → bare `--flag`; None/absent skipped)."""
    argv = list(tool.invoke)
    for name in tool.positional:
        value = params.get(name)
        if value is not None:
            argv.append(str(value))
    for key, value in params.items():
        if key in tool.positional or value is None:
            continue
        if isinstance(value, bool):
            if value:
                argv.append(f"--{key}")
        else:
            argv += [f"--{key}", str(value)]
    return argv


async def provision_tools(
    sandbox: Sandbox, handle: SandboxHandle, tools: Sequence[ToolDef]
) -> None:
    """Install each tool into the sandbox: copy in its prebuilt package (if any),
    then run its `setup`. Raises `ProvisionError` on the first non-zero exit."""
    for tool in tools:
        if tool.prebuilt:
            dest = tool.install_dir or f"/opt/tools/{tool.name}"
            # workspace-root-relative (upload is too) so it resolves the same in
            # the chroot jail (root=/) and unjailed (cwd=workspace dir).
            archive = f".provision-{tool.name}.tar.gz"
            await sandbox.upload(handle, _tar_tree(Path(tool.prebuilt)), archive)
            # --no-same-owner: inside the userns jail we run as mapped-root, so
            # restoring the host uid/gid would fail (`chown … Invalid argument`).
            # Keep file modes (exec bits) but don't chown.
            extract = await sandbox.exec(
                handle,
                ["sh", "-c", f"mkdir -p {dest} && tar xzf {archive} -C {dest} --no-same-owner"],
            )
            if extract.exit_code != 0:
                raise ProvisionError(tool.name, ["tar", "-C", dest], extract)
        for cmd in tool.setup:
            result = await sandbox.exec(handle, cmd)
            if result.exit_code != 0:
                raise ProvisionError(tool.name, cmd, result)


def build_provisioned_tools(tools: Sequence[ToolDef]) -> list[FunctionTool]:
    """One `FunctionTool` per `ToolDef`; calling it execs `invoke + argv` in the
    sandbox (lazily creating it) and returns the formatted output."""
    return [_to_function_tool(t) for t in tools]


def _to_function_tool(tool: ToolDef) -> FunctionTool:
    async def on_invoke(ctx: RunContextWrapper[AgentToolContext], args_json: str) -> str:
        params = json.loads(args_json) if args_json else {}
        actx = ctx.context
        assert actx.sandbox is not None  # provisioned tools imply a sandbox
        handle = await actx.ensure_sandbox()
        result = await actx.sandbox.exec(
            handle, build_argv(tool, params), on_output=actx.on_exec_output
        )
        return _format_exec(result)

    return FunctionTool(
        name=tool.name,
        description=tool.description,
        params_json_schema=tool.params_json_schema,
        on_invoke_tool=on_invoke,
        # Lenient: ToolDef schemas carry optional params (rows/out/json) without
        # the strict-mode `required`/nullable gymnastics.
        strict_json_schema=False,
    )
