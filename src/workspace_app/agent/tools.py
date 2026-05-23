from __future__ import annotations

from agents import FunctionTool, RunContextWrapper, function_tool

from ..filestore.protocol import FileNotFound
from ..sandbox.protocol import ExecResult
from .context import AgentToolContext


def _format_exec(r: ExecResult) -> str:
    stdout = r.stdout.decode("utf-8", errors="replace")
    stderr = r.stderr.decode("utf-8", errors="replace")
    return f"exit_code={r.exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


async def exec_impl(ctx: RunContextWrapper[AgentToolContext], cmd: list[str]) -> str:
    """Run a shell command inside the workspace sandbox."""
    handle = await ctx.context.ensure_sandbox()
    # Flush any FileStore writes the agent made via write_file/delete_file
    # so the shell command sees the same view of the workspace.
    await ctx.context.sync.flush(ctx.context.investigation_id, handle)
    result = await ctx.context.sandbox.exec(handle, cmd)
    return _format_exec(result)


async def read_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Read a file from the workspace file store."""
    try:
        data = await ctx.context.filestore.read(ctx.context.investigation_id, path)
    except FileNotFound:
        return f"error: file not found: {path}"
    return data.decode("utf-8", errors="replace")


async def write_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str, content: str) -> str:
    """Write a file to the workspace file store."""
    await ctx.context.filestore.write(ctx.context.investigation_id, path, content.encode("utf-8"))
    return f"wrote {len(content)} bytes to {path}"


async def ls_impl(ctx: RunContextWrapper[AgentToolContext], prefix: str = "") -> list[str]:
    """List files in the workspace file store, optionally filtered by prefix."""
    return await ctx.context.filestore.ls(ctx.context.investigation_id, prefix)


async def exists_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> bool:
    """Check whether a file exists in the workspace file store."""
    return await ctx.context.filestore.exists(ctx.context.investigation_id, path)


async def delete_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Delete a file from the workspace file store."""
    try:
        await ctx.context.filestore.delete(ctx.context.investigation_id, path)
    except FileNotFound:
        return f"error: file not found: {path}"
    return f"deleted {path}"


_IMPLS = {
    "exec": exec_impl,
    "read_file": read_file_impl,
    "write_file": write_file_impl,
    "ls": ls_impl,
    "exists": exists_impl,
    "delete_file": delete_file_impl,
}


def build_tools(allowed: list[str] | None = None) -> list[FunctionTool]:
    """Build FunctionTool list for the Agent. If `allowed` is None, all tools."""
    names = allowed if allowed is not None else list(_IMPLS)
    return [function_tool(_IMPLS[n], name_override=n) for n in names]
