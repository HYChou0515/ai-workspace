from msgspec import Struct, field


class AgentConfig(Struct):
    name: str
    model: str = "ollama_chat/qwen3:14b"
    system_prompt: str = ""
    suggestions: list[str] = field(default_factory=list)
    """Quick-prompt chips shown in the agent panel. Sent verbatim as the
    user prompt when clicked — so the prompt library lives with the agent
    config, not hardcoded in the FE."""

    allowed_tools: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    sandbox_image: str = "workspace-app/sandbox:py312-ds"
    """Default sandbox image built from `docker/Dockerfile.workspace`
    (plan-backend §7.5). Bumped from the prior workspace-app default of
    `python:3.12-slim` to one with ipykernel + numpy/pandas/matplotlib/scipy
    pre-installed."""

    idle_timeout_seconds: int = 28800
    """8 hours — per grill-me Q10 the RCA workflow expects long
    open-then-come-back sessions. Was 900 (15 min) for workspace-app."""
