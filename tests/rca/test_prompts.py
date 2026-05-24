from workspace_app.rca.agent import default_rca_agent_config
from workspace_app.rca.prompts import load_system_prompt


def test_load_system_prompt_returns_non_empty_markdown():
    text = load_system_prompt()
    assert text.strip()
    assert "# RCA Agent" in text


def test_base_prompt_teaches_app_level_conventions_not_a_fixed_layout():
    """The base prompt carries only template-agnostic artifact conventions
    (FE renderers depend on these). Template-specific starting files live in
    each profile's `_prompt.md` appendix (see test_templates.py), so the base
    must NOT hardcode one template's layout."""
    text = load_system_prompt()
    for marker in ("/report.v", ".canvas", ".ipynb"):
        assert marker in text, f"base prompt missing convention {marker!r}"
    # Template-specific files must NOT be baked into the base prompt.
    for leaked in ("/brief.md", "/drift.ipynb", "/pareto.ipynb", "/data/"):
        assert leaked not in text, f"base prompt should not hardcode {leaked!r}"


def test_prompt_teaches_one_tool_call_per_turn():
    """Workaround for the LiteLLM Ollama multi-tool-call bug. The agent
    is explicitly told to serialize."""
    text = load_system_prompt()
    assert "one tool call" in text.lower() or "One tool call" in text


def test_default_rca_agent_config_loads_prompt():
    cfg = default_rca_agent_config()
    assert cfg.name == "RCA Agent"
    assert "RCA Agent" in cfg.system_prompt
    assert "/report.v" in cfg.system_prompt  # base carries the app conventions
    # Picks up the RCA-tuned AgentConfig defaults from §3.
    assert cfg.sandbox_image == "workspace-app/sandbox:py312-ds"
    assert cfg.idle_timeout_seconds == 28800
