"""Request / response bodies for the workspace API (#54).

The Pydantic models the hand-written workspace routes accept and return, gathered
out of ``create_app`` so each route module imports the shapes it needs from one
place. Pure data — no behaviour.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .kb_chat_routes import EnhancementsInput


class _MessageBody(BaseModel):
    content: str
    # Per-message reasoning effort from the UI selector; None → model default.
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Knowledge-search depth from the composer picker. Applies to this
    # turn's ask_knowledge_base lookups (the bridge forwards it to the
    # KB sub-agent); None → operator default.
    enhancements: EnhancementsInput | None = None


class _UndoOut(BaseModel):
    """Result of an undo: the conversation's new length + how many
    messages the undone turns removed."""

    message_count: int
    removed: int


class _FileEntry(BaseModel):
    """One workspace file in the listing (#205). ``read_only`` flags files the IDE
    must render non-editable — files under the reserved ``.readonly/`` directory,
    server-enforced (PUT is refused). A computed convention, so no per-file metadata."""

    path: str
    size: int
    read_only: bool


class _SkillEntry(BaseModel):
    """One co-created skill in a workspace (#298) — `.skill/<name>/SKILL.md`. The
    FE Skills panel lists these (the IDE tree hides the dot-folder) so the user can
    see, download, and reuse what they built with the agent."""

    name: str
    description: str


class _WorkspaceUsage(BaseModel):
    """A workspace's total storage usage vs its quota (#245), for the upload
    usage bar. ``used`` is the durable logical byte total; ``quota`` of 0 means
    no quota (the FE then hides the bar)."""

    used: int
    quota: int


class _CreateChatBody(BaseModel):
    # #topic-hub P7 (manual §3): open a new FREE chat in an item. Title is optional;
    # a workflow chat is opened by the run endpoint (P8), not here.
    title: str = ""


class _RenameChatBody(BaseModel):
    # #132: manual rename of a chat from the manage modal.
    title: str


class _ChatInfo(BaseModel):
    """One chat in an item's multi-chat list (manual §3)."""

    chat_id: str
    title: str
    run_id: str | None
    created_ms: int | None
    message_count: int
    is_default: bool
    name_hint: str = ""
    """First user message (whitespace-collapsed, truncated) so the FE can label an
    unnamed chat without fetching its thread (#132). "" until the first user turn."""
    status: str | None = None
    """The driving `WorkflowRun.status` for a workflow chat (running / awaiting_human /
    done / …), so the list shows a status badge without per-run polling (#132). None
    for a free chat."""
    last_activity_ms: int | None = None
    """Epoch ms of the chat's last write (specstar `updated_time`) — the recency sort
    key for the list (#132). None only if the revision time is unavailable."""


class _MentionBody(BaseModel):
    user_ids: list[str]
    note: str = ""


class _CellExecuteBody(BaseModel):
    code: str


class _ExecBody(BaseModel):
    cmd: list[str]


class _MoveBody(BaseModel):
    # `from` is a Python keyword — accept it on the wire via alias.
    from_: str = Field(alias="from")
    to: str


class _MkdirBody(BaseModel):
    path: str


class _SearchBody(BaseModel):
    query: str
    regex: bool = False
    caseSensitive: bool = False
    wholeWord: bool = False
    include: str = ""
    exclude: str = ""


class _ReplaceBody(_SearchBody):
    replacement: str = ""


class _CloseItemBody(BaseModel):
    # null → pure close; a string must be one of the App manifest's
    # `lifecycle.closing_states` (validated against the manifest, not here).
    status: str | None = None


class _DecisionBody(BaseModel):
    # #100: a human's answer at a workflow `human_gate` (manual §10). `choice` ∈
    # the gate's `allow` (e.g. approve/reject); `input` is an optional revision.
    choice: str
    input: str = ""


class _SteerBody(BaseModel):
    # #288 (manual §10): a free-text instruction to redirect a run. The read-only
    # steerer turns it into a reviewable plan (edit inputs + invalidate steps).
    instruction: str


class _SteerConfirmBody(BaseModel):
    # #288: the human's verdict on the proposed steer plan — apply + resume, or discard.
    approve: bool


class _SteerAck(BaseModel):
    # #288: the steer was accepted; the steerer runs in the background and, once it has a
    # plan, the run goes `awaiting_human` with `pending_steer` set (the FE refetches).
    run_id: str
    steering: bool = True


class _SteerConfirmOut(BaseModel):
    # #288: the plan was applied + the run resumed (approve=True), or discarded (False).
    run_id: str
    applied: bool


class _IngestBody(BaseModel):
    # #100: a deterministic node's ingest capability call (manual §8).
    collection: str
    path: str


class _CardBody(BaseModel):
    # topic-hub P9: a deterministic node's create-context-card capability call (§8).
    collection: str
    keys: list[str] = Field(default_factory=list)
    title: str = ""
    body: str = ""


class _PreflightCheckOut(BaseModel):
    # #283: one pre-flight checklist line in the launch dialog.
    label: str
    ok: bool
    severity: str  # "required" | "advisory"
    reason: str = ""


class _PhaseOut(BaseModel):
    id: str
    title: str = ""


class _PreflightPreviewOut(BaseModel):
    # #283: what the launch dialog renders BEFORE a run starts — the workflow's
    # identity + phases, plus (when the author wrote a ``preflight``) a human summary
    # of what the run will do and a checklist of its preconditions.
    workflow_id: str
    title: str
    description: str
    phases: list[_PhaseOut]
    summary: str
    checks: list[_PreflightCheckOut]
    can_run: bool
    has_preflight: bool
