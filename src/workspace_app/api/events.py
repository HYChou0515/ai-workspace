from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal, get_args

# #100: workflow phase/step events live in the `workflow` package (so the step
# engine can emit them without importing the API layer) and are folded into the
# AgentEvent union below — they ride the same per-item broadcast stream (§12).
from ..workflow.events import (
    AwaitingHumanEvent,
    PhaseEntered,
    SteerProposed,
    StepFailed,
    StepOutput,
    StepPassed,
    StepRetrying,
    StepSkipped,
    StepStarted,
)


@dataclass(frozen=True)
class MessageDelta:
    type: Literal["message_delta"] = "message_delta"
    text: str = ""
    # When True the text is the model's reasoning (<think>) channel, not the
    # visible answer — the FE renders it collapsed.
    reasoning: bool = False


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
    # #62: the FULL display result (a successful command's stderr kept), when
    # it differs from `output` (the cleaned, LLM-facing result). "" ⇒ no
    # separate display; the FE renders `output`. The exec tools record it on
    # the context and the runner attaches it here keyed by `output`.
    display: str = ""
    type: Literal["tool_end"] = "tool_end"


@dataclass(frozen=True)
class ToolLog:
    """A chunk of stdout from a still-running tool (e.g. a long exec),
    streamed live. `call_id` may be empty — the FE then attaches it to the
    latest running tool call."""

    text: str
    call_id: str = ""
    type: Literal["tool_log"] = "tool_log"


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
class RepetitionStopped:
    """#113: the model degenerated into a repetition loop and we stopped the
    turn. The repeated text already streamed live (so the user sees the model
    misbehaved); the persisted message is truncated by `loop_length` trailing
    chars on `channel` ("content" or "reasoning"). A RunDone follows."""

    loop_length: int
    channel: Literal["content", "reasoning"] = "content"
    type: Literal["repetition_stopped"] = "repetition_stopped"


@dataclass(frozen=True)
class UserMessage:
    """#43: a human message posted to a SHARED investigation, broadcast on the
    per-investigation stream so every viewer sees who said what — live, before
    the agent turn it triggers. Only appears on the broadcast stream (never
    produced by the runner / per-requester KB stream)."""

    author: str
    content: str
    created_at: int = 0
    type: Literal["user_message"] = "user_message"


@dataclass(frozen=True)
class FileChanged:
    """#43: a workspace file changed (a human wrote / moved / deleted it),
    broadcast on the per-investigation stream so other viewers refetch. The
    model is last-write-wins; this is the 'someone else edited' signal."""

    path: str
    by: str
    kind: str  # written | moved | copied | deleted | dir_created
    type: Literal["file_changed"] = "file_changed"


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


@dataclass(frozen=True)
class FailoverSwitch:
    """#249/#131: the chat model was busy/blipped before its first token, so the
    turn switched to the next model in the preset's failover chain. Ephemeral —
    a live "the backend degraded gracefully, hang on" signal the FE shows as a
    transient status line; it is NOT persisted to the transcript. ``from_model``
    is the model that gave way (for logs/telemetry); the FE shows a de-jargoned
    notice and never the raw id."""

    from_model: str
    reason: str = ""
    type: Literal["failover_switch"] = "failover_switch"


@dataclass(frozen=True)
class RestoreProgress:
    """#492 P11: the item's sandbox was cold, so before the turn can run its
    durable snapshot is being restored file-by-file. `done`/`total` count files
    copied so the FE shows "還原中 N/M" instead of a blank running card while a
    slow cold wake completes. Ephemeral — a transient status line, never
    persisted; the turn proceeds normally once restore finishes. Only the
    app-side restore path emits these (host-managed rsync restore is fast)."""

    done: int
    total: int
    type: Literal["restore_progress"] = "restore_progress"


