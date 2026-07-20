"""#537: `ask_wiki` — the KB agent's second knowledge source.

The wiki is consulted by DELEGATION, never by grepping it from the caller's own
loop: a wiki reader navigates index-first (index → pages → the sources behind
them) inside a throwaway context, and only its synthesized answer comes back.
That is what keeps whole wiki pages out of the caller's window (#270's context
economy) while still making the wiki a real, citable source (#537).

These prove the tool's contract against a fake consultant; the reader itself is
covered in tests/kb/wiki/test_reader.py.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.context import AgentToolContext, WikiSearchBudget
from workspace_app.agent.tools import ask_wiki_impl
from workspace_app.resources.kb import RetrievedPassage


def _passage(doc: str, text: str) -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c1",
        document_id=doc,
        filename=f"{doc}.md",
        start=0,
        end=len(text),
        source_chunk_ids=[],
        text=text,
        score=0.0,
    )


def _ctx(consult=None, **kw) -> AgentToolContext:
    return AgentToolContext(run_wiki_reader=consult, **kw)


async def test_the_answer_comes_back_and_its_sources_join_the_turns_citations():
    async def consult(question: str, sink=None):
        assert question == "what is zone 3?"
        return "Zone 3 runs at 245C [1].", [_passage("spec-doc", "Zone 3 setpoint 245C.")]

    ctx = _ctx(consult)
    out = await ask_wiki_impl(RunContextWrapper(ctx), "what is zone 3?")

    assert "245C" in out
    # The reader's grounding passage is registered on the TURN, so the caller can
    # quote its [n] and the answer's citation resolves to the underlying document.
    assert [p.document_id for p in ctx.kb_passages] == ["spec-doc"]
    # …and the tool message carries the resolved citation, so the reference card
    # opens the source document the wiki page was grounded on.
    (cites,) = ctx.subagent_citations["ask_wiki"]
    assert [(c.marker, c.document_id) for c in cites] == [(1, "spec-doc")]


async def test_markers_are_renumbered_onto_the_end_of_the_turns_citation_list():
    """The reader numbers its own sources from [1]; the caller may already hold
    passages from a kb_search. Folding one list into the other without shifting
    would silently repoint `[1]` at the wrong document."""

    async def consult(question: str, sink=None):
        return "The wiki says 245C [1] and 3 zones [2].", [
            _passage("wiki-src-a", "a"),
            _passage("wiki-src-b", "b"),
        ]

    ctx = _ctx(consult)
    ctx.kb_passages.append(_passage("earlier-chunk", "from an earlier kb_search"))

    out = await ask_wiki_impl(RunContextWrapper(ctx), "q")

    assert "[2]" in out and "[3]" in out  # shifted past the one passage already held
    assert "[1]" not in out
    assert [p.document_id for p in ctx.kb_passages] == [
        "earlier-chunk",
        "wiki-src-a",
        "wiki-src-b",
    ]


async def test_no_wiki_in_scope_says_so_instead_of_failing():
    ctx = _ctx(None)
    out = await ask_wiki_impl(RunContextWrapper(ctx), "q")
    assert "no wiki" in out.lower()
    # One bucket entry per call, including this early return — persist() pairs the
    # Nth bucket entry with the Nth `ask_wiki` tool message, so a skipped append
    # would drift every later pairing.
    assert ctx.subagent_citations["ask_wiki"] == [[]]


async def test_a_spent_budget_stops_further_consultations():
    calls = 0

    async def consult(question: str, sink=None):
        nonlocal calls
        calls += 1
        return "answer", []

    ctx = _ctx(consult, wiki_search_budget=WikiSearchBudget(max_calls=1))
    first = await ask_wiki_impl(RunContextWrapper(ctx), "q1")
    second = await ask_wiki_impl(RunContextWrapper(ctx), "q2")

    assert calls == 1
    assert "answer" in first
    assert "budget" in second.lower()
    assert ctx.subagent_citations["ask_wiki"] == [[], []]  # one entry per call


async def test_a_capped_result_reports_what_is_left_so_the_model_spends_frugally():
    async def consult(question: str, sink=None):
        return "answer", []

    ctx = _ctx(consult, wiki_search_budget=WikiSearchBudget(max_calls=3))
    out = await ask_wiki_impl(RunContextWrapper(ctx), "q")
    assert "1 of 3" in out and "2 left" in out


async def test_an_uncapped_consultation_reports_no_budget_line():
    async def consult(question: str, sink=None):
        return "answer", []

    out = await ask_wiki_impl(RunContextWrapper(_ctx(consult)), "q")
    assert out == "answer"
