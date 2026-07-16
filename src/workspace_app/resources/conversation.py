from __future__ import annotations

from typing import Any

from msgspec import Struct, field


class MessageMetrics(Struct, frozen=True):
    """The turn's final token usage, persisted on the assistant message so a
    reloaded thread can still show the ↑prompt / ↓completion line (which is
    otherwise live-only / lost on refresh)."""

    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: int


class Citation(Struct):
    """A parsed ``[n]`` marker in an answer, resolved to its source. Lives on a
    persisted message — both `KbMessage` (direct KB chat) and the RCA `Message`
    produced by the `ask_knowledge_base` tool — so the FE can render reference
    cards under the answer/tool card. Retrieved chunks get MERGED, so chunk-level
    provenance is the SET of original chunk ids that composed the cited passage.

    Lives in `conversation.py` (not `kb.py`) so RCA's `Message` can carry it
    without circular import (kb.py already imports `MessageMetrics` from here).
    """

    marker: int  # the [n] in the answer
    collection_id: str
    document_id: str  # SourceDoc resource id (encoded natural key; see kb.doc_id)
    filename: str  # display name = basename(path)
    start: int  # merged span (min start) into canonical text
    end: int  # max end
    source_chunk_ids: list[str]  # original DocChunk ids merged
    snippet: str = ""
    # Issue #254: the cited passage's aggregated source location
    # (``{"page": [3, 4], "section": ["Ch.2 > 2.1"]}``) so the FE can render a
    # "p.3 §2.1" chip on the reference card. ``{}`` when the source had none.
    provenance: dict[str, Any] = field(default_factory=dict)


class WithheldSource(Struct):
    """A knowledge source (a collection) the user may SEE EXISTS (``read_meta``)
    but may NOT read (``read_content``), surfaced by the disclosure probe because
    it holds a competitive answer to the query. Persisted on the assistant message
    so the FE can render a "🔒 <name> — request access" chip instead of the system
    silently dropping the source (permission-disclosure).

    The withheld CONTENT never travels — only the collection's identity + owner,
    all of which a ``read_meta`` holder already sees. Lives here (not ``kb.py``) so
    both the RCA ``Message`` and the KB ``KbMessage`` can carry it, mirroring
    ``Citation``.
    """

    collection_id: str
    name: str
    owner: str  # created_by — the grant authority and the "request access" target


class Message(Struct):
    role: str
    """One of `user` / `assistant` / `tool` / `system` / `error`.
    `error` (issue #37) records a terminal turn failure so a reloaded
    thread still shows it — see `error_kind`."""

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

    error_kind: str | None = None
    """Only set when role=error (issue #37) — why the turn failed:
    `error` (system/model failure), `cancelled` (user interrupted),
    `max_turns` (hit the step cap). Drives whether the failure re-enters
    the next turn's LLM history (`api.turns.history_items`): `cancelled`
    is replayed as a system note, the rest are human-only diagnostics."""

    stopped_reason: str | None = None
    """#113: `repetition` when role=assistant was stopped mid-stream for a
    degenerate repetition loop and `content`/`reasoning` was truncated to before
    it. The FE renders a notice so a reloaded thread doesn't read the truncated
    answer as normal. None for an ordinary answer."""

    tool_args: dict[str, Any] | None = None
    """Only set when role=tool — the tool call's arguments (captured from the
    ToolStart), so a reloaded log shows the full call, not just its output."""

    created_at: int | None = None
    """Epoch milliseconds when the message was produced. Persisted so the agent
    log's timestamps survive a reload. None for messages created before this
    field existed (the FE then shows no time)."""

    metrics: MessageMetrics | None = None
    """Only set on assistant answers — the turn's final token usage, so the
    live ↑/↓ token line survives a reload. None for older / non-assistant."""

    mentions: list[str] = field(default_factory=list)
    """Only set when role=mention — the user ids summoned ("@ come look").
    A mention is a human-to-human event in the thread, NOT an agent turn."""

    citations: list[Citation] = field(default_factory=list)
    """Only set when role=tool AND tool_name="ask_knowledge_base" — the
    KB sub-agent's resolved [n] citations for this tool's answer. Empty for
    all other messages. Mirrors `KbMessage.citations`; lets the FE render
    the same reference cards in RCA chat that direct KB chat already shows."""

    withheld: list[WithheldSource] = field(default_factory=list)
    """Only set on the assistant answer — knowledge sources the disclosure probe
    found relevant but that the user may see-exist-but-not-read (read_meta without
    read_content). Bubbled up from the turn's ask_knowledge_base sub-agents. Empty
    for everything else. Mirrors `KbMessage.withheld` (permission-disclosure)."""

    tool_display: str = ""
    """Only set when role=tool and it differs from `content` (#62) — the FULL
    exec result with a successful command's stderr kept. `content` stays the
    cleaned, LLM-facing form (fed back to the model via history_items); the FE
    renders `tool_display` when present so the error the user saw stream live
    doesn't vanish from the reloaded card. "" ⇒ render `content`."""


class Conversation(Struct):
    item_id: str
    """Opaque, indexed handle to the owning item (any App's WorkItem
    `resource_id`; #89). NOT a typed specstar `Ref` — Conversation must serve
    every App's resource, and a `Ref` binds to a single model. Cleanup on item
    deletion is a per-App on-delete event_handler, not declarative cascade."""

    messages: list[Message] = field(default_factory=list)

    title: str = ""
    """Display title for the multi-chat list (manual §3). "" for the implicit
    default chat (the FE labels it); set when a chat is named or launched."""

    run_id: str | None = None
    """Set when this conversation is a *workflow chat* — a `WorkflowRun` (run_id)
    drives its turns (manual §3). None = a *free chat* (human-driven). The item's
    default chat is always a free chat; workflow chats are never the default."""

    created_ms: int | None = None
    """App-level birth stamp (epoch ms) — the stable creation order used to pick the
    default chat (the earliest-born free chat). Distinct from specstar's per-revision
    `created_time` (which advances on every update, so it can't order births). None on
    conversations written before multi-chat (manual §3) — they predate every stamped
    chat, so they remain the default."""
