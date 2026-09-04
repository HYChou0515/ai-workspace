"""Sub-agent definitions (`.agent/<name>/AGENT.md`) — the user-authored
counterpart to skills. A definition names a sub-agent, says when to use it,
narrows its tool set, and its body IS the sub-agent's system prompt.

Same dual source as skills: an App profile ships some, and the item's own
workspace may add or override them (read live — the file IDE can rewrite one
between turns).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from workspace_app.apps.subagents import (
    SUBAGENT_BODY_CAP,
    load_subagents,
    profile_subagent_defs,
    workspace_subagent_defs,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


def _files() -> tuple[WorkspaceFiles, str]:
    return WorkspaceFiles(MemoryFileStore()), "inv-1"


async def _put(files: WorkspaceFiles, inv: str, name: str, md: str) -> None:
    await files.write(inv, f"/.agent/{name}/AGENT.md", md.encode("utf-8"))


async def test_a_workspace_definition_becomes_a_subagent_with_its_tools_and_prompt():
    files, inv = _files()
    await _put(
        files,
        inv,
        "log-digger",
        "---\n"
        "name: log-digger\n"
        "description: Dig through big log files and report what broke.\n"
        "tools: [read_file, list_files, exec]\n"
        "---\n"
        "\n"
        "You read logs. Report the first real error, with the file and line.\n",
    )
    defs = await workspace_subagent_defs(files, inv)
    assert [d.name for d in defs] == ["log-digger"]
    only = defs[0]
    assert only.description == "Dig through big log files and report what broke."
    assert only.tools == ["read_file", "list_files", "exec"]
    assert only.body.startswith("You read logs.")


async def test_a_definition_cannot_grant_itself_a_tool_outside_the_ceiling():
    """The definition file is user-authored, so it is a request, not a grant —
    the App/profile tool ceiling is what actually decides (same rule
    `save_workflow` applies to an agent-written workflow)."""
    files, inv = _files()
    await _put(
        files,
        inv,
        "sneaky",
        "---\nname: sneaky\ndescription: d\ntools: [read_file, exec, delete_file]\n---\n\nbody\n",
    )
    defs = await workspace_subagent_defs(files, inv, ceiling=["read_file", "list_files"])
    assert defs[0].tools == ["read_file"]


async def test_one_bad_hand_edit_does_not_blank_the_whole_index():
    """The `.agent/` dir is hand-editable, so a half-saved file is normal. It is
    skipped; every other definition still loads."""
    files, inv = _files()
    await _put(files, inv, "good", "---\nname: good\ndescription: d\n---\n\nbody\n")
    await _put(files, inv, "broken", "---\nname: [unclosed\n---\n\nbody\n")
    await _put(files, inv, "nameless", "---\ndescription: no name here\n---\n\nbody\n")
    await _put(files, inv, "renamed", "---\nname: something-else\ndescription: d\n---\n\nbody\n")
    assert [d.name for d in await workspace_subagent_defs(files, inv)] == ["good"]


@pytest.fixture
def isolated_apps(tmp_path: Path, monkeypatch):
    """Synthetic apps package at ``tmp_path/agentpkg`` + monkeypatch
    ``apps.subagents._APPS_PKG`` at it, so no production App is read. Layout:
        agentpkg/<slug>/profiles/<profile>/.agent/<name>/AGENT.md
    """
    root = tmp_path / "agent_pkg_root"
    pkg = root / "agentpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    import workspace_app.apps.subagents as subagents

    importlib.reload(subagents)
    monkeypatch.setattr(subagents, "_APPS_PKG", "agentpkg")
    # The package read is cached (it is fixed for the process in production), so
    # clear it either side or one test's synthetic package answers the next.
    subagents._shipped_subagent_defs.cache_clear()
    yield pkg
    subagents._shipped_subagent_defs.cache_clear()
    sys.modules.pop("agentpkg", None)


def _ship(pkg: Path, slug: str, profile: str, name: str, md: str) -> None:
    d = pkg / slug / "profiles" / profile / ".agent" / name
    d.mkdir(parents=True)
    (d / "AGENT.md").write_text(md)


async def test_the_workspace_may_override_a_subagent_the_app_ships(isolated_apps):
    """An App ships useful defaults; the item's own workspace is where a user
    tailors one. Same name ⇒ the workspace copy is the one that runs, so
    tailoring never means losing the rest of the App's set."""
    _ship(
        isolated_apps,
        "rca",
        "default",
        "researcher",
        "---\nname: researcher\ndescription: shipped\n---\n\nshipped prompt\n",
    )
    _ship(
        isolated_apps,
        "rca",
        "default",
        "reporter",
        "---\nname: reporter\ndescription: shipped reporter\n---\n\nreport prompt\n",
    )
    files, inv = _files()
    await _put(
        files, inv, "researcher", "---\nname: researcher\ndescription: mine\n---\n\nmy prompt\n"
    )
    defs = await load_subagents(files, inv, "rca", "default")
    assert [d.name for d in defs] == ["reporter", "researcher"]
    mine = next(d for d in defs if d.name == "researcher")
    assert mine.description == "mine"
    assert mine.body.startswith("my prompt")


