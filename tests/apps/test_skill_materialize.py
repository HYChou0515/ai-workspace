"""#589 — a baked-in skill's files land in the workspace when the skill is used.

Delivering a skill's body to the model is the moment its instructions start
referring to `scripts/summarise.py`, so it is the moment those files have to
exist. Before this, they never existed at all: committing one next to SKILL.md
was a no-op with no error and no log.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import workspace_app.apps.skills as skills_mod
from workspace_app.apps.skills import resolve_skill_body
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


@pytest.fixture
def isolated_apps(tmp_path: Path, monkeypatch):
    """A throwaway apps package so a profile can ship whatever skill we need."""
    root = tmp_path / "apps_root"
    pkg = root / "tplpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    importlib.reload(skills_mod)
    monkeypatch.setattr(skills_mod, "_APPS_PKG", "tplpkg")
    skills_mod.list_skills.cache_clear()
    skills_mod.load_skill.cache_clear()
    yield pkg
    skills_mod.list_skills.cache_clear()
    skills_mod.load_skill.cache_clear()
    sys.modules.pop("tplpkg", None)


def _profile_skill(root: Path, name: str, *, files: dict[str, str]) -> Path:
    sd = root / "rca" / "profiles" / "local-lab" / ".skill" / name
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nrun scripts/x.py")
    for rel, text in files.items():
        target = sd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return sd


async def test_using_a_skill_puts_its_scripts_in_the_workspace(isolated_apps: Path):
    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('hi')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"

    body = await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    assert body is not None and "run scripts/x.py" in body
    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"print('hi')\n"


# The reason the files go into the workspace at all is that the AI is meant to
# tweak them — that is what separates a skill from a tool. So using the skill
# again must never restore the shipped bytes over the edited ones. Refreshing a
# copy is a separate, explicit action, never a side effect of use.
async def test_never_overwrites_an_edited_copy(isolated_apps: Path):
    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('shipped')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    await files.write(inv, "/.skill/triage/scripts/x.py", b"print('the AI improved this')\n")

    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")

    kept = await files.read(inv, "/.skill/triage/scripts/x.py")
    assert kept == b"print('the AI improved this')\n"


# Copying a skill has real consequences: the copy shadows the package version, so
# its body stops tracking upstream and it starts reporting as a workspace skill.
# For a skill that is nothing BUT its SKILL.md, that is all cost and no benefit —
# the copy would be byte-identical to what the package already serves. Every
# skill shipped today is exactly that, so the default has to be "leave it alone".
async def test_a_skill_with_no_files_of_its_own_is_left_in_the_package(isolated_apps: Path):
    _profile_skill(isolated_apps, "plain", files={})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"

    body = await resolve_skill_body(files, inv, "rca", "local-lab", "plain")

    assert body is not None
    assert await files.ls(inv, "/.skill/plain/") == []


# The copy has to remember where it came from, or nothing can ever tell it apart
# from a skill the user wrote by hand — which is what decides whether a newer
# version exists upstream and whether a given file still holds the shipped bytes.
async def test_the_copy_records_where_it_came_from(isolated_apps: Path):
    import msgspec

    from workspace_app.apps.skill_payload import SkillOrigin

    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('hi')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"

    await resolve_skill_body(files, inv, "rca", "local-lab", "triage")
    origin = msgspec.json.decode(await files.read(inv, "/.skill/triage/.origin"), type=SkillOrigin)

    assert origin.source == "profile"
    assert sorted(origin.files) == ["SKILL.md", "scripts/x.py"]


# `read_skill` carries its own copy of the source precedence, so wiring only the
# apply path would leave the files missing exactly when the model went looking
# for them on its own — the commonest way a skill actually gets used.
async def test_the_read_skill_tool_materializes_too(isolated_apps: Path):
    from agents import RunContextWrapper

    from workspace_app.agent.context import AgentToolContext
    from workspace_app.agent.tools import read_skill_impl

    _profile_skill(isolated_apps, "triage", files={"scripts/x.py": "print('hi')\n"})
    files, inv = WorkspaceFiles(MemoryFileStore()), "inv-1"
    ctx = AgentToolContext(
        app_slug="rca", template_profile="local-lab", files=files, investigation_id=inv
    )

    await read_skill_impl(RunContextWrapper(ctx), "triage")

    assert await files.read(inv, "/.skill/triage/scripts/x.py") == b"print('hi')\n"
