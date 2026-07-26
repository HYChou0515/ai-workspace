"""#242 — the per-turn system note that tells the agent who it is replying to,
and how it composes with retry feedback into the system prompt."""

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.litellm_runner import _agent_for, _turn_instructions
from workspace_app.resources import AgentConfig
from workspace_app.users import User

_ALICE = User(id="u1", name="Alice Chen", section="Reflow", email="alice.chen@acme.test")


def test_turn_instructions_merges_speaker_note_then_feedback():
    out = _turn_instructions(
        AgentToolContext(speaker=_ALICE), "retry: send ONE tool_call per response"
    )
    assert out is not None
    assert "replying to Alice Chen (alice.chen) from Reflow." in out
    assert out.endswith("retry: send ONE tool_call per response")


def test_turn_instructions_speaker_only_when_no_feedback():
    out = _turn_instructions(AgentToolContext(speaker=_ALICE), None)
    assert out is not None
    assert "Alice Chen (alice.chen)" in out


def test_turn_instructions_feedback_only_without_a_speaker():
    assert _turn_instructions(AgentToolContext(), "retry hint") == "retry hint"


def test_turn_instructions_none_when_nothing_to_add():
    assert _turn_instructions(AgentToolContext(), None) is None


def test_turn_instructions_includes_the_entity_schema_note():
    out = _turn_instructions(AgentToolContext(entity_schema_note="## This workspace's record types"), None)
    assert out is not None
    assert "This workspace's record types" in out


def test_entity_schema_note_reaches_the_agent_system_prompt():
    note = _turn_instructions(
        AgentToolContext(entity_schema_note="- **issue** (issues/N.md): status (one of: open, done)"),
        None,
    )
    agent = _agent_for(
        AgentConfig(name="a", model="m", system_prompt="BASE PROMPT"),
        extra_instructions=note,
    )
    assert isinstance(agent.instructions, str)
    assert "BASE PROMPT" in agent.instructions
    assert "status (one of: open, done)" in agent.instructions


def test_speaker_note_reaches_the_agent_system_prompt():
    note = _turn_instructions(AgentToolContext(speaker=_ALICE), None)
    agent = _agent_for(
        AgentConfig(name="a", model="m", system_prompt="BASE PROMPT"),
        extra_instructions=note,
    )
    assert isinstance(agent.instructions, str)
    assert "BASE PROMPT" in agent.instructions
    assert "Alice Chen (alice.chen)" in agent.instructions
