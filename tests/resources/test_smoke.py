from specstar import SpecStar

from workspace_app.resources import (
    AgentConfig,
    Conversation,
    Message,
    Workspace,
    register_all,
)


def test_register_all_makes_three_managers(spec_instance: SpecStar):
    register_all(spec_instance)
    for model in (Workspace, AgentConfig, Conversation):
        assert spec_instance.get_resource_manager(model) is not None


def test_workspace_crud_round_trip(spec_instance: SpecStar):
    register_all(spec_instance)
    rm = spec_instance.get_resource_manager(Workspace)
    rev = rm.create(Workspace(name="my ws"))
    got = rm.get(rev.resource_id).data
    assert got.name == "my ws"
    assert got.attached_agent_config_id is None


def test_workspace_references_agent_config(spec_instance: SpecStar):
    register_all(spec_instance)
    ws_rm = spec_instance.get_resource_manager(Workspace)
    ac_rm = spec_instance.get_resource_manager(AgentConfig)
    ac_rev = ac_rm.create(AgentConfig(name="default"))
    ws_rev = ws_rm.create(Workspace(name="ws", attached_agent_config_id=ac_rev.resource_id))
    got = ws_rm.get(ws_rev.resource_id).data
    assert got.attached_agent_config_id == ac_rev.resource_id


def test_agent_config_defaults_match_grill_me_decisions(spec_instance: SpecStar):
    register_all(spec_instance)
    rm = spec_instance.get_resource_manager(AgentConfig)
    rev = rm.create(AgentConfig(name="default"))
    got = rm.get(rev.resource_id).data
    assert got.model == "ollama/qwen2.5-coder:7b-instruct"
    assert got.sandbox_image == "python:3.12-slim"
    assert got.idle_timeout_seconds == 900


def test_conversation_append_message_creates_revision(spec_instance: SpecStar):
    register_all(spec_instance)
    rm = spec_instance.get_resource_manager(Conversation)
    rev = rm.create(Conversation(workspace_id="ws-xyz"))
    conv = rm.get(rev.resource_id).data
    conv.messages.append(Message(role="user", content="hi"))
    rm.update(rev.resource_id, conv)
    got = rm.get(rev.resource_id).data
    assert len(got.messages) == 1
    assert got.messages[0].role == "user"
    assert got.messages[0].content == "hi"