@dataclass(frozen=True)
class TodosUpdated:
    """#613: the agent rewrote this conversation's todo checklist (whole-list
    replace via the `update_todos` tool). `items` is the NEW full list in order,
    as plain `{"text": ..., "status": ...}` dicts (status ∈ pending /
    in_progress / completed) — JSON-native so the cross-pod bus roundtrip
    reconstructs it exactly. The FE swaps its pinned panel state wholesale.
    Ephemeral on the stream; the durable copy lives on `ConversationTodos`."""

    items: list[dict[str, str]]
    type: Literal["todos_updated"] = "todos_updated"


@dataclass(frozen=True)
class Presence:
    """#455: the live roster of an item's stream — the distinct users currently
    subscribed to its `/stream`. Broadcast whenever a viewer joins or leaves, so
    every open view shows who else is here (an avatar stack). Per-pod + ephemeral
    (a viewer on another pod isn't counted), consistent with the SSE broadcast."""

    users: list[str]
    type: Literal["presence"] = "presence"


AgentEvent = (
    MessageDelta
    | ToolStart
    | ToolEnd
    | ToolLog
    | RunDone
    | RunError
    | RunCancelled
    | ToolCallParseError
    | MaxTurnsExceeded
    | RepetitionStopped  # #113: model degenerated into a repetition loop
    | AgentMetrics
    | FailoverSwitch  # #249/#131: chat model switched mid-turn (ephemeral notice)
    | RestoreProgress  # #492 P11: cold-wake snapshot restore progress (ephemeral)
    | TodosUpdated  # #613: the agent rewrote the conversation's todo checklist
    | UserMessage  # #43: broadcast-only (a human's message on the shared stream)
    | FileChanged  # #43: broadcast-only (a workspace file changed)
    | Presence  # #455: broadcast-only (live viewer roster on the item stream)
    | PhaseEntered  # #100: workflow phase/step observability (manual §12)
    | StepStarted
    | StepOutput  # #178: live stdout from a running deterministic step
    | StepPassed
    | StepFailed
    | StepSkipped
    | StepRetrying
    | AwaitingHumanEvent
    | SteerProposed  # #288: a steer plan is ready for review
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


def to_sse(event: AgentEvent | CellEvent, seq: int | None = None) -> str:
    """Serialize one event as an SSE 'data:' line (with trailing blank line).

    `seq` (when given) is the per-session broadcast sequence number — injected
    into the JSON payload so a reconnecting client can resume with `?since=<seq>`
    (the replay buffer in `turns.py`). It is a transport concern, carried in the
    SSE JSON and never on the frozen event dataclasses."""
    data = asdict(event)
    if seq is not None:
        data["seq"] = seq
    return f"data: {json.dumps(data)}\n\n"


def _flatten_union(tp: Any) -> list[Any]:
    """Concrete members of a (possibly nested) type union, e.g. AgentEvent →
    [MessageDelta, ToolStart, …] with WorkflowEvent's members folded in."""
    args = get_args(tp)
    if not args:
        return [tp]
    out: list[Any] = []
    for a in args:
        out.extend(_flatten_union(a))
    return out


# `type` discriminator → dataclass, built from the union so a new event type is
# picked up automatically (no hand-maintained map to drift).
_EVENT_BY_TYPE: dict[str, Any] = {
    c.__dataclass_fields__["type"].default: c
    for c in _flatten_union(AgentEvent)
    if is_dataclass(c) and "type" in getattr(c, "__dataclass_fields__", {})
}


def event_from_dict(data: dict[str, Any]) -> AgentEvent:
    """Reconstruct an AgentEvent from its `asdict` form — the inverse of `to_sse`'s
    payload, for the cross-pod event bus (which ships events as JSON). Dispatches on
    the `type` discriminator; the transport-only `seq` key (and any unknown key) is
    dropped, since it is not a dataclass field."""
    cls = _EVENT_BY_TYPE[str(data["type"])]
    fields = cls.__dataclass_fields__
    return cls(**{k: v for k, v in data.items() if k in fields})