async def test_an_oversized_body_is_skipped_rather_than_becoming_a_system_prompt():
    """The body IS the sub-agent's system prompt, so an accidental paste of a
    whole log file would silently eat the turn's context. Skipped like any other
    malformed definition — the rest of the index survives."""
    files, inv = _files()
    await _put(files, inv, "good", "---\nname: good\ndescription: d\n---\n\nbody\n")
    huge = "x" * (SUBAGENT_BODY_CAP + 1)
    await _put(files, inv, "bloated", f"---\nname: bloated\ndescription: d\n---\n\n{huge}\n")
    assert [d.name for d in await workspace_subagent_defs(files, inv)] == ["good"]


def test_rca_ships_a_working_sub_agent_out_of_the_box():
    """A capability nobody can see is not delivered. An RCA item has someone to
    delegate to on day one, without the user writing a definition first."""
    defs = profile_subagent_defs("rca", "default")
    digger = next(d for d in defs if d.name == "log-digger")
    assert digger.description and digger.body
    # Every tool it asks for is one the App actually grants.
    assert set(digger.tools) <= set(
        json.loads((Path("src/workspace_app/apps/rca/app.json")).read_text())["agent"]["tools"]
    )


def test_a_tool_list_that_would_not_survive_the_file_is_refused():
    """`round_trip`'s third arm. The first two (unparseable, truncated
    description) had tests; this one had none and no caller in the repo, so the
    100% gate would have failed on it while the branch read as fully covered.

    A comma inside one entry renders as `[a,b]` and reads back as two tools, so
    the definition would silently hold something other than what was asked for —
    the exact class this whole check exists to make impossible."""
    from workspace_app.apps.subagents import round_trip

    checked = round_trip("s", "Digs logs", ["read_file,list_files"], "body")

    assert checked.reason is not None
    assert "not what you asked for" in checked.reason
    assert "read_file" in checked.reason


def test_an_unclamped_definition_is_still_a_fresh_object():
    """`_shipped_subagent_defs` is `@cache`d and `SubagentDef.tools` is a plain
    mutable list, so the unclamped path returning `defn` itself handed every
    later turn the cache's own object. One `.tools` mutation anywhere would then
    have edited a shipped definition process-wide.

    The probe is the mutation the comment describes, not the identity check: an
    identity assert would pass on a copy that still shared the list."""
    from workspace_app.apps.subagents import SubagentDef, clamp_tools

    shipped = SubagentDef(name="d", description="d", tools=["read_file"], body="b")

    handed_out = clamp_tools(shipped, None)
    handed_out.tools.append("exec")

    assert shipped.tools == ["read_file"]
    assert clamp_tools(shipped, None).tools == ["read_file"]


async def test_reading_the_definitions_costs_one_resolution_not_one_per_definition():
    """The sub-agent index is rebuilt EVERY turn (a definition the user just
    wrote has to be callable on the next one), so its cost is paid on every
    message — not only when somebody opens a panel.

    Reading each `AGENT.md` with its own call re-resolved where the workspace
    lives per file, which against the hosted sandbox is a network round trip
    apiece."""
    from tests.warm_workspace import warm_files
    from workspace_app.apps.subagents import workspace_subagent_defs

    async def probes_for(count: int) -> int:
        files, sb = await warm_files()
        for i in range(count):
            await files.write(
                "inv-1",
                f"/.agent/a{i}/AGENT.md",
                (
                    f"---\nname: a{i}\ndescription: does a{i} things.\n"
                    "tools: [read_file]\n---\n\nbody\n"
                ).encode(),
            )
        sb.liveness_probes = 0
        defs = await workspace_subagent_defs(files, "inv-1")
        assert len(defs) == count
        return sb.liveness_probes

    few, many = await probes_for(2), await probes_for(20)
    assert many == few, (
        f"{few} probes for 2 definitions but {many} for 20 — every turn pays for "
        "locating the workspace once per sub-agent the item declares"
    )


async def test_a_definition_that_vanishes_mid_listing_still_raises():
    """Batching the reads must not quietly change WHICH races this tolerates.

    The per-file loop did a bare `read`, so a definition deleted between the
    listing and the read raised out of here — and a batch that skipped it
    instead would silently hand the turn a smaller set of sub-agents than the
    item declares, which is worse than an error nobody can miss."""
    import pytest

    from workspace_app.filestore.protocol import FileNotFound

    class _GhostListing(WorkspaceFiles):
        async def ls(self, workspace_id: str, prefix: str = "") -> list[str]:
            got = await super().ls(workspace_id, prefix)
            return [*got, "/.agent/ghost/AGENT.md"]  # listed, never written

    files = _GhostListing(MemoryFileStore())
    await _put(files, "inv-1", "real", "---\nname: real\ndescription: d\n---\n\nbody\n")

    with pytest.raises(FileNotFound):
        await workspace_subagent_defs(files, "inv-1")
