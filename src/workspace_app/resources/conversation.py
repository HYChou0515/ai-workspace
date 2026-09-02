from __future__ import annotations

from typing import Any

from msgspec import Struct, field


class MessageMetrics(Struct, frozen=True):
    """How this reply was produced, persisted on the assistant message so a
    reloaded thread can still show it (the stream is live-only).

    #748: every field is independently absent-able, because they are measured
    by different things that fail independently. `None` means "not measured" —
    never zero, and never a stand-in figure. The turn used to persist a chars/4
    estimate here whenever the provider stayed quiet (local Ollama routinely
    reports 0), with nothing marking it as a guess; any later comparison of
    models, cost or anomalies was reading invented numbers and could not tell.
    """

    model: str | None = None
    """The model that produced THIS message's text — under failover, the endpoint
    that actually served, not the configured head of the chain. A turn can span
    several round trips and switch between them; this names whoever wrote THIS
    message's text.

    Only the turn's final answer carries it: a tool call ends an assistant
    message and starts a new one, and the model is known only on the `final`
    event, which lands on the last. The intermediate bubbles of an agentic turn
    therefore show a time and nothing else — sparse, but not wrong. Stamping the
    last model onto all of them would be a guess in exactly the case this field
    exists to stop guessing about: a mid-turn failover. That a switch happened at all is the
    `FailoverSwitch` event's job, not this field's. None for messages written
    before the field existed."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    """What to STEER by. Approximate when the provider stayed quiet — the turn
    substitutes its own estimate — so never read these as a measurement; `exact`
    says which they are.

    #739's context gauge anchors on `prompt_tokens > 0` and deliberately prefers
    an estimate to no anchor: without one it falls back to a messages-only figure
    that cannot see the system prompt, the tool schemas or the skills index, which
    its own measurements put 5,800 tokens off on a 32k thread and which stopped
    compaction firing on a full window. #748 made these nullable and removed that
    anchor on every endpoint not vouched for — i.e. by default."""

    measured_prompt_tokens: int | None = None
    measured_completion_tokens: int | None = None
    """What was MEASURED. The provider's own counts, `None` where it gave none —
    never an estimate standing in for one.

    Two consumers with opposite requirements: a gauge wants a number even if
    approximate, a record must not carry a figure nobody can tell apart from a
    measurement. §2.8 split them on the event and then collapsed them again
    here, which is what broke the gauge."""
    elapsed_ms: int = 0
    """Wall clock for the whole turn — what the UI's "· 12.3s" shows. NOT the
    denominator for tok/s: see `generation_ms`. Keeping both in one field is the
    mistake #739 §1.3 records, where one number silently changed meaning."""

    generation_ms: int | None = None
    """Time the model spent GENERATING — first token to last, summed over the
    turn's round trips, excluding TTFT and the gaps where a tool was running.

    This is the denominator tok/s needs. Dividing by `elapsed_ms` instead
    measured the turn, not the model: one 60s tool call drags the figure down by
    an order of magnitude, and TTFT grows with the prompt, so the same model
    reads as slower the longer the conversation gets. None when no token ever
    arrived, or on the non-streaming path, which cannot see token timings."""

    exact: bool = False
    """#739: whether `prompt_tokens` above is the provider's own count rather
    than the turn's estimate — i.e. whether the gauge may anchor on it.

    Redundant with `measured_prompt_tokens is not None` and kept because #739's
    consumers read the flag. Derived at the single construction site
    (`turns._TurnReducer`), never set by hand, so the two cannot come to
    disagree about the same reply."""


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

    driven_by: str | None = None
    """#615: which DRIVER produced this message, when it was not a person —
    `"goal"` for an auto-continue round. `None` means a human sent it.

    A driver's message is persisted as `role="user"` and attributed to the goal's
    setter, because it must run with exactly their permissions; that makes it
    indistinguishable from a real one in the stored thread. Off-hours autonomy
    has to tell them apart — "has my owner said anything recently?" is what
    decides whether an unattended agent stands down — and sniffing a `[goal]`
    text prefix would hinge on a string a user can type themselves."""

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

    answers: str | None = None
    """The `tool_call_id` of the `ask_user` question this message answers, when
    the user replied by choosing an option rather than typing (grill-me).

    Without it the UI can only guess which question an answer belongs to by
    adjacency, which breaks the moment two questions are open, the user scrolls
    back to an earlier one, or a second tab answers first. It also lets an
    answered question retire its buttons instead of inviting a second answer.

    `None` for every ordinary message — this is opt-in, and a message that
    merely follows a question does not claim to answer it."""
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

    # #306 PR3: denormalized mirror of the owning item's read-visibility, so the
    # Conversation's own access_scope can gate reading the thread on the item's
    # `read_chat` WITHOUT a cross-resource join (the #303 SourceDoc pattern). The
    # thread is served by the Conversation auto-CRUD, which the item's own scope
    # never covers. Stamped at chat-create from the live item permission and
    # re-pushed when the item's permission / members change. Defaults keep a
    # pre-#306 (absent-cell) conversation PUBLIC via the scope's `isna()` clause.
    item_visibility: str = "public"
    item_read_chat: list[str] = field(default_factory=list)
    item_created_by: str = ""
