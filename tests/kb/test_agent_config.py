from workspace_app.kb.agent import default_kb_agent_config


def test_kb_agent_config_has_only_kb_search_and_a_prompt():
    config = default_kb_agent_config()
    # the KB agent must NOT get file/exec tools — only knowledge-base search
    assert config.allowed_tools == ["kb_search"]
    assert "knowledge base" in config.system_prompt.lower()
    assert "[n]" in config.system_prompt  # tells the model how to cite
