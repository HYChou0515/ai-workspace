"""Issue #328: the findability probe service — given a doc + a representative
question, where do this doc's chunks rank in the real retriever, and how does
that change if we re-parse the doc under a CANDIDATE parser_guidance? Read-only:
the dry-run re-parse (Overlay) persists nothing."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from typing import cast

import pypdf

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.findability import (
    answer_from_passages,
    doc_passages_in_top_k,
    probe_findability,
)
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.llm import ILlm
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.retriever import Retriever
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM, RetrievedPassage


class _EchoVlm(IVlm):
    """A VLM that "follows the guidance": it emits the appended guidance line as
    its description (the last paragraph of the prompt, which is the collection /
    candidate guidance when present). So changing the guidance literally changes
    the chunk text the retriever sees — letting a test prove the candidate
    guidance moved the ranking, the same way a real VLM steered by it would."""

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        tail = prompt.rsplit("\n\n", 1)[-1].strip()
        yield f"## Figure\n\n{tail}", False


def _blank_pdf() -> bytes:
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)  # sparse → VLM describe path
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _setup():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    embedder = HashEmbedder(dim=EMBED_DIM)
    ing = Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=ParserRegistry().register(PdfParser(VlmDescriber(_EchoVlm()))),
    )
    # competitors already carry the query terms — the target must EARN its rank.
    for i in range(4):
        ing.ingest(
            collection_id=cid,
            user="u",
            filename=f"log{i}.md",
            data=f"solder void inspection log entry {i}".encode(),
        )
    (doc_id,) = ing.ingest(collection_id=cid, user="u", filename="deck.pdf", data=_blank_pdf())
    return spec, embedder, ing, doc_id


def test_probe_returns_before_ranks_for_the_doc():
    """Without a candidate guidance, the probe just reports where this doc's
    chunks currently rank for the question (deep) — `after` is absent."""
    spec, embedder, ing, doc_id = _setup()
    result = probe_findability(
        spec,
        Retriever(spec, embedder=embedder),
        ing,
        doc_id=doc_id,
        question="solder void root cause",
    )
    assert result.top_k == 5
    assert result.after is None
    # the deck's generic VLM description has no query terms → it ranks poorly
    # (or not at all within depth), which is exactly the red flag to fix.
    assert result.before.best_rank is None or result.before.best_rank > 1


def test_probe_candidate_guidance_improves_the_rank():
    """The what-if: re-parse THIS doc under a candidate guidance that makes the
    VLM emit the query-relevant terms → the doc becomes findable (its rank
    improves vs. before), all without persisting anything."""
    spec, embedder, ing, doc_id = _setup()
    result = probe_findability(
        spec,
        Retriever(spec, embedder=embedder),
        ing,
        doc_id=doc_id,
        question="solder void root cause",
        guidance="Focus the description on solder void root cause defects.",
    )
    assert result.after is not None
    # the guided re-parse made the doc surface...
    assert result.after.passages, "the re-parsed doc should appear in the results"
    assert result.after.best_rank is not None
    # ...and the candidate guidance's terms are in the previewed passage text.
    assert "solder void root cause" in result.after.passages[0].text.lower()
    # ...and it ranks at least as well as before (strictly better when it was a
    # generic, query-irrelevant description before).
    before = result.before.best_rank
    assert before is None or result.after.best_rank <= before


# ── #356 answer preview: doc∩top-k passages, then a tool-less grounded answer ──


def _passage(doc_id: str, text: str = "x", cid: str = "c") -> RetrievedPassage:
    return RetrievedPassage(
        collection_id=cid,
        document_id=doc_id,
        filename="f",
        start=0,
        end=len(text),
        source_chunk_ids=[],
        text=text,
    )


class _FakeRetriever:
    """Returns a fixed ranked list so a test can pin exactly which of the doc's
    passages fall inside (or outside) the top-k cutoff."""

    top_k = 5

    def __init__(self, ranked: list[RetrievedPassage]) -> None:
        self._ranked = ranked

    def search(self, query, collection_ids, on_progress=None, *, depth=None, **kw):
        return self._ranked[:depth] if depth is not None else self._ranked


def _fake_retriever(ranked: list[RetrievedPassage]) -> Retriever:
    """A `_FakeRetriever` typed as `Retriever` for the probe functions (it only
    uses `.search`/`.top_k`, which the fake supplies)."""
    return cast(Retriever, _FakeRetriever(ranked))


class _RecordingLlm(ILlm):
    """Streams a canned reply word-by-word and records the prompt it was given."""

    def __init__(self, reply: str) -> None:
        self.prompts: list[str] = []
        self._reply = reply

    def stream(self, prompt: str):
        self.prompts.append(prompt)
        for tok in self._reply.split(" "):
            yield tok + " ", False


def test_probe_k_sets_top_k_cutoff_and_widens_depth():
    """#356: the probe's k (slider) both flags which passages are in_top_k AND
    widens how deep we rank (max(DEFAULT_DEPTH, k)). k=5 ⇒ a rank-60 passage is
    beyond depth 50 (invisible); k=100 ⇒ it surfaces and counts as in_top_k."""
    spec, embedder, ing, doc_id = _setup()
    ranked = [
        _passage(doc_id if pos in (1, 60) else "other-doc", text=f"r{pos}") for pos in range(1, 101)
    ]

    res5 = probe_findability(spec, _fake_retriever(ranked), ing, doc_id=doc_id, question="q", k=5)
    assert res5.top_k == 5
    assert [p.rank for p in res5.before.passages] == [1]  # rank 60 is beyond depth 50
    assert res5.before.passages[0].in_top_k is True

    res100 = probe_findability(
        spec, _fake_retriever(ranked), ing, doc_id=doc_id, question="q", k=100
    )
    assert res100.top_k == 100
    assert [p.rank for p in res100.before.passages] == [1, 60]  # depth now 100
    assert all(p.in_top_k for p in res100.before.passages)


def test_doc_passages_in_top_k_keeps_only_doc_passages_within_k():
    """#356 example: the doc's passages rank 1, 4, 6, 12 and k=5 ⇒ only ranks 1 &
    4 enter the context window (intersection of THIS doc and the global top-k)."""
    spec, embedder, ing, doc_id = _setup()
    doc_positions = {1, 4, 6, 12}
    ranked = [
        _passage(doc_id, text=f"mine {pos}")
        if pos in doc_positions
        else _passage("other-doc", text=f"theirs {pos}")
        for pos in range(1, 15)
    ]
    got = doc_passages_in_top_k(
        spec, _fake_retriever(ranked), ing, doc_id=doc_id, question="q", k=5
    )
    assert [p.text for p in got] == ["mine 1", "mine 4"]


def test_doc_passages_in_top_k_after_guidance_returns_full_passage_text():
    """With a candidate guidance, the doc is re-parsed (Overlay) and its surfacing
    passages come back with FULL text (not the 600-char probe snippet) so the
    answerer has the real content."""
    spec, embedder, ing, doc_id = _setup()
    got = doc_passages_in_top_k(
        spec,
        Retriever(spec, embedder=embedder),
        ing,
        doc_id=doc_id,
        question="solder void root cause",
        k=5,
        guidance="Focus the description on solder void root cause defects.",
    )
    assert got and all(p.document_id == doc_id for p in got)
    assert "solder void root cause" in got[0].text.lower()


def test_answer_from_passages_streams_grounded_in_the_passages():
    """The answer is streamed chunk-by-chunk and the LLM is handed the kb system
    prompt + the numbered passages + the question (so its citation rules apply)."""
    llm = _RecordingLlm("Grounded answer [1].")
    seen: list[str] = []
    out = answer_from_passages(
        llm,
        system_prompt="KB-SYS",
        question="why do voids form?",
        passages=[_passage("d", text="voids form from flux outgassing")],
        on_chunk=lambda t, _r: seen.append(t),
    )
    assert "Grounded answer" in out
    assert seen, "the answer should stream"
    prompt = llm.prompts[0]
    assert "KB-SYS" in prompt
    assert "why do voids form?" in prompt
    assert "voids form from flux outgassing" in prompt
    assert "[1]" in prompt


def test_answer_from_passages_empty_intersection_still_calls_the_llm():
    """#356 Q8: an empty doc∩top-k still calls the LLM — the kb prompt then says
    the KB doesn't cover it (or answers from general knowledge), rather than us
    hard-coding a refusal."""
    llm = _RecordingLlm("The knowledge base does not appear to cover this.")
    out = answer_from_passages(llm, system_prompt="KB-SYS", question="q", passages=[])
    assert out and llm.prompts
    assert "no passages" in llm.prompts[0].lower()
