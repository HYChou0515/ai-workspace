from workspace_app.kb.rerank import rerank_passages
from workspace_app.resources.kb import RetrievedPassage


class _FakeLlm:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._reply


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
