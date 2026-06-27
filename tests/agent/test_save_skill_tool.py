"""#298 P2 — `save_skill(name, description, body)`: the deterministic SKILL.md
write. It owns frontmatter assembly + `name==dir` + path so a hand-written file
can't be silently dropped by the loader; the agent only supplies three fields.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import read_skill_impl, save_skill_impl
from workspace_app.apps.skills import SKILL_BODY_CAP
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _ctx():
    files = WorkspaceFiles(MemoryFileStore())
    return RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files))


async def test_save_then_read_round_trips():
    ctx = _ctx()
    out = await save_skill_impl(
        ctx, "smt-reflow", "How to triage reflow defects.", "# Steps\n\n1. x"
    )
    assert "smt-reflow" in out
    assert "read_skill" in out
    assert (await read_skill_impl(ctx, "smt-reflow")).strip() == "# Steps\n\n1. x"


async def test_name_is_slugified_and_matches_dir():
    """A messy display name becomes a kebab slug; the saved skill loads under
    that slug (frontmatter name == dir, so the loader never skips it)."""
    ctx = _ctx()
    out = await save_skill_impl(ctx, "My SMT Reflow!", "d", "body")
    assert "my-smt-reflow" in out
    assert (await read_skill_impl(ctx, "my-smt-reflow")).strip() == "body"


async def test_multiline_description_is_collapsed_to_one_line():
    """The frontmatter parser is line-based — a newline in the description would
    truncate it (or break parsing). save_skill collapses it so the skill still
    lists with its full description."""
    from workspace_app.apps.skills import workspace_skill_metas

    ctx = _ctx()
    await save_skill_impl(ctx, "s", "line one\nline two\n  line three", "body")
    metas = await workspace_skill_metas(ctx.context.files, ctx.context.investigation_id)
    assert [m.description for m in metas] == ["line one line two line three"]


async def test_resave_overwrites():
    ctx = _ctx()
    await save_skill_impl(ctx, "s", "d", "first")
    await save_skill_impl(ctx, "s", "d", "second")
    assert (await read_skill_impl(ctx, "s")).strip() == "second"


async def test_body_over_cap_rejected_and_not_written():
    ctx = _ctx()
    out = await save_skill_impl(ctx, "huge", "d", "x" * (SKILL_BODY_CAP + 1))
    assert "error" in out
    # nothing was written → the skill does not load
    assert "error" in await read_skill_impl(ctx, "huge")


async def test_name_with_no_usable_chars_rejected():
    ctx = _ctx()
    out = await save_skill_impl(ctx, "!!!", "d", "body")
    assert "error" in out


async def test_no_workspace_context_returns_friendly_error():
    ctx = RunContextWrapper(AgentToolContext())  # no files / investigation_id
    out = await save_skill_impl(ctx, "s", "d", "body")
    assert "error" in out
