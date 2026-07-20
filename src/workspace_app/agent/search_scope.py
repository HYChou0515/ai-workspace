"""#537 — which knowledge sources a turn may actually use.

The KB agent has three ways to find something out, and they are not
interchangeable:

- **the glossary** (`lookup_glossary`) — one exact-key lookup against curated
  cards. No model, no retrieval, one read. Effectively free.
- **the wiki** (`ask_wiki`) — a reader navigates the consolidated pages
  index-first and answers. Middling cost.
- **the documents** (`kb_search`) — embed the query, vector + BM25, optional
  rewriting and reranking. The expensive one.

A per-turn budget caps each of the latter two. `0` means that source is off for
this reply, and off has to mean the tool is NOT GRANTED — not granted-then-
refused. A granted-but-refusing tool costs a whole model round-trip to learn
what the prompt could have said for free, and the refusal text ("answer now
from what you have") reads as an instruction to stop searching altogether: a
turn that capped documents at 0 would stop consulting the wiki too, which is
exactly the coupling #537 is about.

So budgets decide the tool set here, and `describe_budgets` states the same
facts in the prompt — a model that is told its allowance up front spends it
deliberately instead of discovering the ceiling by hitting it. Sources that are
off are still NAMED there (#480): the agent can then tell the user "I'd need to
search the documents for this" instead of silently answering worse.
"""

from __future__ import annotations

from .context import KbSearchBudget, WikiSearchBudget

# The budgeted search tools, and the budget each one spends.
KB_SEARCH_TOOL = "kb_search"
WIKI_TOOL = "ask_wiki"


def tools_within_budget(
    allowed: list[str] | None,
    *,
    kb: KbSearchBudget,
    wiki: WikiSearchBudget,
) -> list[str] | None:
    """`allowed` minus the search tools whose budget is `0` for this turn.

    `None` in ⇒ `None` out: that is the "unspecified, use the defaults" arm of
    the tri-state `allowed_tools` contract, and a budget must not silently
    collapse it into an explicit list.
    """
    if allowed is None:
        return None
    off = {
        tool
        for tool, budget in ((KB_SEARCH_TOOL, kb.max_calls), (WIKI_TOOL, wiki.max_calls))
        if budget == 0
    }
    return [t for t in allowed if t not in off]


def _allowance(label: str, max_calls: int | None) -> str:
    if max_calls is None:
        return f"- **{label}**: as often as you need."
    if max_calls == 0:
        return (
            f"- **{label}**: OFF for this reply — the tool is not available. If "
            f"answering properly needs it, say so and suggest the user turn it back on."
        )
    times = "time" if max_calls == 1 else "times"
    return f"- **{label}**: at most {max_calls} {times}."


def describe_budgets(
    *,
    kb: KbSearchBudget,
    wiki: WikiSearchBudget,
    glossary: bool,
    has_wiki: bool,
) -> str:
    """The per-turn allowance block appended to the KB agent's prompt.

    Stating the allowance up front is the point: budgets were previously
    invisible until a tool refused, so a model planned as if searching were free
    and then got cut off mid-thought. `has_wiki` False means no collection in
    scope keeps a wiki at all — a different fact from "the wiki is off", and the
    agent should not offer to turn on something that doesn't exist.
    """
    lines = ["## What you may use for this reply", ""]
    if glossary:
        lines.append(_allowance("Glossary lookup", None))
    if has_wiki:
        lines.append(_allowance("The wiki", wiki.max_calls))
    else:
        lines.append("- **The wiki**: none of the collections in scope keeps one.")
    lines.append(_allowance("Document search", kb.max_calls))
    lines += [
        "",
        "Spend the cheap ones first and stop as soon as you can answer — an "
        "allowance is a ceiling, not a target.",
    ]
    return "\n".join(lines)
