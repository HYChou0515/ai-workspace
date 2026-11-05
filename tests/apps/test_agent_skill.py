"""The skills that teach publishing (`docs/plan-wui-publish-and-agent-hub.md`
W5/A10) teach what the code accepts: each yaml example they show is read by
the parser the platform runs — the oracle, not a second list kept alike by
hand."""

from __future__ import annotations

import re

import yaml

from workspace_app.api.publish import COPYING, SHARINGS
from workspace_app.apps.agent_spec import parse_agent_spec
from workspace_app.apps.shared_skills import SHARED_SKILLS, shared_skill_source


def _yaml_blocks(name: str) -> list[str]:
    src = shared_skill_source(name)
    assert src is not None
    return re.findall(r"```yaml\n(.*?)```", (src / "SKILL.md").read_text(), re.S)


def test_the_agent_skill_is_shipped():
    assert "agent" in SHARED_SKILLS
    src = shared_skill_source("agent")
    assert src is not None and (src / "SKILL.md").is_file()


def test_the_agent_file_the_skill_shows_is_an_agent_with_nothing_wrong():
    (example,) = [b for b in _yaml_blocks("agent") if "view: agent" in b]

    spec, problems = parse_agent_spec(example)

    assert problems == []
    assert spec is not None and spec.instructions.strip()
    assert spec.tools == ["read_file", "list_files", "ask_knowledge_base"]


def test_every_way_of_sharing_the_wui_skill_names_is_one_a_wui_takes():
    src = shared_skill_source("wui")
    assert src is not None
    text = (src / "SKILL.md").read_text()
    named = set(re.findall(r"^   - `(\w+)` —", text, re.M))

    assert named == SHARINGS["wui"]
    for block in _yaml_blocks("wui"):
        doc = yaml.safe_load(block) or {}
        publish = doc.get("publish") if isinstance(doc, dict) else None
        if publish:
            assert publish["sharing"] in SHARINGS["wui"]
            assert publish["sharing"] in COPYING or "reach" not in publish
