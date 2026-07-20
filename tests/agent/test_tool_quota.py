"""#538 — the workspace quota reaches the agent's own file tools.

The quota used to be enforced only by the HTTP upload endpoint, so the one actor
most able to fill a workspace — the agent, writing files and downloading things
into the sandbox — was the one actor never checked. The gate now lives in the
`WorkspaceFiles` chokepoint, and these tests pin what the agent SEES when it
trips: a message that names the problem and the remedy, not a traceback.
"""

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import edit_file_impl, write_file_impl
from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec


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
            spec=spec,  # ty: ignore[invalid-argument-type]
            app_slug="rca",
            acting_user="bob",
        )
    )
    return ctx, files, iid


async def test_write_file_over_quota_tells_the_agent_to_free_space():
    ctx, files, iid = _ctx(quota=1000)
    await files.write(iid, "/big.bin", b"x" * 900)

    out = await write_file_impl(ctx, "/more.bin", "y" * 200)

    assert "workspace is full" in out.lower()
    assert "delete" in out.lower()
    assert await files.exists(iid, "/more.bin") is False


async def test_edit_file_over_quota_tells_the_agent_to_free_space():
    ctx, files, iid = _ctx(quota=1000)
    await files.write(iid, "/notes.md", b"seed")
    await files.write(iid, "/big.bin", b"x" * 990)

    out = await edit_file_impl(ctx, "/notes.md", "seed", "seed" + "z" * 100)

    assert "workspace is full" in out.lower()
    assert await files.read(iid, "/notes.md") == b"seed"


async def test_an_edit_that_frees_space_still_goes_through_when_full():
    # The agent has to be able to act on the advice it is given.
    ctx, files, iid = _ctx(quota=1000)
    await files.write(iid, "/notes.md", b"keep" + b"z" * 996)

    out = await edit_file_impl(ctx, "/notes.md", "z" * 996, "")

    assert out == "edited /notes.md"
    assert await files.read(iid, "/notes.md") == b"keep"
