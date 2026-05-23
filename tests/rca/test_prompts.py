from workspace_app.rca.agent import default_rca_agent_config
from workspace_app.rca.prompts import load_system_prompt


def test_load_system_prompt_returns_non_empty_markdown():
    text = load_system_prompt()
    assert text.strip()
    assert "# RCA Agent" in text


def test_prompt_teaches_file_conventions():
    """Spot-check that the prompt names the conventional paths so the
    agent knows the design's file schema (FE renderers depend on these)."""
    text = load_system_prompt()
    for marker in (
        "/brief.md",
        "/drift.ipynb",
        "/pareto.ipynb",
        "/fishbone.canvas",
        "/5-why.md",
        "/report.v",
        "/data/",
    ):
        assert marker in text, f"system prompt missing {marker!r}"


def test_prompt_teaches_one_tool_call_per_turn():
    """Workaround for the LiteLLM Ollama multi-tool-call bug. The agent
    is explicitly told to serialize."""
    text = load_system_prompt()
    assert "one tool call" in text.lower() or "One tool call" in text


def test_default_rca_agent_config_loads_prompt():
    cfg = default_rca_agent_config()
    assert cfg.name == "RCA Agent"
    assert "RCA Agent" in cfg.system_prompt
    assert "/brief.md" in cfg.system_prompt
    # Picks up the RCA-tuned AgentConfig defaults from §3.
    assert cfg.sandbox_image == "workspace-app/sandbox:py312-ds"
    assert cfg.idle_timeout_seconds == 28800
