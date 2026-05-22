from agents import FunctionTool, RunContextWrapper

from workspace_app.agent import (
    AgentToolContext,
    build_tools,
    delete_file_impl,
    exec_impl,
    exists_impl,
    ls_impl,
    read_file_impl,
    write_file_impl,
)


async def test_exec_lazy_creates_sandbox_on_first_call(
    ctx: RunContextWrapper[AgentToolContext],
):
    assert ctx.context.handle is None
    await exec_impl(ctx, ["echo", "hi"])
    assert ctx.context.handle is not None


async def test_exec_returns_formatted_output(ctx: RunContextWrapper[AgentToolContext]):
    out = await exec_impl(ctx, ["echo", "hello"])
    assert "exit_code=0" in out
    assert "hello" in out


async def test_exec_reuses_same_sandbox_handle(ctx: RunContextWrapper[AgentToolContext]):
    await exec_impl(ctx, ["echo", "a"])
    h1 = ctx.context.handle
    await exec_impl(ctx, ["echo", "b"])
    assert ctx.context.handle is h1


async def test_write_then_read_roundtrip(ctx: RunContextWrapper[AgentToolContext]):
    msg = await write_file_impl(ctx, "/notes.txt", "hello world")
    assert "wrote" in msg
    assert await read_file_impl(ctx, "/notes.txt") == "hello world"


async def test_read_missing_returns_error_string(
    ctx: RunContextWrapper[AgentToolContext],
):
    out = await read_file_impl(ctx, "/never")
    assert "error" in out.lower()


async def test_file_ops_do_not_create_sandbox(
    ctx: RunContextWrapper[AgentToolContext],
):
    await write_file_impl(ctx, "/x", "x")
    await read_file_impl(ctx, "/x")
    await ls_impl(ctx)
    await exists_impl(ctx, "/x")
    assert ctx.context.handle is None


async def test_ls_after_writes(ctx: RunContextWrapper[AgentToolContext]):
    await write_file_impl(ctx, "/a", "1")
    await write_file_impl(ctx, "/b", "2")
    assert sorted(await ls_impl(ctx)) == ["/a", "/b"]


async def test_exists_returns_bool(ctx: RunContextWrapper[AgentToolContext]):
    await write_file_impl(ctx, "/x", "x")
    assert await exists_impl(ctx, "/x") is True
    assert await exists_impl(ctx, "/missing") is False


async def test_delete_removes_file(ctx: RunContextWrapper[AgentToolContext]):
    await write_file_impl(ctx, "/x", "x")
    await delete_file_impl(ctx, "/x")
    assert await exists_impl(ctx, "/x") is False


async def test_delete_missing_returns_error_string(
    ctx: RunContextWrapper[AgentToolContext],
):
    out = await delete_file_impl(ctx, "/never")
    assert "error" in out.lower()


def test_build_tools_returns_all_six_by_default():
    tools = build_tools()
    names = {t.name for t in tools}
    assert names == {"exec", "read_file", "write_file", "ls", "exists", "delete_file"}
    assert all(isinstance(t, FunctionTool) for t in tools)


def test_build_tools_filters_by_allowed_list():
    tools = build_tools(allowed=["exec", "read_file"])
    assert {t.name for t in tools} == {"exec", "read_file"}
