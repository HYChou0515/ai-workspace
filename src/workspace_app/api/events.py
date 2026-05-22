from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class MessageDelta:
    type: Literal["message_delta"] = "message_delta"
    text: str = ""


@dataclass(frozen=True)
class ToolStart:
    call_id: str
    name: str
    args: dict[str, object]
    type: Literal["tool_start"] = "tool_start"


@dataclass(frozen=True)
class ToolEnd:
    call_id: str
    output: str
    type: Literal["tool_end"] = "tool_end"


@dataclass(frozen=True)
class RunDone:
    type: Literal["done"] = "done"


@dataclass(frozen=True)
class RunError:
    message: str
    type: Literal["error"] = "error"


AgentEvent = MessageDelta | ToolStart | ToolEnd | RunDone | RunError


def to_sse(event: AgentEvent) -> str:
    """Serialize one event as an SSE 'data:' line (with trailing blank line)."""
    payload = json.dumps(asdict(event))
    return f"data: {payload}\n\n"
