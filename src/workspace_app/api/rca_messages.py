"""RCA conversation-message helpers (#54).

Pure translations between a finished turn's neutral ``TurnMessage`` stream and the
persisted RCA ``Conversation`` model — used by the interactive send path and the
workflow node driver alike, so they live in one importable place rather than as
closures inside ``create_app``.
"""

from __future__ import annotations

import re

from ..resources import Message
from ..resources.kb import Citation
from .turns import TurnMessage

_MARKER_RE = re.compile(r"\[(\d+)\]")


def to_rca_message(m: TurnMessage) -> Message:
    """Map a turn's neutral output to the RCA Conversation model: assistant
    answers are authored by the agent + carry reasoning; tool messages keep the
    call's id/name/args."""
    if m.role == "assistant":
        return Message(
            role="assistant",
            content=m.content,
            author="RCA Agent",
            reasoning=m.reasoning,
            created_at=m.created_at,
            metrics=m.metrics,
            stopped_reason=m.stopped_reason,  # #113: repetition-stop notice survives reload
        )
    if m.role == "error":
        # Issue #37: a terminal failure, persisted so a reloaded thread
        # shows it. `error_kind` drives the next-turn history policy.
        return Message(
            role="error",
            content=m.content,
            error_kind=m.error_kind,
            created_at=m.created_at,
        )
    return Message(
        role="tool",
        content=m.content,
        tool_call_id=m.tool_call_id,
        tool_name=m.tool_name,
        tool_args=m.tool_args,
        tool_display=m.tool_display,
        created_at=m.created_at,
    )


def undo_cut_index(messages: list[Message], turns: int) -> int:
    """The index to truncate `messages` at to drop the last `turns` whole
    turns (issue #38). A turn is delimited by a `role="user"` prompt —
    everything after it (assistant / tool / error / mention) belongs to
    that turn until the next prompt. Returns 0 when undoing more turns
    than exist (clears the conversation)."""
    user_idxs = [i for i, m in enumerate(messages) if m.role == "user"]
    if turns >= len(user_idxs):
        return 0
    return user_idxs[-turns]


def bubble_kb_citations(content: str, seen_subagent: list[list[Citation]]) -> list[Citation]:
    """Pick KB citations to attach to an assistant message that follows
    one or more sub-agent calls (ask_knowledge_base / infer_modules /
    any future KB-citing tool) in the same turn. Two modes:

    - **Explicit quotes** — content has `[N]` markers. Each marker is
      matched to the corresponding citation from the calls SEEN SO FAR;
      most-recent call wins on collisions (two sub-agent calls both
      having `[1]` → the latest one's `[1]` is the live reference).
      Returns only the matched citations, in marker order.

    - **Implicit synthesis** — content has no `[N]` markers but a
      sub-agent did run. Common case: the agent forwards the KB result
      into a file (`write_file ./report.v1.md`) without re-quoting the
      markers in chat prose; without a fallback the chat would render
      the outer answer as citation-less even though every claim came
      from the KB. Returns the LATEST sub-agent call's citations
      (deduped by chunk).

    Empty when `seen_subagent` is empty — caller guards on that to
    avoid smearing arbitrary citations onto pre-sub-agent messages.
    """
    markers = {int(m.group(1)) for m in _MARKER_RE.finditer(content)}
    if not markers:
        # Implicit synthesis — latest call wins, dedupe by chunk.
        if not seen_subagent:
            return []
        seen: set[tuple[str, int]] = set()
        out: list[Citation] = []
        for c in seen_subagent[-1]:
            key = (c.document_id, c.start)
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        out.sort(key=lambda c: c.marker)
        return out
    picked: dict[int, Citation] = {}
    for call in reversed(seen_subagent):
        for c in call:
            if c.marker in markers and c.marker not in picked:
                picked[c.marker] = c
    return [picked[k] for k in sorted(picked)]
