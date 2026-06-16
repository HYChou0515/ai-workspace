"""``AgentConfig.suggestions`` shape: list[Suggestion(label, prompt)].

Quick-prompt chips need a separate UI label and the prompt that gets sent
when the chip is pressed (#91). The previous shape was ``list[str]`` —
both display and send were the same string, so a chip couldn't read as a
short word but submit a long instruction.
"""

from __future__ import annotations

from workspace_app.resources import AgentConfig
from workspace_app.resources.agent_config import Suggestion


def test_bare_agent_config_defaults_suggestions_to_empty_list():
    """A bare ``AgentConfig(name=...)`` has no chips."""
    cfg = AgentConfig(name="bare")
    assert cfg.suggestions == []


def test_suggestion_has_label_and_prompt_fields():
    """The Suggestion struct exposes the two fields the FE needs.

    ``label`` is what the chip button renders. ``prompt`` is what gets
    sent verbatim as the user message on click.
    """
    s = Suggestion(label="SPC", prompt="Show me the SPC analysis with control charts.")
    assert s.label == "SPC"
    assert s.prompt == "Show me the SPC analysis with control charts."


def test_agent_config_accepts_list_of_suggestion_objects():
    """``AgentConfig.suggestions`` is typed as ``list[Suggestion]`` and
    accepts struct instances directly. Round-trips without coercion."""
    chips = [
        Suggestion(label="SPC", prompt="Show me the SPC analysis."),
        Suggestion(label="Pareto", prompt="Run a Pareto of defect modes."),
    ]
    cfg = AgentConfig(name="rca", suggestions=chips)
    assert cfg.suggestions == chips
    assert cfg.suggestions[0].label == "SPC"
    assert cfg.suggestions[1].prompt == "Run a Pareto of defect modes."
