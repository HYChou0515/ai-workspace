"""`list_files` is `ls`, not `find`: one directory level at a time.

Paths come back RELATIVE (#549) — the store key `/x` is the system root once
it reaches `exec`, and a listing is the strongest evidence the model has about
what a path here looks like.

A recursive listing has no ceiling that the agent can steer — the answer is
however many files the workspace happens to hold, and the base prompt tells
every agent to list the workspace before it does anything else.
"""

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, list_files_impl, write_file_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _ctx(exec_output_max_chars: int = 30_000) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=WorkspaceFiles(MemoryFileStore()),
            exec_output_max_chars=exec_output_max_chars,
        )
    )


async def test_lists_one_level_and_marks_directories():
    ctx = _ctx()
    for path in ("/a.txt", "/b.txt", "/sub/deep.txt", "/sub/nested/deeper.txt"):
        await write_file_impl(ctx, path, "x")

    out = await list_files_impl(ctx)

    # sub/ is one entry, not two files — and directories lead, because they are
    # what the agent needs to keep going when the listing is cut.
    assert out.splitlines() == ["sub/", "a.txt", "b.txt"]


async def test_descending_into_a_directory_lists_its_own_level():
    ctx = _ctx()
    for path in ("/a.txt", "/sub/deep.txt", "/sub/nested/deeper.txt"):
        await write_file_impl(ctx, path, "x")

    assert (await list_files_impl(ctx, "/sub")).splitlines() == ["sub/nested/", "sub/deep.txt"]
    assert (await list_files_impl(ctx, "/sub/nested")).splitlines() == ["sub/nested/deeper.txt"]


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
    ctx = _ctx(400)
    for i in range(500):
        await write_file_impl(ctx, f"/data/f{i:04d}.txt", "x")

    out = await list_files_impl(ctx, "/data")

    assert len(out) < 800
    assert "500" in out  # the true total, not just "truncated"
    lines = [ln for ln in out.splitlines() if ln.startswith("data/f")]
    assert 0 < len(lines) < 500
    assert lines == sorted(lines)  # a stable, resumable window — not a random sample


async def test_a_listing_within_budget_carries_no_notice():
    ctx = _ctx()
    await write_file_impl(ctx, "/only.txt", "x")

    assert await list_files_impl(ctx, "") == "only.txt"


async def test_directories_survive_the_cut_because_they_are_the_way_forward():
    """The cut says "list a sub-directory to see the rest" — so the cut must not
    be what removes the sub-directories. A file-heavy directory would otherwise
    become a navigation dead end."""
    ctx = _ctx(300)
    for i in range(200):
        await write_file_impl(ctx, f"/data/f{i:04d}.txt", "x")
    for name in ("alpha", "beta"):
        await write_file_impl(ctx, f"/data/{name}/inner.txt", "x")

    out = await list_files_impl(ctx, "/data")

    assert "data/alpha/" in out
    assert "data/beta/" in out


async def test_a_partial_name_still_lists_what_starts_with_it():
    """`prefix` meant "path prefix" before this tool listed one level. A model
    that passes half a name must not be told the workspace is empty."""
    ctx = _ctx()
    for i in range(3):
        await write_file_impl(ctx, f"/data/report{i}.txt", "x")
    await write_file_impl(ctx, "/data/other.txt", "x")

    out = await list_files_impl(ctx, "/data/report")

    assert out.splitlines() == ["data/report0.txt", "data/report1.txt", "data/report2.txt"]


async def test_dot_means_the_workspace_root_like_every_other_file_tool():
    ctx = _ctx()
    await write_file_impl(ctx, "/a.txt", "x")

    assert await list_files_impl(ctx, ".") == "a.txt"
    assert await list_files_impl(ctx, "./") == "a.txt"


async def test_pointing_at_a_file_answers_about_that_file():
    ctx = _ctx()
    await write_file_impl(ctx, "/a.txt", "x")

    assert await list_files_impl(ctx, "/a.txt") == "a.txt"


async def test_a_cut_listing_can_be_resumed_with_offset():
    ctx = _ctx(200)
    for i in range(100):
        await write_file_impl(ctx, f"/flat/f{i:03d}.txt", "x")

    first = await list_files_impl(ctx, "/flat")
    shown = [ln for ln in first.splitlines() if ln.startswith("flat/")]
    assert "offset" in first  # the notice names the way forward

    second = await list_files_impl(ctx, "/flat", offset=len(shown) + 1)
    assert second.splitlines()[0] == f"flat/f{len(shown):03d}.txt"


async def test_one_entry_too_long_for_the_budget_is_still_shown():
    """Returning a notice and no entry at all is strictly worse than one entry:
    the agent learns nothing and has nothing to narrow with."""
    ctx = _ctx(20)
    await write_file_impl(ctx, "/a-very-long-file-name-indeed.txt", "x")
    await write_file_impl(ctx, "/another-very-long-file-name.txt", "x")

    out = await list_files_impl(ctx)

    assert "a-very-long-file-name-indeed.txt" in out  # one entry, not just a notice
    assert "offset=2" in out  # and a way to reach the other one


async def test_no_path_the_listing_prints_ever_starts_with_a_slash():
    """The two rules have to hold at once — one level at a time (#552) AND the
    one path dialect that works in the shell too (#549). A listing that pages or
    truncates must not quietly reintroduce the `/foo` form on the way."""
    ctx = _ctx(200)
    for i in range(60):
        await write_file_impl(ctx, f"/deep/dir{i:02d}/f.txt", "x")

    for out in (
        await list_files_impl(ctx),
        await list_files_impl(ctx, "/deep"),
        await list_files_impl(ctx, "/deep", offset=10),
        await list_files_impl(ctx, "/nope"),
    ):
        assert not any(ln.startswith("/") for ln in out.splitlines()), out
