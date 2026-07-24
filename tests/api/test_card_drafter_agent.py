"""AgentCardDrafter (#506 P5) — the agentic CardDrafter. Where LlmCardDrafter
does one ILlm pass, this drives an agent LOOP so the drafter can consult the KB
(ask_knowledge_base) BEFORE drafting. #506/#577 follow-up: its DEFAULT consultation
is the GLOSSARY of existing cards only — not RAG or the wiki — because grading a
card against the same corpus it was extracted from suppresses nearly everything
(the self-suppression #577 fixed at reconcile; the drafter is a separate path). So
it drafts a card whenever the document defines a term, skipping only an exact
existing-card duplicate; card-vs-card dedup stays with reconcile. The final
assistant message is the same digest JSON the one-shot drafter emitted, parsed by
the same tolerant parser."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from workspace_app.agent import AgentToolContext
from workspace_app.agent.ask_kb import AskKbSpec
from workspace_app.api.card_drafter_agent import (
    AgentCardDrafter,
    default_card_drafter_config,
    default_drafter_ask_kb_spec,
    drafter_context_builder,
)
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone, RunError
from workspace_app.api.runner import ScriptedAgentRunner
from workspace_app.resources import AgentConfig

_GOOD = json.dumps(
    {
        "cards": [
            {
                "keys": ["M4", "Metal 4"],
                "title": "Metal 4",
                "body": "The fourth metal layer.",
                "snippet": "M4 (Metal 4) is the fourth interconnect layer.",
            }
        ]
    }
)


def test_agentic_drafter_parses_the_final_assistant_message_into_a_digest():
    # The loop's final assistant text IS the digest JSON — driven by an agent loop
    # instead of a one-shot ILlm, parsed by the same tolerant parser.
    runner = ScriptedAgentRunner([MessageDelta(text=_GOOD), RunDone()])
    d = AgentCardDrafter(runner, lambda cid: AgentToolContext())
    (card,) = d.digest(doc_path="a.md", doc_text="...").cards
    assert card.keys == ["M4", "Metal 4"]
    assert card.title == "Metal 4"


def test_streamed_chunks_concatenate_and_reasoning_is_ignored():
    # #494: a reasoning model streams its <think> scratch on the reasoning channel
    # and the answer in chunks on the content channel — the drafter must join the
    # content chunks and never let the reasoning text pollute the parsed digest.
    half = len(_GOOD) // 2
    runner = ScriptedAgentRunner(
        [
            MessageDelta(text='{"cards": [{"keys": ["SCRATCH"]}]}', reasoning=True),
            MessageDelta(text=_GOOD[:half]),
            MessageDelta(text=_GOOD[half:]),
            RunDone(),
        ]
    )
    d = AgentCardDrafter(runner, lambda cid: AgentToolContext())
    (card,) = d.digest(doc_path="a.md", doc_text="...").cards
    assert card.keys == ["M4", "Metal 4"]  # the content answer, not the reasoning scratch


def test_a_run_error_is_a_give_up_that_raises_not_a_silent_empty_digest():
    # #494 / #414: when the runner exhausts its retry budget (RunError), the doc
    # is a genuine give-up — raise so the coordinator marks it FAILED, rather than
    # returning an empty digest that reads as a falsely-green "0 cards" run. Any
    # partial answer text streamed before the error must NOT be parsed as a result.
    runner = ScriptedAgentRunner(
        [MessageDelta(text=_GOOD[:20]), RunError(message="all providers exhausted")]
    )
    d = AgentCardDrafter(runner, lambda cid: AgentToolContext())
    with pytest.raises(RuntimeError, match="all providers exhausted"):
        d.digest(doc_path="a.md", doc_text="...")


async def test_drafter_context_delegates_ask_knowledge_base_scoped_to_the_doc_collection():
    # The composition seam: the drafter's context wires `run_subagent` to a
    # spec-configured ask_knowledge_base (make_ask_knowledge_base) whose scope is
    # FORCED to the document's own collection and whose budgets come from the base
    # spec — so when the drafting agent delegates, it searches exactly [cid].
    captured: dict = {}

    async def fake_bridge(purpose, payload, emit=None, origin_id=None, **kw):
        captured.update(collection_ids=kw.get("collection_ids"), budget=kw.get("budget"))
        return "kb answer", []

    build = drafter_context_builder(
        bridge_run=fake_bridge,
        agent_config=AgentConfig(name="drafter", allowed_tools=["ask_knowledge_base"]),
        base_spec=AskKbSpec(kb_search_max=3, wiki_search_max=0),
    )
    ctx = build("cid-42")

    assert ctx.agent_config is not None
    assert ctx.agent_config.allowed_tools == ["ask_knowledge_base"]
    assert ctx.run_subagent is not None
    # invoking the wired run_subagent (exactly as ask_knowledge_base_impl does)
    answer, _ = await ctx.run_subagent("kb_chat", "what is M4?", None, "orig", None)
    assert answer == "kb answer"
    assert captured["collection_ids"] == ["cid-42"]  # scoped to the doc's collection
    assert captured["budget"].max_calls == 3  # kb budget from the base spec


def test_default_card_drafter_config_grants_only_ask_knowledge_base():
    # The drafting agent delegates KB lookups (context isolation, #270); it needs
    # exactly the delegating tool, not the leaf kb_search/search_wiki.
    cfg = default_card_drafter_config()
    assert cfg.allowed_tools == ["ask_knowledge_base"]
    assert cfg.system_prompt  # a non-empty role instruction


def test_drafter_spec_grants_no_wiki_or_corpus_search_so_it_cannot_self_suppress_cards():
    # #506/#577 follow-up — the root cause of "1000 docs → 5 proposals, all
    # suppressed": the agentic drafter used to consult RAG + wiki over the SAME
    # collection it extracts cards from, so nearly every term it drafted was
    # "already explained" in the corpus/wiki and it declined to draft the card.
    # That is the self-suppression #577 fixed only at the RECONCILE stage — the
    # drafter is a separate code path with its own (hardcoded) wiki budget. The
    # drafter's KB consultation must NOT reach the wiki or the document corpus:
    # "already known" = "already has a card" (the glossary = existing context
    # cards), never "the wiki / another doc mentions it". Card-vs-card dedup is
    # reconcile's job (#577), not the drafter's.
    spec = default_drafter_ask_kb_spec()
    assert spec.kb_search_max == 0  # no corpus suppression of cards
    assert spec.wiki_search_max == 0  # no wiki suppression of cards
    assert spec.glossary is True  # only the existing-cards glossary remains
    # With both searches off, the sub-agent gets ONLY the glossary — no `ask_wiki`,
    # no `kb_search` — so a card can never be dropped for being in the corpus/wiki.
    assert spec.allowed_tools() == ["lookup_glossary"]


class _PromptCapturingRunner:
    def __init__(self) -> None:
        self.prompt = ""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.prompt = prompt
        yield MessageDelta(text='{"cards": []}')
        yield RunDone()


def test_agentic_drafter_prompts_the_model_to_consult_the_knowledge_base_first():
    # By default the agentic drafter drives the KB-consulting prompt (not the
    # one-shot template) — it carries the document and grants the ask_knowledge_base
    # tool (now an existing-card check, not a corpus/wiki grade).
    runner = _PromptCapturingRunner()
    d = AgentCardDrafter(runner, lambda cid: AgentToolContext())
    d.digest(doc_path="reflow.md", doc_text="Zone 3 setpoint 245C.")
    assert "ask_knowledge_base" in runner.prompt
    assert "Zone 3 setpoint 245C." in runner.prompt  # the document rode along
    assert "reflow.md" in runner.prompt


def test_agentic_drafter_prompt_does_not_suppress_a_card_for_being_covered_elsewhere():
    # #506/#577 follow-up: the prompt must NOT tell the model to skip a card because
    # the concept is explained in the wiki or another document (the old step 3 said
    # "already known; leave it out" — that was the self-suppression). It must instead
    # say coverage elsewhere is not a reason to skip. This is the soft half of the
    # fix; the hard guarantee is the glossary-only spec above.
    runner = _PromptCapturingRunner()
    d = AgentCardDrafter(runner, lambda cid: AgentToolContext())
    d.digest(doc_path="reflow.md", doc_text="Zone 3 setpoint 245C.")
    # Collapse the markdown's hard line-wraps so a phrase match doesn't hinge on
    # where the prose happened to wrap.
    prompt = " ".join(runner.prompt.lower().split())
    assert "not a reason to skip" in prompt  # coverage elsewhere ≠ skip the card
    assert "leave it out" not in prompt  # the old suppression instruction is gone


def test_wire_agentic_card_drafter_swaps_the_coordinator_onto_the_closed_loop():
    # #506 worker parity: build_coordinators (shared by API + worker) builds the
    # OPEN-loop one-shot LlmCardDrafter; wire_agentic_card_drafter is the single
    # seam both create_app AND the split-deployment worker call to swap in the
    # agentic (closed-loop) drafter — so a card-gen job consults the KB before
    # drafting no matter which pod drains it. It must set_drafter an AgentCardDrafter.
    from workspace_app.agent.config_catalog import AgentConfigCatalog
    from workspace_app.api.card_drafter_agent import wire_agentic_card_drafter
    from workspace_app.kb.card_drafter import NullCardDrafter
    from workspace_app.kb.card_gen import CardDrafter
    from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.kb.retriever import Retriever
    from workspace_app.resources import make_spec
    from workspace_app.resources.kb import EMBED_DIM

    spec = make_spec(default_user="u")

    class _RecordingCoord(CardGenCoordinator):
        # A real coordinator (starts open-loop) that records the swapped-in drafter.
        def __init__(self) -> None:
            super().__init__(spec, NullCardDrafter())
            self.swapped: CardDrafter | None = None

        def set_drafter(self, drafter: CardDrafter) -> None:
            self.swapped = drafter

    coord = _RecordingCoord()
    wire_agentic_card_drafter(
        coord,
        spec=spec,
        runner=ScriptedAgentRunner([]),
        retriever=Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM)),
        catalog=AgentConfigCatalog(),
        kb_agent_config=AgentConfig(name="KB"),
        max_searches=3,
    )

    assert isinstance(coord.swapped, AgentCardDrafter)  # closed-loop drafter, not one-shot


def test_wire_passes_the_glossary_only_spec_and_never_re_enables_corpus_or_wiki(monkeypatch):
    # #506/#577 follow-up regression guard. A past version re-enabled the drafter's
    # chunk search here (`replace(default_drafter_ask_kb_spec(), kb_search_max=
    # max_searches or 3)`), which reintroduced the self-suppression: the drafter
    # graded its own cards against the corpus and dropped nearly all of them. The
    # composition root must pass the glossary-only default UNCHANGED, even when a
    # non-zero `max_searches` is configured. Spy on the builder to prove the spec it
    # receives grants ONLY the glossary — no kb_search, no ask_wiki.
    import workspace_app.api.card_drafter_agent as mod
    from workspace_app.agent.config_catalog import AgentConfigCatalog
    from workspace_app.kb.card_drafter import NullCardDrafter
    from workspace_app.kb.card_gen_coordinator import CardGenCoordinator
    from workspace_app.kb.embedder import HashEmbedder
    from workspace_app.kb.retriever import Retriever
    from workspace_app.resources import make_spec
    from workspace_app.resources.kb import EMBED_DIM

    spec = make_spec(default_user="u")
    captured: dict = {}

    def spy(*, bridge_run, agent_config, base_spec):
        captured["spec"] = base_spec
        return lambda cid: AgentToolContext()

    monkeypatch.setattr(mod, "drafter_context_builder", spy)

    class _Coord(CardGenCoordinator):
        def __init__(self) -> None:
            super().__init__(spec, NullCardDrafter())

        def set_drafter(self, drafter) -> None:  # noqa: D401 — record only
            pass

    mod.wire_agentic_card_drafter(
        _Coord(),
        spec=spec,
        runner=ScriptedAgentRunner([]),
        retriever=Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM)),
        catalog=AgentConfigCatalog(),
        kb_agent_config=AgentConfig(name="KB"),
        max_searches=3,  # a non-zero budget must NOT leak into the drafter's spec
    )

    got = captured["spec"]
    assert got.kb_search_max == 0 and got.wiki_search_max == 0
    assert got.allowed_tools() == ["lookup_glossary"]
