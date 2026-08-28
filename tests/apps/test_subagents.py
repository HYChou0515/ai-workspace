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
    yield pkg
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
