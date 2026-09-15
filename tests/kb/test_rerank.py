from collections.abc import Iterator

import msgspec

from workspace_app.kb.llm import ILlm
from workspace_app.kb.rerank import rerank_passages
from workspace_app.resources.kb import RetrievedPassage


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._reply, False


def _p(doc: str, text: str) -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c",
        document_id=doc,
        filename=doc,
        start=0,
        end=len(text),
        source_chunk_ids=[f"{doc}#0"],
        text=text,
        score=0.0,
    )


def test_rerank_reorders_by_the_models_ranking():
    passages = [_p("a.md", "alpha"), _p("b.md", "beta"), _p("c.md", "gamma")]
    llm = _FakeLlm("3, 1, 2")  # model says c, a, b
    out = rerank_passages(llm, "which?", passages)
    assert [p.document_id for p in out] == ["c.md", "a.md", "b.md"]
    assert "alpha" in llm.prompts[0]  # passages were shown to the model


def test_rerank_appends_unranked_and_ignores_out_of_range():
    passages = [_p("a.md", "alpha"), _p("b.md", "beta"), _p("c.md", "gamma")]
    # model names only #2, plus a bogus #9 — the rest keep their original order
    out = rerank_passages(_FakeLlm("2, 9"), "q", passages)
    assert [p.document_id for p in out] == ["b.md", "a.md", "c.md"]


def test_rerank_with_no_numbers_keeps_original_order():
    passages = [_p("a.md", "alpha"), _p("b.md", "beta")]
    out = rerank_passages(_FakeLlm("no idea"), "q", passages)
    assert [p.document_id for p in out] == ["a.md", "b.md"]


def test_rerank_empty_is_noop():
    assert rerank_passages(_FakeLlm("1"), "q", []) == []


def test_rerank_ranks_the_context_the_agent_will_read_not_the_bare_hit():
    # plan-rag-context P2: the reranker must see what will be DELIVERED. A hit
    # whose fragment lacks the answer but whose neighbouring context holds it
    # is exactly the passage expansion exists to rescue; ranking the bare
    # fragment would bury it.
    import msgspec

    widened = msgspec.structs.replace(
        _p("a.md", "alpha"), context_text="before alpha after", context_start=0, context_end=18
    )
    llm = _FakeLlm("1")
    rerank_passages(llm, "which?", [widened, _p("b.md", "beta")])
    assert "before alpha after" in llm.prompts[0]
    assert "[2] beta" in llm.prompts[0]  # an unexpanded passage still shows its hit


def test_rerank_trims_the_context_it_sees_around_the_hit():
    # plan-rag-context P6: the listing is one prompt holding every candidate,
    # and with neighbouring context on it grows ~4× (English) / ~27× (Chinese)
    # with no bound — a reranker whose window is smaller truncates from the
    # FRONT, where the question is, and the reply's numbers become noise it
    # applies silently. `context_cap` bounds what each candidate contributes,
    # centred on the hit so the matched text is always inside the window.
    import msgspec

    hit = _p("a.md", "alpha")
    widened = msgspec.structs.replace(hit, context_text="B" * 3000 + "alpha" + "C" * 3000)

    def prompt_for(cap):
        llm = _FakeLlm("1")
        rerank_passages(llm, "q", [widened], context_cap=cap)
        return llm.prompts[0]

    capped = prompt_for(100)
    assert "alpha" in capped
    assert capped.count("B") <= 60 and capped.count("C") <= 60  # a window, not the whole thing
    assert prompt_for(0).count("B") == 0 and "alpha" in prompt_for(0)  # 0 = the bare hit
    assert prompt_for(None).count("B") == 3000  # None = uncapped


def test_the_cap_keeps_the_head_when_the_hit_is_not_a_slice_of_its_context():
    from workspace_app.kb.rerank import _seen_by_reranker

    p = msgspec.structs.replace(
        _p("d", "zzz"), context_text="some context that does not hold the hit verbatim"
    )
    assert _seen_by_reranker(p, 12) == "some context"
