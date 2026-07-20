"""A rejected write/edit echoes the file's current content so the agent can
retry — but the echo is as big as whatever the user happened to upload, and a
failed `old_string` match on a big file is an everyday event, not an edge case.
"""

from agents import RunContextWrapper

from workspace_app.agent import (
    AgentToolContext,
    edit_file_impl,
    write_file_impl,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _ctx(cap: int) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=WorkspaceFiles(MemoryFileStore()),
            exec_output_max_chars=cap,
        )
    )


async def test_a_rejected_edit_caps_the_echoed_content_and_says_it_is_partial():
    ctx = _ctx(300)
    big = "\n".join(f"line{i}" for i in range(2000))
    await write_file_impl(ctx, "/big.txt", big)

    out = await edit_file_impl(ctx, "/big.txt", "nowhere-in-the-file", "x")

    assert "could not apply the edit" in out
    assert len(out) < 300 + 400  # the 300-char budget plus the error line + marker
    assert "read_file" in out  # how to get the exact text to match on


async def test_a_rejected_create_caps_the_echoed_content():
    ctx = _ctx(300)
    big = "\n".join(f"line{i}" for i in range(2000))
    await write_file_impl(ctx, "/big.txt", big)

    out = await write_file_impl(ctx, "/big.txt", "replacement")

    assert "already exists" in out
    assert len(out) < 300 + 400
    assert "read_file" in out


async def test_a_small_file_is_still_echoed_whole_so_the_retry_can_match_it():
    ctx = _ctx(30_000)
    await write_file_impl(ctx, "/small.txt", "alpha\nbeta\n")

    out = await edit_file_impl(ctx, "/small.txt", "gamma", "delta")

    assert "alpha\nbeta\n" in out
    assert "omitted" not in out


async def test_the_truncation_hint_names_the_file_the_way_the_agent_must_type_it():
    """The hint tells the agent to re-read the file, so the path in it is a path
    the agent will use — and every path an agent sees is relative (#549)."""
    ctx = _ctx(300)
    await write_file_impl(ctx, "/data/big.txt", "\n".join(f"line{i}" for i in range(2000)))

    out = await edit_file_impl(ctx, "/data/big.txt", "nowhere", "x")

    assert "read_file data/big.txt" in out
    assert "read_file /data/big.txt" not in out
