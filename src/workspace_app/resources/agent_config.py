from msgspec import Struct, field


class AgentConfig(Struct):
    name: str
    model: str = "ollama/qwen2.5-coder:7b-instruct"
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    sandbox_image: str = "python:3.12-slim"
    idle_timeout_seconds: int = 900
