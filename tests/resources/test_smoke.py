from specstar import SpecStar

from workspace_app.apps.rca.model import RcaInvestigation, Severity, Status
from workspace_app.resources import (
    AgentConfig,
    Conversation,
    Message,
)


def test_make_spec_registers_two_core_managers(spec_instance: SpecStar):
    """`make_spec` is the only public spec-construction path; verify the
    core resource managers are present without callers needing to know
    about the internal `register_all` step. `AgentConfig` is deliberately
    NOT here — it's owned by the runner (Settings.agent_configs), not
    persisted as a specstar resource."""
    for model in (RcaInvestigation, Conversation):
        assert spec_instance.get_resource_manager(model) is not None


def test_item_crud_round_trip(spec_instance: SpecStar):
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    rev = rm.create(RcaInvestigation(title="solder voids", owner="alice"))
    got = rm.get(rev.resource_id).data
    assert got.title == "solder voids"
    assert got.owner == "alice"
    assert got.severity is Severity.P2  # default
    assert got.status is Status.TRIAGING  # default
    assert got.topics == []
    assert got.members == []
    assert got.profile == "default"  # WorkItemBase default
    assert got.attached_preset == ""  # no preset pinned


def test_item_severity_and_status_round_trip(spec_instance: SpecStar):
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    rev = rm.create(
        RcaInvestigation(
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


def test_item_references_preset_by_name(spec_instance: SpecStar):
    """`attached_preset` carries a preset NAME (config.yaml semantics). The
    field is a plain string — AppCatalog does the lookup; the spec just
    round-trips the value."""
    rm = spec_instance.get_resource_manager(RcaInvestigation)
    rev = rm.create(RcaInvestigation(title="x", owner="alice", attached_preset="claude-opus"))
    got = rm.get(rev.resource_id).data
    assert got.attached_preset == "claude-opus"


def test_agent_config_defaults_match_rca_pivot():
    """AgentConfig is now a plain msgspec.Struct (not a specstar resource);
    its defaults match the RCA deploy expectations so a bare
    `AgentConfig(name="…")` carries the right model + sandbox image."""
    cfg = AgentConfig(name="default")
    assert cfg.model == "ollama_chat/qwen3:14b"
    assert cfg.sandbox_image == "workspace-app/sandbox:py312-ds"
    assert cfg.idle_timeout_seconds == 28800  # 8 hours per Q10


def test_conversation_append_message_creates_revision(spec_instance: SpecStar):
    item_rm = spec_instance.get_resource_manager(RcaInvestigation)
    conv_rm = spec_instance.get_resource_manager(Conversation)
    item_rev = item_rm.create(RcaInvestigation(title="x", owner="alice"))

    rev = conv_rm.create(Conversation(item_id=item_rev.resource_id))
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
    item_rm = spec_instance.get_resource_manager(RcaInvestigation)
    conv_rm = spec_instance.get_resource_manager(Conversation)
    item_rev = item_rm.create(RcaInvestigation(title="x", owner="alice"))
    rev = conv_rm.create(Conversation(item_id=item_rev.resource_id))
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
