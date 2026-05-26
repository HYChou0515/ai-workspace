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
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


async def test_file_tools_use_injected_files_facade():
    """When the caller injects a WorkspaceFiles facade, the file tools go
    through it (covers the non-fallback branch of _workspace)."""
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files))
    await write_file_impl(ctx, "/a.txt", "hello")
    assert await read_file_impl(ctx, "/a.txt") == "hello"
    assert await exists_impl(ctx, "/a.txt") is True


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


async def test_exec_streams_output_to_context_sink(ctx: RunContextWrapper[AgentToolContext]):
    """exec forwards the command's stdout to the context's on_exec_output sink
    as it runs, so the runner can surface it live in run history."""
    chunks: list[bytes] = []
    ctx.context.on_exec_output = chunks.append
    await exec_impl(ctx, ["echo", "hello"])
    assert b"".join(chunks) == b"hello\n"


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


async def test_exec_after_write_file_sees_the_content(
    ctx: RunContextWrapper[AgentToolContext],
):
    """The user-facing promise: agent writes via write_file (which lands
    in FileStore), then runs a shell command that needs to read the file.
    The flush hook is what makes this work."""
    await write_file_impl(ctx, "/notes.txt", "hello from agent")
    out = await exec_impl(ctx, ["cat", "/notes.txt"])
    assert "hello from agent" in out


def test_build_tools_returns_the_workspace_set_by_default():
    tools = build_tools()
    names = {t.name for t in tools}
    # file/exec tools + ask_knowledge_base (RCA consults the KB); NOT kb_search,
    # which is the KB agent's own opt-in tool.
    assert names == {
        "exec",
        "read_file",
        "write_file",
        "ls",
        "exists",
        "delete_file",
        "ask_knowledge_base",
        "mention_user",
    }
    assert "kb_search" not in names
    assert all(isinstance(t, FunctionTool) for t in tools)


def test_build_tools_filters_by_allowed_list():
    tools = build_tools(allowed=["exec", "read_file"])
    assert {t.name for t in tools} == {"exec", "read_file"}


async def test_ask_knowledge_base_delegates_to_the_context_bridge():
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl

    received: dict[str, object] = {}

    async def fake_ask(question: str, emit: object, origin_id: object) -> str:
        received["emit"] = emit
        received["origin_id"] = origin_id
        return f"KB answer to: {question}"

    def sink(b: bytes) -> None: ...

    ctx = RunContextWrapper(AgentToolContext(ask_kb=fake_ask, on_exec_output=sink))
    out = await ask_knowledge_base_impl(ctx, "why did zone three drift?")
    assert out == "KB answer to: why did zone three drift?"
    # the run's output sink is handed to the bridge so KB progress can stream
    assert received["emit"] is sink


async def test_mention_user_delegates_to_the_context_hook():
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, mention_user_impl

    calls: list[tuple[str, list[str], str]] = []

    def fake_mention(investigation_id: str, user_ids: list[str], note: str) -> None:
        calls.append((investigation_id, user_ids, note))

    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1", mention=fake_mention))
    out = await mention_user_impl(ctx, "alice", "please review the SPC")
    assert "alice" in out
    assert calls == [("inv-1", ["alice"], "please review the SPC")]
