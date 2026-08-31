"""`save_subagent(name, description, tools, body)` — the deterministic AGENT.md
write.

The agent could always write `.agent/<name>/AGENT.md` with `write_file`, but a
hand-assembled file is silently skipped by the loader when the frontmatter is
malformed or `name` disagrees with the folder — and nothing tells the agent, so
it believes it saved something that does not exist. This tool owns the format
(and the path, and the slug) so that failure mode is unreachable: the agent
supplies fields, never syntax.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import save_subagent_impl
from workspace_app.apps.subagents import (
    SUBAGENT_BODY_CAP,
    SubagentDef,
    workspace_subagent_defs,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources.agent_config import AgentConfig


def _ctx() -> RunContextWrapper[AgentToolContext]:
    files = WorkspaceFiles(MemoryFileStore())
    return RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files))


async def _defs(ctx: RunContextWrapper[AgentToolContext]) -> list[SubagentDef]:
    """What the loader reads back for this ctx — the assert narrows the optional
    workspace for `ty` (the tests above always wire one)."""
    files = ctx.context.files
    assert files is not None
    return await workspace_subagent_defs(files, "inv-1")


async def test_what_it_saves_is_what_the_loader_reads_back():
    """The whole point of the tool: no round trip can be lost to formatting."""
    ctx = _ctx()

    out = await save_subagent_impl(
        ctx,
        "log-digger",
        "Digs through long logs and reports the first real error.",
        ["read_file", "list_files"],
        "You read logs. Report the first real error with file and line.",
    )

    assert "log-digger" in out
    defs = await _defs(ctx)
    assert [d.name for d in defs] == ["log-digger"]
    only = defs[0]
    assert only.description == "Digs through long logs and reports the first real error."
    assert only.tools == ["read_file", "list_files"]
    assert only.body.startswith("You read logs.")


async def test_asking_for_a_tool_the_turn_does_not_hold_is_refused_by_name():
    """Silently trimming would hand back a sub-agent that believes it holds
    `exec`; it then fails at its task in a way its caller cannot read. Being told
    now is what lets the agent pick another approach."""
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=files,
            agent_config=AgentConfig(name="main", allowed_tools=["read_file", "list_files"]),
        )
    )

    out = await save_subagent_impl(ctx, "digger", "d", ["read_file", "exec", "delete_file"], "b")

    assert "error" in out
    assert "exec" in out and "delete_file" in out  # says WHICH ones
    assert "read_file" in out  # ...and what it may use instead
    assert await workspace_subagent_defs(files, "inv-1") == []  # nothing written


async def test_a_sub_agent_with_no_instructions_is_refused():
    """The body IS the sub-agent's whole system prompt. Saving an empty one
    produces something that loads, appears in the delegation index, and then
    answers from nothing — the caller cannot tell that from a bad answer."""
    ctx = _ctx()

    out = await save_subagent_impl(ctx, "hollow", "d", ["read_file"], "   \n\n  ")

    assert "error" in out
    assert await _defs(ctx) == []


async def test_a_messy_display_name_still_loads_back_under_its_slug():
    """The guarantee in one test: whatever the agent types as a name, the file
    that lands has frontmatter `name` equal to its folder, so `_def_from` cannot
    skip it for a mismatch."""
    ctx = _ctx()
    out = await save_subagent_impl(ctx, "My Log Digger!", "d", [], "read the logs")
    assert "my-log-digger" in out
    defs = await _defs(ctx)
    assert [d.name for d in defs] == ["my-log-digger"]


async def test_a_multiline_description_is_collapsed_so_the_index_keeps_it():
    """The frontmatter parser is line-based — a newline would truncate the
    description, and the description is how the caller picks this sub-agent."""
    ctx = _ctx()
    await save_subagent_impl(ctx, "d1", "line one\nline two\n  line three", [], "body")
    defs = await _defs(ctx)
    assert [d.description for d in defs] == ["line one line two line three"]


async def test_instructions_over_the_cap_are_refused_and_nothing_is_written():
    ctx = _ctx()
    out = await save_subagent_impl(ctx, "huge", "d", [], "x" * (SUBAGENT_BODY_CAP + 1))
    assert "error" in out
    assert await _defs(ctx) == []


async def test_anything_the_loader_would_skip_is_refused_before_it_is_written():
    """The tool's promise is that what it saves is callable. Owning the file
    format is not enough — each of these wrote a file the loader then dropped,
    which is the exact failure this tool exists to make unreachable.

    (Found by an adversarial review probe, not by design. A character blacklist
    would have missed the cap case, so the check is the round trip itself.)
    """
    ctx = _ctx()
    cases = {
        "unclosed bracket": ("[WIP draft", ["read_file"], "body"),
        "unclosed brace": ("{draft", ["read_file"], "body"),
        # A `#` does not fail the parse — it silently truncates, which is worse.
        "hash truncates": ("finds the #1 error cause", ["read_file"], "body"),
        # render_agent_md ends the body with a newline, so exactly-at-cap crosses it.
        "body at the cap": ("d", [], "x" * SUBAGENT_BODY_CAP),
    }
    for label, (desc, tools, body) in cases.items():
        out = await save_subagent_impl(ctx, f"digger-{len(label)}", desc, tools, body)
        assert "error" in out, f"{label}: expected a refusal, got {out!r}"

    assert await _defs(ctx) == []  # and nothing was written


async def test_a_description_that_survives_the_round_trip_is_still_accepted():
    """Positive control for the refusal above — the check must not have become
    "refuse anything interesting"."""
    ctx = _ctx()
    out = await save_subagent_impl(
        ctx, "digger", "Finds the first real error (and says which line).", ["read_file"], "b"
    )
    assert "error" not in out
    [only] = await _defs(ctx)
    assert only.description == "Finds the first real error (and says which line)."


async def test_refining_a_sub_agent_takes_effect_in_the_same_reply():
    """`run_agent` re-reads the workspace when a name MISSES, which covers a new
    sub-agent — but re-saving an existing one is a hit, so the turn kept running
    the old body while this tool said "refine freely… callable now".

    Delegate → the report is poor → refine the body → delegate again is the
    obvious loop, and silently discarding the refinement is the worst way to lose
    it. Found by an adversarial review probe."""
    ctx = _ctx()
    stale = SubagentDef(name="digger", description="old", tools=[], body="OLD INSTRUCTIONS")
    ctx.context.subagent_defs = (stale,)

    await save_subagent_impl(ctx, "digger", "new", ["read_file"], "NEW INSTRUCTIONS")

    [live] = ctx.context.subagent_defs
    assert live.body.strip() == "NEW INSTRUCTIONS"
    assert live.tools == ["read_file"]


async def test_a_tool_a_sub_agent_could_never_hold_is_refused_not_quietly_dropped():
    """`update_todos` is a whole-list replace on the parent's pinned checklist and
    `ask_user` ends the turn for a reply nobody will see, so a sub-agent cannot
    hold either — nor the delegation pair. Accepting them and stripping them in
    the child would be the quiet trim this tool's own rule forbids, and the
    refusal's "Available:" line would have advertised them."""
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=files,
            agent_config=AgentConfig(
                name="main", allowed_tools=["read_file", "update_todos", "save_subagent"]
            ),
        )
    )

    out = await save_subagent_impl(ctx, "digger", "d", ["read_file", "update_todos"], "b")

    assert "error" in out and "update_todos" in out
    assert "update_todos" not in out.split("Available:")[-1]
    assert await workspace_subagent_defs(files, "inv-1") == []


async def test_resaving_the_same_name_overwrites_so_it_can_be_refined():
    ctx = _ctx()
    await save_subagent_impl(ctx, "digger", "d", [], "first")
    await save_subagent_impl(ctx, "digger", "d", ["read_file"], "second")
    [only] = await _defs(ctx)
    assert only.body.strip() == "second"
    assert only.tools == ["read_file"]


async def test_a_name_with_no_usable_characters_is_refused():
    ctx = _ctx()
    assert "error" in await save_subagent_impl(ctx, "!!!", "d", [], "body")


async def test_no_workspace_on_this_turn_says_so_instead_of_raising():
    ctx = RunContextWrapper(AgentToolContext())
    assert "error" in await save_subagent_impl(ctx, "digger", "d", [], "body")


async def test_the_confirmation_names_a_path_the_agent_can_actually_use():
    """Same trap `save_skill` documents (#549): the store key starts with `/`,
    but `exec` has no chroot, so echoing it teaches the agent a path that points
    at the system root."""
    ctx = _ctx()
    out = await save_subagent_impl(ctx, "digger", "d", [], "body")
    assert ".agent/digger/AGENT.md" in out
    assert "/.agent/digger/AGENT.md" not in out
