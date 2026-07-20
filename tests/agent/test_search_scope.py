"""#537: budgets decide which knowledge sources exist for a turn, and the prompt
says so up front.

Two halves of one idea. `tools_within_budget` makes `0` mean "not granted"
rather than "granted and then refused"; `describe_budgets` tells the agent its
allowance before it plans, instead of letting it discover the ceiling by being
cut off mid-thought.
"""

from __future__ import annotations

from workspace_app.agent.context import KbSearchBudget, WikiSearchBudget
from workspace_app.agent.search_scope import (
    allowance_note,
    describe_budgets,
    tools_within_budget,
)

ALL = ["kb_search", "ask_wiki", "lookup_glossary", "request_wiki_update"]


def _scope(allowed, kb=None, wiki=None):
    return tools_within_budget(
        allowed,
        kb=KbSearchBudget(max_calls=kb),
        wiki=WikiSearchBudget(max_calls=wiki),
    )


def test_zero_document_searches_removes_the_document_tool_and_nothing_else():
    # "only the wiki, not the documents" — the literal ask of #537. The wiki tool
    # survives untouched: the two budgets are independent.
    assert _scope(ALL, kb=0, wiki=3) == ["ask_wiki", "lookup_glossary", "request_wiki_update"]


def test_zero_wiki_consultations_removes_the_wiki_tool_and_nothing_else():
    assert _scope(ALL, kb=3, wiki=0) == ["kb_search", "lookup_glossary", "request_wiki_update"]


def test_both_off_leaves_the_free_lookups():
    # Degenerate but legal: answer from the glossary and the conversation.
    assert _scope(ALL, kb=0, wiki=0) == ["lookup_glossary", "request_wiki_update"]


def test_an_uncapped_or_merely_capped_budget_grants_the_tool():
    assert _scope(ALL, kb=None, wiki=None) == ALL
    assert _scope(ALL, kb=1, wiki=1) == ALL


def test_unspecified_tools_stay_unspecified():
    # `None` is the "haven't specified → defaults" arm of the tri-state contract.
    # Collapsing it into a concrete list here would silently strip an agent of
    # every default tool it never named.
    assert _scope(None, kb=0, wiki=0) is None


def test_a_tool_the_agent_never_had_is_not_conjured_by_a_budget():
    assert _scope(["lookup_glossary"], kb=5, wiki=5) == ["lookup_glossary"]


def _describe(kb=None, wiki=None, glossary=True, has_wiki=True):
    return describe_budgets(
        kb=KbSearchBudget(max_calls=kb),
        wiki=WikiSearchBudget(max_calls=wiki),
        glossary=glossary,
        has_wiki=has_wiki,
    )


def test_the_allowance_names_every_source_with_its_number():
    text = _describe(kb=2, wiki=3)
    assert "at most 2 times" in text  # documents
    assert "at most 3 times" in text  # wiki
    assert "as often as you need" in text  # glossary is free


def test_one_allowed_call_reads_as_singular():
    assert "at most 1 time." in _describe(kb=1)


def test_an_off_source_is_still_named_so_the_agent_can_ask_for_it():
    # #480: a disabled tool is disclosed, not hidden — the agent should be able to
    # say "I'd need document search for this" rather than quietly answering worse.
    text = _describe(kb=0, wiki=3)
    assert "OFF for this reply" in text
    assert "turn it back on" in text


def test_a_scope_with_no_wiki_says_so_rather_than_calling_it_off():
    # "there is no wiki here" and "the wiki is switched off" are different facts;
    # offering to re-enable something that doesn't exist wastes the user's time.
    text = _describe(has_wiki=False)
    assert "none of the collections in scope keeps one" in text
    assert "turn it back on" not in text


def test_the_glossary_is_omitted_when_the_agent_does_not_have_it():
    assert "Glossary" not in _describe(glossary=False)


def test_the_allowance_is_framed_as_a_ceiling_not_a_target():
    # Without this, a model reads "3 searches" as "do 3 searches".
    assert "ceiling, not a target" in _describe(kb=3)


def _note(allowed, kb=None, wiki=None, has_wiki=True):
    return allowance_note(
        allowed,
        kb=KbSearchBudget(max_calls=kb),
        wiki=WikiSearchBudget(max_calls=wiki),
        has_wiki=has_wiki,
    )


def test_an_agent_with_no_search_tools_gets_no_allowance_block():
    # A workspace agent, or the wiki reader itself — nothing to budget, so the
    # prompt stays exactly as it was.
    assert _note(None) == ""
    assert _note([]) == ""
    assert _note(["read_file", "exec"]) == ""


def test_a_kb_agent_gets_the_block():
    assert "What you may use for this reply" in _note(["kb_search", "ask_wiki"], kb=2, wiki=1)


def test_a_source_dropped_for_this_reply_is_still_disclosed():
    """The note is built from the grant BEFORE budgets trim it. Derived after the
    trim, "off for this reply" would be indistinguishable from "this agent never
    had it", and the agent could no longer tell the user what it's missing."""
    text = _note(["kb_search", "ask_wiki", "lookup_glossary"], kb=0, wiki=3)
    assert "OFF for this reply" in text
    assert "turn it back on" in text


def test_no_wiki_in_scope_reads_differently_from_a_wiki_switched_off():
    text = _note(["kb_search", "ask_wiki"], wiki=3, has_wiki=False)
    assert "none of the collections in scope keeps one" in text
