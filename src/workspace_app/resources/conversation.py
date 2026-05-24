from __future__ import annotations

from typing import Annotated, Any

from msgspec import Struct, field
from specstar import OnDelete, Ref


class Message(Struct):
    role: str
    """One of `user` / `assistant` / `tool` / `system`."""

    content: str
    """User-facing message body. Excludes the model's chain-of-thought
    (see `reasoning`)."""

    author: str | None = None
    """User id when role=user; agent name when role=assistant.
    Forward-compatible with multi-user / multi-agent setups."""

    reasoning: str | None = None
    """LLM reasoning / thinking content. Qwen3 returns this as
    `thinking`; OpenAI o-series returns reasoning items. Split from
    `content` so the FE can render collapsed ("Show thinking")."""

    tool_call_id: str | None = None
    """Only set when role=tool — the call id this output responds to."""

    tool_name: str | None = None
    """Only set when role=tool — the tool that produced this output."""

    tool_args: dict[str, Any] | None = None
    """Only set when role=tool — the tool call's arguments (captured from the
    ToolStart), so a reloaded log shows the full call, not just its output."""

    created_at: int | None = None
    """Epoch milliseconds when the message was produced. Persisted so the agent
    log's timestamps survive a reload. None for messages created before this
    field existed (the FE then shows no time)."""


class Conversation(Struct):
    investigation_id: Annotated[str, Ref("investigation", on_delete=OnDelete.cascade)]
    """Ref to the parent investigation. Deleting the investigation
    cascades — the conversation goes with it."""

    messages: list[Message] = field(default_factory=list)
