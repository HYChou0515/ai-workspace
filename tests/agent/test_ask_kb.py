"""#506: the configurable `ask_knowledge_base` backbone — an `AskKbSpec` (the
budget-only knobs a caller tunes) + `build_ask_kb_context` (the spec → sub-agent
context builder). The card drafter (P5) consumes these to give the ask-KB
sub-agent a capped wiki + chunk search over one collection; here they're proven
directly."""

from __future__ import annotations

from workspace_app.agent import AgentToolContext
from workspace_app.agent.ask_kb import AskKbSpec, build_ask_kb_context


def test_default_spec_grants_kb_search_wiki_and_glossary():
    # kb_search is always granted (a KB agent must search); the default spec caps
    # both searches and keeps the cheap glossary.
    assert AskKbSpec().allowed_tools() == ["kb_search", "search_wiki", "lookup_glossary"]


def test_wiki_max_zero_omits_search_wiki():
    # wiki off ⇒ the tool isn't granted (there's no wiki_mode enum; 0 = off).
    assert AskKbSpec(wiki_search_max=0).allowed_tools() == ["kb_search", "lookup_glossary"]


def test_glossary_false_omits_lookup_glossary():
    assert AskKbSpec(glossary=False).allowed_tools() == ["kb_search", "search_wiki"]


def test_build_stamps_budgets_from_spec():
    base = AgentToolContext(collection_ids=["c1"])
    ctx = build_ask_kb_context(AskKbSpec(kb_search_max=3, wiki_search_max=2), base)
    assert ctx.kb_search_budget.max_calls == 3
    assert ctx.wiki_search_budget.max_calls == 2


def test_build_scope_overrides_caller_collections():
    base = AgentToolContext(collection_ids=["c1", "c2"])
    ctx = build_ask_kb_context(AskKbSpec(scope=["only"]), base)
    assert ctx.collection_ids == ["only"]


def test_build_scope_none_inherits_caller_collections():
    base = AgentToolContext(collection_ids=["c1", "c2"])
    ctx = build_ask_kb_context(AskKbSpec(scope=None), base)
    assert ctx.collection_ids == ["c1", "c2"]


def test_build_preserves_other_base_context_fields():
    # only budgets + scope are stamped; everything else the caller set carries through.
    base = AgentToolContext(collection_ids=["c1"], acting_user="alice")
    ctx = build_ask_kb_context(AskKbSpec(), base)
    assert ctx.acting_user == "alice"
