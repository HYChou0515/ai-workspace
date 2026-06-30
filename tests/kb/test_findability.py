"""Issue #328: the findability probe service — given a doc + a representative
question, where do this doc's chunks rank in the real retriever, and how does
that change if we re-parse the doc under a CANDIDATE parser_guidance? Read-only:
the dry-run re-parse (Overlay) persists nothing."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence

import pypdf

from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.findability import probe_findability
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.pdf import PdfParser
from workspace_app.kb.retriever import Retriever
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources import Collection, make_spec
from workspace_app.resources.kb import EMBED_DIM


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
