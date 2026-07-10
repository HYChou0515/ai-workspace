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

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .context import KbSearchBudget, WikiSearchBudget

if TYPE_CHECKING:
    from .context import AgentToolContext


@dataclass(frozen=True)
class AskKbSpec:
    """One configured shape of `ask_knowledge_base`. All fields are data — a caller
    tunes them; the algorithm (spawn KB sub-agent → search → synthesize) is fixed.

    - `kb_search_max` / `wiki_search_max`: the per-call search caps. `0` ⇒ that
      tool isn't granted (off); `N` ⇒ granted, capped at N; `None` ⇒ unlimited.
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
        """The tools this spec grants its sub-agent: `kb_search` always (a KB agent
        must be able to search), `search_wiki` unless wiki is off (`max == 0`), and
        the cheap `lookup_glossary` when enabled."""
        tools = ["kb_search"]
        if self.wiki_search_max != 0:
            tools.append("search_wiki")
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
