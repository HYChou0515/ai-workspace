from specstar import SpecStar

from workspace_app.resources import (
    AgentConfig,
    Conversation,
    Investigation,
    Message,
    Severity,
    Status,
    register_all,
)


def test_register_all_makes_three_managers(spec_instance: SpecStar):
    register_all(spec_instance)
    for model in (Investigation, AgentConfig, Conversation):
        assert spec_instance.get_resource_manager(model) is not None


def test_investigation_crud_round_trip(spec_instance: SpecStar):
    register_all(spec_instance)
    rm = spec_instance.get_resource_manager(Investigation)
    rev = rm.create(Investigation(title="solder voids", owner="alice"))
    got = rm.get(rev.resource_id).data
    assert got.title == "solder voids"
    assert got.owner == "alice"
    assert got.severity is Severity.P2  # default
    assert got.status is Status.TRIAGING  # default
    assert got.topics == []
    assert got.members == []
    assert got.attached_agent_config_id is None


def test_investigation_severity_and_status_round_trip(spec_instance: SpecStar):
    register_all(spec_instance)
    rm = spec_instance.get_resource_manager(Investigation)
    rev = rm.create(
        Investigation(
            title="critical drift",
            owner="alice",
            severity=Severity.P0,
            status=Status.AWAITING_REVIEW,
            topics=["Reflow zone-3", "SMT placement"],
            product="MX-7 board",
            members=["bob", "carol"],
        )
    )
    got = rm.get(rev.resource_id).data
    assert got.severity is Severity.P0
    assert got.status is Status.AWAITING_REVIEW
    assert got.topics == ["Reflow zone-3", "SMT placement"]
    assert got.product == "MX-7 board"
    assert got.members == ["bob", "carol"]


def test_investigation_references_agent_config(spec_instance: SpecStar):
    register_all(spec_instance)
    inv_rm = spec_instance.get_resource_manager(Investigation)
    ac_rm = spec_instance.get_resource_manager(AgentConfig)
    ac_rev = ac_rm.create(AgentConfig(name="default"))
    inv_rev = inv_rm.create(
        Investigation(title="x", owner="alice", attached_agent_config_id=ac_rev.resource_id)
    )
    got = inv_rm.get(inv_rev.resource_id).data
    assert got.attached_agent_config_id == ac_rev.resource_id


def test_agent_config_defaults_match_rca_pivot(spec_instance: SpecStar):
    register_all(spec_instance)
    rm = spec_instance.get_resource_manager(AgentConfig)
    rev = rm.create(AgentConfig(name="default"))
    got = rm.get(rev.resource_id).data
    assert got.model == "ollama_chat/qwen3:14b"
    assert got.sandbox_image == "workspace-app/sandbox:py312-ds"
    assert got.idle_timeout_seconds == 28800  # 8 hours per Q10


def test_conversation_append_message_creates_revision(spec_instance: SpecStar):
    register_all(spec_instance)
    inv_rm = spec_instance.get_resource_manager(Investigation)
    conv_rm = spec_instance.get_resource_manager(Conversation)
    inv_rev = inv_rm.create(Investigation(title="x", owner="alice"))

    rev = conv_rm.create(Conversation(investigation_id=inv_rev.resource_id))
    conv = conv_rm.get(rev.resource_id).data
    conv.messages.append(Message(role="user", content="hi", author="alice"))
    conv_rm.update(rev.resource_id, conv)
    got = conv_rm.get(rev.resource_id).data
    assert len(got.messages) == 1
    assert got.messages[0].role == "user"
    assert got.messages[0].content == "hi"
    assert got.messages[0].author == "alice"


def test_message_supports_reasoning_field(spec_instance: SpecStar):
    """Message.reasoning carries LLM thinking content separately from
    the user-facing answer (Qwen3 / o-series style)."""
    register_all(spec_instance)
    inv_rm = spec_instance.get_resource_manager(Investigation)
    conv_rm = spec_instance.get_resource_manager(Conversation)
    inv_rev = inv_rm.create(Investigation(title="x", owner="alice"))
    rev = conv_rm.create(Conversation(investigation_id=inv_rev.resource_id))
    conv = conv_rm.get(rev.resource_id).data
    conv.messages.append(
        Message(
            role="assistant",
            content="The drift starts at 13:30.",
            reasoning="Looking at the SPC trace... zone-3 actual diverges from set-point.",
            author="RCA Agent",
        )
    )
    conv_rm.update(rev.resource_id, conv)
    got = conv_rm.get(rev.resource_id).data
    assert got.messages[0].reasoning is not None
    assert "diverges" in got.messages[0].reasoning
