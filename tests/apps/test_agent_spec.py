"""P5 of `docs/plan-wui-publish-and-agent-hub.md`: what an `agent.ai.yaml` says.

One file defines the whole agent (A4/A5): its name and look, its
`instructions:`, the tools it narrows to (never widens — that is applied at
the turn, `api/turn_context.py`), opening suggestions, and publish settings.
"""

from __future__ import annotations

from workspace_app.apps.agent_spec import (
    AgentSpec,
    agent_folder,
    overlay_destination,
    parse_agent_spec,
)

FULL = """\
view: agent
title: Sales helper
description: Answers questions about orders.
icon: chart
color: "#3B82F6"
instructions: |
  You help the sales team.
  Always answer in Chinese.
tools: [read_file, ask_knowledge_base]
suggestions:
  - Show this week's orders
  - label: Late shipments
    prompt: List every order shipped late this month.
publish:
  reach: everyone
"""


def test_reads_every_key_one_file_holds():
    spec, problems = parse_agent_spec(FULL)

    assert problems == []
    assert spec == AgentSpec(
        title="Sales helper",
        description="Answers questions about orders.",
        icon="chart",
        color="#3B82F6",
        instructions="You help the sales team.\nAlways answer in Chinese.\n",
        tools=["read_file", "ask_knowledge_base"],
        suggestions=[
            ("Show this week's orders", "Show this week's orders"),
            ("Late shipments", "List every order shipped late this month."),
        ],
        publish={"reach": "everyone"},
    )


def test_a_key_left_empty_is_as_if_absent():
    spec, problems = parse_agent_spec(
        "view: agent\ntitle: A\ninstructions: x\ndescription:\ntools:\npublish:\n"
    )

    assert problems == []
    assert spec == AgentSpec(title="A", instructions="x")


def test_no_tools_key_means_the_apps_tools_not_none_of_them():
    spec, _ = parse_agent_spec("view: agent\ntitle: A\ninstructions: Help.\n")

    assert spec is not None
    assert spec.tools is None


def test_an_empty_tools_list_is_an_agent_with_no_tools():
    spec, _ = parse_agent_spec("view: agent\ntitle: A\ninstructions: Help.\ntools: []\n")

    assert spec is not None
    assert spec.tools == []


def test_a_file_that_is_not_an_agent_has_no_spec_and_says_why():
    for text, said in (
        ("view: [", "yaml"),
        ("- a\n- b\n", "mapping"),
        ("view: wui\ntitle: A\ninstructions: x\n", "view: agent"),
        ("view: agent\ninstructions: Help.\n", "title"),
        ("view: agent\ntitle: A\n", "instructions"),
        ("view: agent\ntitle: A\ninstructions: '  '\n", "instructions"),
        ("view: agent\ntitle: A\ninstructions: x\ntools: read_file\n", "tools"),
        ("view: agent\ntitle: A\ninstructions: x\ntools: [1]\n", "tools"),
        ("view: agent\ntitle: 3\ninstructions: x\n", "title"),
        ("view: agent\ntitle: A\ninstructions: x\nsuggestions: hi\n", "suggestions"),
        ("view: agent\ntitle: A\ninstructions: x\nsuggestions: [{label: a}]\n", "suggestions"),
        ("view: agent\ntitle: A\ninstructions: x\npublish: everyone\n", "publish"),
        ("view: agent\ntitle: A\ninstructions: x\ndescription: [a]\n", "description"),
    ):
        spec, problems = parse_agent_spec(text)
        assert spec is None, text
        assert any(said in p for p in problems), (text, problems)


def test_an_unknown_key_is_named_but_does_not_unmake_the_agent():
    """A typo (`instruction:`) would otherwise be an agent silently running
    without what its author wrote — said, so the panel and the skill can show
    it; publishing refuses it (`api/publish.py`)."""
    spec, problems = parse_agent_spec("view: agent\ntitle: A\ninstructions: x\nmodel: gpt\n")

    assert spec is not None
    assert problems == [
        "unknown key `model` — an agent.ai.yaml holds: "
        + ", ".join(
            [
                "color",
                "description",
                "icon",
                "instructions",
                "publish",
                "suggestions",
                "title",
                "tools",
                "view",
            ]
        )
    ]


def test_the_folder_an_agent_owns():
    assert agent_folder("/agents/sales/agent.ai.yaml") == "/agents/sales"
    assert agent_folder("/agent.ai.yaml") == ""


def test_its_own_skills_and_workflows_land_where_the_root_s_are_in_a_copy():
    """A5: the agent's skills and workflows are the root's plus its folder's,
    its own winning on a clash — made true in the COPY by writing its folder's
    over the root's, so every reader of `.skill/` and `.workflows/` sees one
    ordinary workspace."""
    folder = "/agents/sales"

    assert (
        overlay_destination("/agents/sales/.skill/grill/SKILL.md", folder)
        == "/.skill/grill/SKILL.md"
    )
    assert (
        overlay_destination("/agents/sales/.skill/grill/scripts/a.py", folder)
        == "/.skill/grill/scripts/a.py"
    )
    assert (
        overlay_destination("/agents/sales/.workflows/weekly.json", folder)
        == "/.workflows/weekly.json"
    )
    # Not its skills or workflows: stays where it is and only there.
    for path in (
        "/agents/sales/agent.ai.yaml",
        "/agents/sales/.workflows/schedules.json",
        "/agents/sales/.workflows/nested/x.json",
        "/agents/sales/.skill/SKILL.md",
        "/agents/salesman/.skill/x/SKILL.md",
        "/.skill/x/SKILL.md",
    ):
        assert overlay_destination(path, folder) is None, path
    # An agent at the root has no folder of its own to overlay.
    assert overlay_destination("/.skill/x/SKILL.md", "") is None
