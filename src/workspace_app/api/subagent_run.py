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

#: Never granted to a sub-agent: the tool that delegates, and the tool that
#: creates something to delegate to. A sub-agent answers once, from an empty
#: context — neither is anything it could finish using.
_NO_DELEGATION = frozenset({"run_agent", "save_subagent"})


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
    return answer


def _child_context(parent_ctx: AgentToolContext, defn: SubagentDef) -> AgentToolContext:
    parent_cfg = parent_ctx.agent_config
    if parent_cfg is None:  # pragma: no cover — the tool guards this first
        raise ValueError("run_agent_task needs the parent turn's AgentConfig")
    return dataclasses.replace(
        parent_ctx,
        agent_config=msgspec.structs.replace(
            parent_cfg,
            system_prompt=defn.body,
            # The delegation pair is stripped rather than merely unwired. Nulling
            # the seam stops recursion, but `build_tools` decides what to BUILD
            # from the names alone — so a definition naming `save_subagent` (and
            # all three apps let one) had `run_agent` built for the child, which
            # could then only ever refuse: the #537 shape, aimed at a sub-agent.
            # An adversarial review probe found this; the comment claiming "the
            # tool is simply not built" was describing an intention.
            allowed_tools=[t for t in defn.tools if t not in _NO_DELEGATION],
        ),
        history=[],
        run_agent=None,
        subagent_defs=(),
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
