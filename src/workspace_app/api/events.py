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


@dataclass(frozen=True)
class RunCancelled:
    """User interrupted this turn — either by sending a new message or by
    hitting Stop. Terminal: the SSE stream closes after this event."""

    type: Literal["run_cancelled"] = "run_cancelled"


@dataclass(frozen=True)
class ToolCallParseError:
    """The model emitted a tool call we couldn't parse — typically the
    LiteLLM Ollama chunk_parser bug where multi-tool-call streaming
    concatenates arguments into invalid JSON. Non-terminal: a retry
    with feedback to the model follows."""

    hint: str
    call_id: str = ""
    raw: str = ""
    type: Literal["tool_call_parse_error"] = "tool_call_parse_error"


@dataclass(frozen=True)
class MaxTurnsExceeded:
    """The agent burned through its turn budget without converging.
    Terminal."""

    turns: int
    type: Literal["max_turns_exceeded"] = "max_turns_exceeded"


@dataclass(frozen=True)
class AgentMetrics:
    """Live token telemetry for the current turn. `phase` is:
      - "up":    the prompt is being sent (Claude-Code's ↑),
      - "down":  the completion is streaming back (↓), counts tick live,
      - "final": the turn ended; counts are the model's exact usage.
    Token counts during up/down are approximate (chars/4); final is exact
    when the provider reports usage."""

    phase: Literal["up", "down", "final"]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: int = 0
    type: Literal["agent_metrics"] = "agent_metrics"


AgentEvent = (
    MessageDelta
    | ToolStart
    | ToolEnd
    | RunDone
    | RunError
    | RunCancelled
    | ToolCallParseError
    | MaxTurnsExceeded
    | AgentMetrics
)


# ----- Cell execution events (notebook-side, separate stream from agent) -----


@dataclass(frozen=True)
class CellStream:
    """stdout/stderr chunk from a running cell."""

    stream: Literal["stdout", "stderr"]
    text: str
    type: Literal["cell_stream"] = "cell_stream"


@dataclass(frozen=True)
class CellDisplayData:
    """Rich output: text/plain, image/png (base64), text/html, etc."""

    data: dict[str, str]
    type: Literal["cell_display_data"] = "cell_display_data"


@dataclass(frozen=True)
class CellError:
    ename: str
    evalue: str
    traceback: list[str]
    type: Literal["cell_error"] = "cell_error"


@dataclass(frozen=True)
class CellDone:
    """Cell finished — terminal for the cell stream."""

    execution_count: int
    type: Literal["cell_done"] = "cell_done"


CellEvent = CellStream | CellDisplayData | CellError | CellDone


def to_sse(event: AgentEvent | CellEvent) -> str:
    """Serialize one event as an SSE 'data:' line (with trailing blank line)."""
    payload = json.dumps(asdict(event))
    return f"data: {payload}\n\n"
