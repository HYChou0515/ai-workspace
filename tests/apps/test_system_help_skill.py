"""docs/plan-ai-reads-docs.md P2 — the `system-help` skill: the entry through
which the in-app AI reads the platform's docs and plans.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import workspace_app.apps.shared_skills as shared
from workspace_app.apps.skills import _parse_frontmatter
from workspace_app.apps.subagents import SUBAGENT_FORBIDDEN_TOOLS, _def_from

from .test_shared_skills import BARE_SKILL_FILE_POINTER

APPS = Path(shared.__file__).resolve().parent
FOLDER = shared.SHARED_SKILLS_DIR / "system-help"


def test_it_is_a_readonly_shared_skill():
    assert shared.SHARED_SKILLS["system-help"] == FOLDER
    front, _body = _parse_frontmatter((FOLDER / "SKILL.md").read_bytes())
    assert front["name"] == "system-help"
    assert shared.shared_skill_readonly("system-help") is True


def _declared_skill_lists() -> dict[str, list[str]]:
    """Every skill list an item's agent can be given: each shipped app's
    `agent.skills`, and each profile that overrides it. Listed from the files,
    never by hand, so an app added later is checked too."""
    out: dict[str, list[str]] = {}
    for app_json in sorted(APPS.glob("*/app.json")):
        app = json.loads(app_json.read_text())
        out[app_json.parent.name] = list(app.get("agent", {}).get("skills", []))
        for prof in sorted((app_json.parent / "profiles").glob("*/_profile.json")):
            p = json.loads(prof.read_text())
            if "skills" in p:
                out[f"{app_json.parent.name}/{prof.parent.name}"] = list(p["skills"])
    return out


def test_every_shipped_app_and_profile_gives_it():
    lists = _declared_skill_lists()
    assert lists, "no app.json found"
    missing = sorted(where for where, skills in lists.items() if "system-help" not in skills)
    assert missing == []


def test_every_path_it_names_is_one_the_copy_holds():
    """`read_file` resolves from the workspace root, so every path the body names
    is the full `.skill/system-help/…` one, and each exists in the folder."""
    text = (FOLDER / "SKILL.md").read_text() + (
        FOLDER / "references" / "docs-reader.md"
    ).read_text()
    assert BARE_SKILL_FILE_POINTER.findall(text) == []
    named = set(re.findall(r"\.skill/system-help/([\w./-]+\.\w+)", text))
    assert named, "the body names no file"
    assert sorted(p for p in named if not (FOLDER / p).exists()) == []


def test_the_docs_reader_it_ships_is_a_sub_agent_save_subagent_accepts():
    """The skill ships `docs-reader` as an AGENT.md-shaped file the agent saves
    with `save_subagent` when `run_agent` does not list it. It must parse as a
    sub-agent and ask for no tool a sub-agent may never hold."""
    raw = (FOLDER / "references" / "docs-reader.md").read_bytes()
    defn = _def_from(raw, "docs-reader")
    assert defn is not None
    assert defn.name == "docs-reader"
    assert defn.tools
    assert sorted(set(defn.tools) & set(SUBAGENT_FORBIDDEN_TOOLS)) == []
