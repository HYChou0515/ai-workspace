"""#506: the configurable `ask_knowledge_base` backbone — an `AskKbSpec` (the
budget-only knobs a caller tunes) + `build_ask_kb_context` (the spec → sub-agent
context builder). The card drafter (P5) consumes these to give the ask-KB
sub-agent a capped wiki + chunk search over one collection; here they're proven
directly."""

from __future__ import annotations

from workspace_app.agent import AgentToolContext
from workspace_app.agent.ask_kb import AskKbSpec, build_ask_kb_context, make_ask_knowledge_base


def test_default_spec_grants_both_sources_and_the_glossary():
    assert AskKbSpec().allowed_tools() == ["kb_search", "ask_wiki", "lookup_glossary"]


def test_wiki_max_zero_omits_the_wiki_tool():
    # 0 = off ⇒ not granted at all (there's no wiki_mode enum).
    assert AskKbSpec(wiki_search_max=0).allowed_tools() == ["kb_search", "lookup_glossary"]


def test_kb_max_zero_omits_document_search():
    """#537: the mirror of the wiki knob, and the one that was missing. kb_search
    used to be granted unconditionally, so a caller could switch the wiki off but
    never the documents — "consult the wiki, not the documents" was unexpressible
    however the budgets were set."""
    assert AskKbSpec(kb_search_max=0).allowed_tools() == ["ask_wiki", "lookup_glossary"]


def test_both_sources_off_leaves_only_the_free_lookup():
    assert AskKbSpec(kb_search_max=0, wiki_search_max=0).allowed_tools() == ["lookup_glossary"]


def test_glossary_false_omits_lookup_glossary():
    assert AskKbSpec(glossary=False).allowed_tools() == ["kb_search", "ask_wiki"]


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


async def test_make_ask_knowledge_base_applies_the_spec_to_the_delegation():
    # The factory's product: a `run_subagent`-shaped callable that forwards to the
    # shared SubagentBridge with the spec's knobs applied — the ONE place the
    # drafter (P5) and the interactive KB agent (Task #1) differ is the spec they
    # pass. Here a fake bridge captures what the spec injected.
    captured: dict = {}

    async def fake_bridge(purpose, payload, emit=None, origin_id=None, **kw):
        captured.update(purpose=purpose, payload=payload, emit=emit, origin_id=origin_id, **kw)
        return "the answer", []

    run = make_ask_knowledge_base(
        AskKbSpec(
            kb_search_max=2, wiki_search_max=1, scope=["cid"], sub_agent_purpose="drafter_kb"
        ),
        fake_bridge,
    )
    answer, cites = await run("kb_chat", "what is M4?", None, "orig-1", None)

    assert (answer, cites) == ("the answer", [])
    assert captured["purpose"] == "drafter_kb"  # the spec's purpose wins over the caller's
    assert captured["payload"] == "what is M4?"
    assert captured["origin_id"] == "orig-1"
    assert captured["collection_ids"] == ["cid"]  # spec.scope forces the collection
    assert captured["budget"].max_calls == 2  # kb_search budget seeded from the spec
    assert captured["wiki_budget"].max_calls == 1  # wiki budget seeded from the spec
    assert captured["ask_kb_spec"].kb_search_max == 2  # spec forwarded so the bridge derives tools


async def test_make_ask_knowledge_base_inherits_incoming_scope_when_spec_scope_none():
    # A spec with no scope (the interactive KB agent) lets the caller's tier scope
    # through unchanged — same rule as build_ask_kb_context.
    captured: dict = {}

    async def fake_bridge(purpose, payload, emit=None, origin_id=None, **kw):
        captured.update(kw)
        return "", []

    run = make_ask_knowledge_base(AskKbSpec(scope=None), fake_bridge)
    await run("kb_chat", "q", None, None, ["tier-a", "tier-b"])
    assert captured["collection_ids"] == ["tier-a", "tier-b"]
