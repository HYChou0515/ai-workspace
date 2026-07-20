"""`list_files` is `ls`, not `find`: one directory level at a time.

A recursive listing has no ceiling that the agent can steer — the answer is
however many files the workspace happens to hold, and the base prompt tells
every agent to list the workspace before it does anything else.
"""

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, list_files_impl, write_file_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _ctx(**kw: object) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(investigation_id="inv-1", files=WorkspaceFiles(MemoryFileStore()), **kw)
    )


async def test_lists_one_level_and_marks_directories():
    ctx = _ctx()
    for path in ("/a.txt", "/b.txt", "/sub/deep.txt", "/sub/nested/deeper.txt"):
        await write_file_impl(ctx, path, "x")

    out = await list_files_impl(ctx)

    assert out.splitlines() == ["/a.txt", "/b.txt", "/sub/"]  # sub/ is one entry, not two files


async def test_descending_into_a_directory_lists_its_own_level():
    ctx = _ctx()
    for path in ("/a.txt", "/sub/deep.txt", "/sub/nested/deeper.txt"):
        await write_file_impl(ctx, path, "x")

    assert (await list_files_impl(ctx, "/sub")).splitlines() == ["/sub/deep.txt", "/sub/nested/"]
    assert (await list_files_impl(ctx, "/sub/nested")).splitlines() == ["/sub/nested/deeper.txt"]


async def test_a_trailing_slash_and_a_bare_name_list_the_same_directory():
    ctx = _ctx()
    await write_file_impl(ctx, "/sub/deep.txt", "x")

    assert await list_files_impl(ctx, "/sub/") == await list_files_impl(ctx, "sub")


async def test_an_empty_directory_says_so_instead_of_answering_with_nothing():
    ctx = _ctx()
    assert "no files" in await list_files_impl(ctx, "/nope")


async def test_a_huge_directory_is_cut_to_the_budget_with_a_countable_notice():
    """One directory can still hold more entries than the context can take, so
    the listing itself is capped — and says what it held so the agent knows the
    listing is partial rather than believing it saw everything."""
    ctx = _ctx(exec_output_max_chars=400)
    for i in range(500):
        await write_file_impl(ctx, f"/data/f{i:04d}.txt", "x")

    out = await list_files_impl(ctx, "/data")

    assert len(out) < 800
    assert "500" in out  # the true total, not just "truncated"
    lines = [ln for ln in out.splitlines() if ln.startswith("/data/f")]
    assert 0 < len(lines) < 500
    assert lines == sorted(lines)  # a stable, resumable window — not a random sample


async def test_a_listing_within_budget_carries_no_notice():
    ctx = _ctx()
    await write_file_impl(ctx, "/only.txt", "x")

    assert await list_files_impl(ctx, "") == "/only.txt"
