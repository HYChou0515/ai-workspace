"""#538 — the workspace quota reaches the agent's own file tools.

The quota used to be enforced only by the HTTP upload endpoint, so the one actor
most able to fill a workspace — the agent, writing files and downloading things
into the sandbox — was the one actor never checked. The gate now lives in the
`WorkspaceFiles` chokepoint, and these tests pin what the agent SEES when it
trips: a message that names the problem and the remedy, not a traceback.
"""

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import (
    _guard_workspace_full,
    edit_file_impl,
    save_skill_impl,
    write_file_impl,
)
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec

# The guard is applied where tools are BUILT, so every tool that can reach the
# store is covered by one rule instead of each remembering to catch. Tests go
# through it for the same reason the SDK does.
write_file = _guard_workspace_full(write_file_impl)
edit_file = _guard_workspace_full(edit_file_impl)
save_skill = _guard_workspace_full(save_skill_impl)


def _ctx(quota: int) -> tuple[RunContextWrapper, WorkspaceFiles, str]:
    spec = make_spec(default_user="bob")
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using("bob"):
        iid = rm.create(RcaInvestigation(title="t", owner="bob", permission=None)).resource_id
    files = WorkspaceFiles(MemoryFileStore(), quota=quota)
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id=iid,
            files=files,
            spec=spec,
            app_slug="rca",
            acting_user="bob",
        )
    )
    return ctx, files, iid


async def test_write_file_over_quota_tells_the_agent_to_free_space():
    ctx, files, iid = _ctx(quota=1000)
    await files.write(iid, "/big.bin", b"x" * 900)

    out = await write_file(ctx, "/more.bin", "y" * 200)

    assert "workspace is full" in out.lower()
    assert "delete" in out.lower()
    assert await files.exists(iid, "/more.bin") is False


async def test_edit_file_over_quota_tells_the_agent_to_free_space():
    ctx, files, iid = _ctx(quota=1000)
    await files.write(iid, "/notes.md", b"seed")
    await files.write(iid, "/big.bin", b"x" * 990)

    out = await edit_file(ctx, "/notes.md", "seed", "seed" + "z" * 100)

    assert "workspace is full" in out.lower()
    assert await files.read(iid, "/notes.md") == b"seed"


async def test_an_edit_that_frees_space_still_goes_through_when_full():
    # The agent has to be able to act on the advice it is given.
    ctx, files, iid = _ctx(quota=1000)
    await files.write(iid, "/notes.md", b"keep" + b"z" * 996)

    out = await edit_file(ctx, "/notes.md", "z" * 996, "")

    assert out == "edited /notes.md"
    assert await files.read(iid, "/notes.md") == b"keep"


async def test_the_guard_covers_tools_beyond_write_file():
    # The point of guarding at the build site is that a tool doesn't have to
    # remember. `save_skill` never catches anything itself.
    ctx, files, iid = _ctx(quota=1000)
    await files.write(iid, "/big.bin", b"x" * 990)

    out = await save_skill(ctx, "note", "a description", "body " * 100)

    assert "workspace is full" in out.lower()
    assert "delete" in out.lower()


def test_the_guard_preserves_each_impl_s_calling_convention():
    # Not every tool impl is a coroutine — kb_search, lookup_glossary,
    # resolve_collection and the context-card pair are plain functions. An
    # `async def` wrapper around those would hand `function_tool` a coroutine
    # where it expects a value, breaking tools that never touch the workspace.
    # No test builds the tool list AND calls through it, so only the types said so.
    import inspect

    from workspace_app.agent.tools import _IMPLS

    sync_impls = [n for n, fn in _IMPLS.items() if not inspect.iscoroutinefunction(fn)]
    assert sync_impls, "expected some sync tool impls — this test guards their wrapping"
    for name in sync_impls:
        guarded = _guard_workspace_full(_IMPLS[name])
        assert not inspect.iscoroutinefunction(guarded), name
    for name, fn in _IMPLS.items():
        if inspect.iscoroutinefunction(fn):
            assert inspect.iscoroutinefunction(_guard_workspace_full(fn)), name


def test_a_guarded_sync_tool_still_returns_its_value():
    def probe(_ctx: object, query: str) -> str:
        return f"found {query}"

    assert _guard_workspace_full(probe)(None, "reflow") == "found reflow"
