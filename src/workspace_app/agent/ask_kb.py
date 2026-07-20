"""#506: the configurable `ask_knowledge_base` backbone.

`ask_knowledge_base` spawns a KB sub-agent to answer a question against the
in-house docs, keeping the noisy retrieval in a throwaway context (#270). This
module makes that sub-agent *configurable* without a GoF class factory — the
variation is all DATA, not algorithm:

- `AskKbSpec` is the budget-only knob set a caller tunes (how many chunk / wiki
  searches, whether to consult the glossary, the sub-agent's prompt + collection
  scope). `wiki_mode` folds into `wiki_search_max` (0 = off) + the prompt, so
  wiki and chunk search take the exact same shape — one budget each.
- `build_ask_kb_context` is the builder: given a spec + the caller's base
  context, it stamps the spec's budgets + scope onto a sub-agent context.
  `allowed_tools()` derives which tools that sub-agent gets.

The card drafter (#506 P5) is the first consumer; a thin
`make_ask_knowledge_base(spec)` closure over the two is the only "factory" needed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .context import KbSearchBudget, WikiSearchBudget

if TYPE_CHECKING:
    from ..resources.kb import Citation
    from .context import AgentToolContext

# The shape `ask_knowledge_base_impl` calls `ctx.run_subagent` with, and the shape
# a `SubagentBridge.run`-like callable satisfies: `(purpose, payload, emit,
# origin_id, scope, **spec_knobs) -> (answer, citations)`. Kept `Any`-loose
# because the bridge takes several keyword-only extras (budget, wiki_budget, …).
RunSubagent = Callable[..., Awaitable["tuple[str, list[Citation]]"]]


@dataclass(frozen=True)
class AskKbSpec:
    """One configured shape of `ask_knowledge_base`. All fields are data — a caller
    tunes them; the algorithm (spawn KB sub-agent → search → synthesize) is fixed.

    - `kb_search_max` / `wiki_search_max`: the per-call caps, and the ONLY switch
      for each source. `0` ⇒ that tool isn't granted (off); `N` ⇒ granted, capped
      at N; `None` ⇒ unlimited. Independent (#537).
    - `glossary`: grant the cheap, deterministic `lookup_glossary` (no budget).
    - `prompt`: override the sub-agent's instruction (e.g. "force a wiki check").
    - `scope`: the collection ids to search; `None` ⇒ inherit the caller's scope.
    - `sub_agent_purpose`: which agent preset the sub-agent runs as.
    """

    kb_search_max: int | None = 3
    wiki_search_max: int | None = 3
    glossary: bool = True
    prompt: str | None = None
    scope: list[str] | None = None
    sub_agent_purpose: str = "kb_chat"

    def allowed_tools(self) -> list[str]:
        """The tools this spec grants its sub-agent.

        #537: the two searches are SYMMETRIC — each is granted unless its budget
        is off (`max == 0`), so "the wiki but not the documents" and "the
        documents but not the wiki" are both expressible. `kb_search` used to be
        unconditional ("a KB agent must be able to search"), which made the wiki
        knob the only real one and welded document search to every consultation.
        The wiki is reached through the delegating `ask_wiki`, never the raw grep
        (#270's A/B convention). `lookup_glossary` is free and rides along."""
        tools = []
        if self.kb_search_max != 0:
            tools.append("kb_search")
        if self.wiki_search_max != 0:
            tools.append("ask_wiki")
        if self.glossary:
            tools.append("lookup_glossary")
        return tools


def build_ask_kb_context(spec: AskKbSpec, base: AgentToolContext) -> AgentToolContext:
    """Stamp `spec`'s budgets + scope onto a copy of the caller's `base` context —
    the sub-agent's context. Budgets are freshly minted per build (each sub-agent
    invocation gets its own allotment); `scope=None` inherits the caller's
    collections. The tool set comes from `spec.allowed_tools()`, applied where the
    sub-agent's `AgentConfig` is assembled."""
    return replace(
        base,
        collection_ids=list(spec.scope) if spec.scope is not None else base.collection_ids,
        kb_search_budget=KbSearchBudget(max_calls=spec.kb_search_max),
        wiki_search_budget=WikiSearchBudget(max_calls=spec.wiki_search_max),
    )


def make_ask_knowledge_base(spec: AskKbSpec, bridge_run: RunSubagent) -> RunSubagent:
    """The factory's product: pre-bind `spec` into a `run_subagent`-shaped callable
    over a `SubagentBridge.run`-like `bridge_run`. Wire the result as a context's
    `run_subagent` and its `ask_knowledge_base` tool delegates to a KB sub-agent
    configured by `spec` — a capped chunk + wiki search over the spec's collection
    scope, with the glossary, in an isolated sub-context (#270).

    This is the ONE seam the card drafter (#506 P5) and the interactive KB agent
    (Task #1) share: both delegate through the same bridge; they differ ONLY in the
    `AskKbSpec` they pass here (the drafter forces `scope=[collection_id]` + tight
    budgets; the interactive agent inherits the caller's tier scope). The spec's
    budgets seed a FRESH allotment per call; `spec.scope` (when set) forces the
    collection, else the caller's `scope` argument (a resolved priority tier, #280)
    passes through. The whole spec rides along as `ask_kb_spec` so the bridge can
    derive the sub-agent's tool set (`spec.allowed_tools()`) and prompt."""

    async def run_subagent(
        purpose: str,
        payload: str,
        emit: Any = None,
        origin_id: str | None = None,
        scope: list[str] | None = None,
    ) -> tuple[str, list[Citation]]:
        return await bridge_run(
            spec.sub_agent_purpose,
            payload,
            emit,
            origin_id,
            collection_ids=list(spec.scope) if spec.scope is not None else scope,
            budget=KbSearchBudget(max_calls=spec.kb_search_max),
            wiki_budget=WikiSearchBudget(max_calls=spec.wiki_search_max),
            ask_kb_spec=spec,
        )

    return run_subagent
