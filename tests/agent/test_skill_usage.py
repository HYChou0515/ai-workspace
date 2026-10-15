"""Skill loads, counted like tool calls (docs/plan-tool-usage-stats.md §8).

A load is `read_skill` returning a body, or the chat send preloading an applied
skill (D9); only a body counts (D11). Each load is labelled with where the
skill came from (§8.2, D10) and counted against the turn's person, App, item
and surface, as a tool call is.
"""

from __future__ import annotations

from typing import Any

import msgspec
import pytest
from agents import RunContextWrapper

from tests.agent.test_read_skill_tool import (
    _profile_with_skill,
    _write,
    _ws_ctx,
    isolated_apps,  # noqa: F401 — the fixture
)
from workspace_app.agent.tool_usage import SEP, ToolUsageRecorder
from workspace_app.apps.skill_payload import SkillOrigin


def _rec() -> ToolUsageRecorder:
    return ToolUsageRecorder(day=lambda: "d")


def _counting(ctx: RunContextWrapper, rec: ToolUsageRecorder) -> RunContextWrapper:
    actx = ctx.context
    actx.tool_usage = rec
    actx.acting_user = "alice"
    actx.surface = "chat"
    return ctx


def _skills(rec: ToolUsageRecorder) -> dict[tuple[str, str], int]:
    return {(r.tool, r.source): r.calls for r in rec.drain() if r.kind == "skill"}


async def _ws_skill(ctx: RunContextWrapper, name: str, origin: SkillOrigin | bytes | None) -> None:
    await _write(ctx, f"/.skill/{name}/SKILL.md", f"---\nname: {name}\ndescription: d\n---\n\nB")
    if origin is not None:
        # The way installs and copies write it — the agent's own file tools
        # may not touch `.origin`.
        raw = origin if isinstance(origin, bytes) else msgspec.json.encode(origin)
        files = ctx.context.files
        assert files is not None
        await files.write("inv-1", f"/.skill/{name}/.origin", raw)


# ── where a skill came from (§8.2) ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("origin", "source"),
    [
        (None, "workspace"),  # written here, never published
        (SkillOrigin(source="shared", files={}), "shared"),  # a copy of a built-in
        (SkillOrigin(source="profile", files={}), "profile"),  # a copy of the App's
        (SkillOrigin(source="hub", files={}, entry="e1", commit="c"), "hub"),
        (SkillOrigin(source="hub", files={}, entry="e1", forked=True), "workspace"),  # a fork
        (b"{not json", "workspace"),  # an `.origin` that does not decode
    ],
)
async def test_a_workspace_skill_counts_under_where_its_copy_came_from(
    origin: Any,  # noqa: ANN401
    source: str,
) -> None:
    from workspace_app.agent.tools import read_skill_impl

    rec = _rec()
    ctx = _counting(_ws_ctx(), rec)
    await _ws_skill(ctx, "s", origin)

    out = await read_skill_impl(ctx, "s")

    assert out == "B"
    assert _skills(rec) == {("s", source): 1}


async def test_a_built_in_skill_counts_as_shared(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    import workspace_app.apps.shared_skills as shared
    from workspace_app.agent.tools import read_skill_impl

    d = tmp_path / "author-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: author-skill\ndescription: m\n---\n\nSHARED")
    monkeypatch.setattr(shared, "SHARED_SKILLS", {"author-skill": d})
    rec = _rec()
    ctx = _counting(_ws_ctx(), rec)

    await read_skill_impl(ctx, "author-skill")

    assert _skills(rec) == {("author-skill", "shared"): 1}


async def test_an_apps_skill_counts_as_profile(isolated_apps) -> None:  # noqa: ANN001, F811
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "default", "report-format", "fmt", "# Fmt")
    rec = _rec()
    ctx = _counting(_ws_ctx(), rec)

    await read_skill_impl(ctx, "report-format")

    assert _skills(rec) == {("report-format", "profile"): 1}


# ── only a body counts (D11) ────────────────────────────────────────────────


async def test_a_read_that_returns_no_body_counts_nothing(isolated_apps) -> None:  # noqa: ANN001, F811
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "default", "off", "x", "body")
    rec = _rec()
    ctx = _counting(_ws_ctx(), rec)
    ctx.context.skill_prefs = {"off": False}

    assert (await read_skill_impl(ctx, "ghost")).startswith("error")
    assert (await read_skill_impl(ctx, "off")).startswith("error")

    assert _skills(rec) == {}


async def test_a_turn_with_no_recorder_still_reads(isolated_apps) -> None:  # noqa: ANN001, F811
    from workspace_app.agent.tools import read_skill_impl

    _profile_with_skill(isolated_apps, "rca", "default", "f", "x", "# F")

    assert (await read_skill_impl(_ws_ctx(), "f")).startswith("# F")


# ── who and where: the turn's, as for a tool call ───────────────────────────


async def test_a_load_counts_against_the_turns_person_app_item_and_surface() -> None:
    from workspace_app.agent.tools import read_skill_impl

    rec = _rec()
    ctx = _counting(_ws_ctx(), rec)
    await _ws_skill(ctx, "s", None)

    await read_skill_impl(ctx, "s")

    [row] = rec.drain()
    assert (row.kind, row.fails, row.total_ms) == ("skill", 0, 0)
    assert list(row.by_user) == ["alice"]
    assert list(row.by_surface_app) == [f"chat{SEP}rca"]
    assert list(row.by_item) == ["inv-1"]


def test_a_skill_and_a_tool_of_the_same_name_are_two_rows() -> None:
    rec = _rec()
    rec.record(
        "x", "workspace", kind="skill", failed=False, ms=0, user="u", surface="c", app="", item=""
    )
    rec.record("x", "builtin", failed=False, ms=0, user="u", surface="c", app="", item="")

    assert sorted((r.kind, r.tool) for r in rec.drain()) == [("skill", "x"), ("tool", "x")]


# ── the other door: a skill the user applies (D9) ───────────────────────────


async def test_applying_a_skill_reports_each_loaded_skill_with_its_source() -> None:
    from workspace_app.apps.skills import build_applied_skills_block

    ctx = _ws_ctx()
    await _ws_skill(ctx, "mine", None)
    await _ws_skill(ctx, "got", SkillOrigin(source="hub", files={}, entry="e", commit="c"))
    loaded: list[tuple[str, str]] = []

    block = await build_applied_skills_block(
        ctx.context.files,
        "inv-1",
        "rca",
        "default",
        ["mine", "ghost", "got"],
        on_loaded=lambda name, source: loaded.append((name, source)),
    )

    assert "Apply these skills now" in block
    assert loaded == [("mine", "workspace"), ("got", "hub")]  # "ghost" loaded nothing
