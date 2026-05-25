from __future__ import annotations

from agents import FunctionTool, RunContextWrapper, function_tool

from ..filestore.protocol import FileNotFound, FileStore
from ..sandbox.protocol import ExecResult
from .context import AgentToolContext


def _format_exec(r: ExecResult) -> str:
    stdout = r.stdout.decode("utf-8", errors="replace")
    stderr = r.stderr.decode("utf-8", errors="replace")
    return f"exit_code={r.exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


def _workspace(ctx: RunContextWrapper[AgentToolContext]) -> tuple[FileStore, str]:
    """The (filestore, investigation_id) the RCA file tools require."""
    fs, inv = ctx.context.filestore, ctx.context.investigation_id
    assert fs is not None and inv is not None  # file tools imply an RCA context
    return fs, inv


async def exec_impl(ctx: RunContextWrapper[AgentToolContext], cmd: list[str]) -> str:
    """Run a shell command inside the workspace sandbox."""
    assert ctx.context.sync is not None and ctx.context.sandbox is not None
    _, inv = _workspace(ctx)
    handle = await ctx.context.ensure_sandbox()
    # Flush any FileStore writes the agent made via write_file/delete_file
    # so the shell command sees the same view of the workspace.
    await ctx.context.sync.flush(inv, handle)
    # Stream stdout live (when the runner wired a sink) so a long-running
    # command's output shows up in run history as it happens.
    result = await ctx.context.sandbox.exec(handle, cmd, on_output=ctx.context.on_exec_output)
    return _format_exec(result)


async def read_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Read a file from the workspace file store."""
    fs, inv = _workspace(ctx)
    try:
        data = await fs.read(inv, path)
    except FileNotFound:
        return f"error: file not found: {path}"
    return data.decode("utf-8", errors="replace")


async def write_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str, content: str) -> str:
    """Write a file to the workspace file store."""
    fs, inv = _workspace(ctx)
    await fs.write(inv, path, content.encode("utf-8"))
    return f"wrote {len(content)} bytes to {path}"


async def ls_impl(ctx: RunContextWrapper[AgentToolContext], prefix: str = "") -> list[str]:
    """List files in the workspace file store, optionally filtered by prefix."""
    fs, inv = _workspace(ctx)
    return await fs.ls(inv, prefix)


async def exists_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> bool:
    """Check whether a file exists in the workspace file store."""
    fs, inv = _workspace(ctx)
    return await fs.exists(inv, path)


async def delete_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Delete a file from the workspace file store."""
    fs, inv = _workspace(ctx)
    try:
        await fs.delete(inv, path)
    except FileNotFound:
        return f"error: file not found: {path}"
    return f"deleted {path}"


def kb_search_impl(ctx: RunContextWrapper[AgentToolContext], query: str) -> str:
    """Search the knowledge base; returns numbered passages to cite as [n].

    Call this whenever you need facts from the documents — and again, with a
    refined query, when an answer references something else worth looking up.
    Each result is numbered globally across the turn; cite a claim with the
    matching [n]. Numbers persist across calls, so [1] always means the same
    passage.
    """
    retriever = ctx.context.retriever
    assert retriever is not None  # kb_search implies a KB context
    registry = ctx.context.kb_passages
    seen = {(p.document_id, p.start, p.end): i for i, p in enumerate(registry)}

    lines: list[str] = []
    for passage in retriever.search(query, ctx.context.collection_ids):
        key = (passage.document_id, passage.start, passage.end)
        idx = seen.get(key)
        if idx is None:
            idx = len(registry)
            seen[key] = idx
            registry.append(passage)
        lines.append(f"[{idx + 1}] {passage.filename}: {passage.text}")

    if not lines:
        return "No matching passages in the knowledge base."
    return "\n\n".join(lines)


async def ask_knowledge_base_impl(ctx: RunContextWrapper[AgentToolContext], question: str) -> str:
    """Ask the knowledge-base agent a question about the in-house documents.

    Use this when the investigation needs facts, procedures, or history that
    live in the knowledge base rather than in the workspace files. Returns a
    synthesized answer with a Sources list. Phrase a focused question, not just
    keywords.
    """
    ask_kb = ctx.context.ask_kb
    assert ask_kb is not None  # the API layer wires this for RCA runs
    # Hand the KB bridge this run's output sink (so the KB agent's searches and
    # reasoning stream live under this tool call) + this investigation's id (so
    # its KB citations are logged against it).
    return await ask_kb(question, ctx.context.on_exec_output, ctx.context.investigation_id)


async def mention_user_impl(
    ctx: RunContextWrapper[AgentToolContext], user_id: str, reason: str = ""
) -> str:
    """Summon a human teammate to look at this investigation.

    Use when the case needs a person — a domain expert, the owner, a reviewer.
    They get a notification linking here. Pass their user id and a short reason.
    """
    mention = ctx.context.mention
    assert mention is not None  # the API layer wires this for RCA runs
    investigation_id = ctx.context.investigation_id
    assert investigation_id is not None  # mentions belong to an investigation
    mention(investigation_id, [user_id], reason)
    return f"Notified {user_id} to come look at this investigation."


_IMPLS = {
    "exec": exec_impl,
    "read_file": read_file_impl,
    "write_file": write_file_impl,
    "ls": ls_impl,
    "exists": exists_impl,
    "delete_file": delete_file_impl,
    "mention_user": mention_user_impl,
    "ask_knowledge_base": ask_knowledge_base_impl,
    "kb_search": kb_search_impl,
}

# The RCA workspace toolset — what `build_tools(None)` hands out. It includes
# ask_knowledge_base (the RCA agent consults the KB through it). `kb_search`
# lives in `_IMPLS` for lookup but is opt-in only — it's the KB agent's OWN
# tool and needs a retriever in the context, which RCA runs never set.
_WORKSPACE_TOOLS = [
    "exec",
    "read_file",
    "write_file",
    "ls",
    "exists",
    "delete_file",
    "ask_knowledge_base",
    "mention_user",
]


def build_tools(allowed: list[str] | None = None) -> list[FunctionTool]:
    """Build FunctionTool list for the Agent. If `allowed` is None, the
    workspace toolset (file/exec); otherwise exactly the named tools."""
    names = allowed if allowed is not None else _WORKSPACE_TOOLS
    return [function_tool(_IMPLS[n], name_override=n) for n in names]
