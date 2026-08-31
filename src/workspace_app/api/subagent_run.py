"""Run one sub-agent turn to completion and return its report.

The generic counterpart to `SubagentBridge` / `answer_question`, which drive the
KB sub-agent. The difference that matters: the KB sub-agent's context is built
from scratch and deliberately drops the workspace (it only needs a retriever),
whereas a delegated sub-task is work *in this item* — so this builds the child
context by REPLACING a few fields on the parent's. Sandbox, files, filestore,
sync, app slug, acting user and the output ceilings all carry over untouched,
which is also why `tool_authz` keeps gating the sub-agent as the same speaker.

What changes: the definition's prompt and tool set, an empty history (the point
of delegating is that the noise stays out of both contexts), and the delegation
seam set to `None` so a sub-agent cannot spawn another one.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

import msgspec

from ..agent.context import AgentToolContext
from ..apps.subagents import SubagentDef
from .events import AgentEvent, MessageDelta, RunError
from .runner import AgentRunner

logger = logging.getLogger(__name__)

#: Tools a sub-agent may never hold, and why each one: a sub-agent answers ONCE,
#: from an empty context, to another agent rather than to a person.
#:
#: - `run_agent` / `save_subagent` — it has no seam to delegate through, and
#:   nothing it created could be used before it finishes.
#: - `update_todos` — a WHOLE-LIST replace on the parent conversation's pinned
#:   checklist. A sub-agent has no idea what is on it, so "add my step" is
#:   really "delete the user's plan", and the live event goes to the child's
#:   queue, so the panel changes with nothing in the stream explaining it.
#: - `ask_user` — it ends the turn to wait for a reply. In a sub-turn that means
#:   stopping with a question nobody is shown and no report for the caller.
#:
#: Enforced twice on purpose: subtracted from what `save_subagent` will grant
#: (so the agent is TOLD, per the refuse-don't-trim rule) and stripped again in
#: the child context (so a hand-written `.agent/` file cannot slip one past).
SUBAGENT_FORBIDDEN_TOOLS = frozenset({"run_agent", "save_subagent", "update_todos", "ask_user"})


async def run_agent_task(
    runner: AgentRunner,
    parent_ctx: AgentToolContext,
    defn: SubagentDef,
    prompt: str,
    *,
    on_event: Callable[[AgentEvent], None] | None = None,
) -> str:
    """Drive `defn`'s sub-agent over `prompt` and return what it finally said.

    `on_event` (when given) fires for every event as it happens, so the caller
    can relay the sub-agent's work into the parent turn's stream."""
    child = _child_context(parent_ctx, defn)
    parts: list[str] = []
    failure: str | None = None
    async for ev in runner.run(prompt, child):
        if on_event is not None:
            on_event(ev)
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            parts.append(ev.text)
        elif isinstance(ev, RunError):
            failure = ev.message
    answer = "".join(parts).strip()
    if failure is not None:
        # Told, not raised: one delegated step failing is something the main
        # agent can work around, whereas raising would end the whole turn.
        logger.warning("sub-agent %r failed: %s", defn.name, failure)
        return f"sub-agent {defn.name!r} failed: {failure}" + (
            f"\n\nIt had said, before failing:\n{answer}" if answer else ""
        )
    if not answer:
        # A sub-agent can end a turn without ever writing prose (it stopped on a
        # tool call, or burned its steps). Returning "" hands the caller a blank
        # tool result it cannot tell from a successful empty answer, so it says so.
        logger.warning("sub-agent %r produced no report", defn.name)
        return (
            f"sub-agent {defn.name!r} finished without writing a report — it may have "
            "run out of steps, or stopped on a tool call. Try a narrower task, or do "
            "this one yourself."
        )
    return answer


def _child_context(parent_ctx: AgentToolContext, defn: SubagentDef) -> AgentToolContext:
    """The parent's context with everything a sub-agent must not inherit removed.

    `dataclasses.replace` copies the reference for every field NOT named here,
    which is the trap this function exists to manage: the first version reset
    only `subagent_citations` and thereby let a sub-agent rewrite the user's todo
    list, chip the parent's answer with sources it never looked at, and re-send
    the parent's attached image on every delegation. So the rule, rather than a
    list of past bugs: a sub-agent inherits the WORKSPACE (files, sandbox,
    identity, ceilings — it is working in this item) and inherits NOTHING that
    belongs to the parent's conversation or accumulates across the parent's turn.
    Anything added to `AgentToolContext` in either of those two categories
    belongs here.
    """
    parent_cfg = parent_ctx.agent_config
    if parent_cfg is None:  # pragma: no cover — `_agent_for` needs a config too
        raise ValueError("run_agent_task needs the parent turn's AgentConfig")
    return dataclasses.replace(
        parent_ctx,
        agent_config=msgspec.structs.replace(
            parent_cfg,
            system_prompt=defn.body,
            # Stripped, not merely unwired: nulling a seam stops the tool WORKING,
            # but `build_tools` decides what to BUILD from names alone. A
            # hand-written definition naming one of these would otherwise be
            # handed a tool that can only refuse — the #537 shape, aimed at a
            # sub-agent. `save_subagent` refuses these up front; this is the
            # backstop for files it never saw.
            allowed_tools=[t for t in defn.tools if t not in SUBAGENT_FORBIDDEN_TOOLS],
        ),
        history=[],
        run_agent=None,
        subagent_defs=(),
        # Conversation-scoped channels. Nulled so that a tool which slipped
        # through anyway reports itself unavailable instead of acting on the
        # PARENT's conversation — the todo panel is the sharp case: whole-list
        # replace, and the live event would go to the child's queue, so the
        # user's checklist changes with nothing in the stream explaining it.
        conversation_id=None,
        on_todos_updated=None,
        # The parent turn's attached images. Sharing them contradicts this
        # feature's whole contract ("starts with an EMPTY context and cannot see
        # this conversation") and re-pays their token cost on every delegation.
        turn_image_urls=[],
        # Accumulators the parent's ASSISTANT MESSAGE is built from. Anything a
        # sub-agent appends here is attributed to the parent: withheld-source
        # chips for a lookup the user never saw it make, and the same for the
        # citation buckets below.
        withheld_collection_ids=[],
        kb_passages=[],
        injected_card_ids=set(),
        # A FRESH accumulator, not the parent's. Citation buckets are paired with
        # the PARENT's tool messages positionally (`bubble_kb_citations`,
        # most-recent-call-wins), so a sub-agent consulting the KB through the
        # shared dict would make the parent's answer cite a lookup the user never
        # saw it make.
        #
        # The cost, stated because it is easy to miss: this dict dies with the
        # child, so a sub-agent granted `ask_knowledge_base` returns prose whose
        # `[n]` markers no longer resolve to anything. Its report reads fine; the
        # numbers in it are inert. Fixing that means the sub-agent citing its
        # sources in words, which is the definition's job — not this seam's.
        subagent_citations={},
    )
