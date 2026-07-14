"""#513 P9 — retriever attachment-aware parent merge.

A defect figure (an attachment doc) is meaningless retrieved alone — its
semantics (morphology, judgement criteria) live in the PARENT document's text.
So when a search hits an attachment's chunk, the retriever additionally pulls
its parent document's content into the results. The parent rides along ON TOP of
the normal `top_k` cut (it doesn't displace a primary hit), and is not
duplicated when the parent was independently hit.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from specstar import SpecStar

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.image_fetcher import IImageFetcher
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.li_pipeline import build_doc_pipeline
from workspace_app.kb.parsers import ParserRegistry
from workspace_app.kb.parsers.vlm_image import VlmImageParser
from workspace_app.kb.retriever import Retriever
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.resources.kb import EMBED_DIM, Collection


class _FakeFetcher(IImageFetcher):
    def __init__(self, mapping: dict[str, tuple[bytes, str]]) -> None:
        self._mapping = mapping

    def fetch(self, url: str) -> tuple[bytes, str] | None:
        return self._mapping.get(url)


class _CannedVlm(IVlm):
    """Describes any image with a fixed caption — the attachment's chunk text."""

    def __init__(self, caption: str) -> None:
        self._caption = caption

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        yield f"## Figure\n\n{self._caption}", False


def _ingestor(spec: SpecStar, *, caption: str, url: str) -> Ingestor:
    embedder = HashEmbedder(dim=EMBED_DIM)
    registry = ParserRegistry().register(VlmImageParser(VlmDescriber(_CannedVlm(caption))))
    return Ingestor(
        spec,
        pipeline=build_doc_pipeline(embedder=embedder),
        embedder=embedder,
        parser_registry=registry,
        image_fetcher=_FakeFetcher({url: (b"\x89PNG\r\n\x1a\n px", "image/png")}),
    )


def _collection(spec: SpecStar) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name="defects")).resource_id


def test_hitting_an_attachment_pulls_in_its_parent_document(spec: SpecStar):
    # The image's caption is the ONLY place the query words appear; the parent
    # text is about something else — so a top_k=1 search returns just the
    # attachment, and the parent surfaces ONLY because P9 pulls it in.
    url = "http://img.local/ring.png"
    ingestor = _ingestor(spec, caption="solder ball void bright anomaly", url=url)
    cid = _collection(spec)
    md = (
        "# Ring Defect\n\nMorphology: concentric halos. "
        f"Judgement: scrap above three halos.\n\n![r]({url})\n"
    )
    ingestor.ingest(collection_id=cid, user="u", filename="d.md", data=md.encode())

    embedder = HashEmbedder(dim=EMBED_DIM)
    passages = Retriever(spec, embedder=embedder, top_k=1).search("solder ball void anomaly", [cid])

    ids = [p.document_id for p in passages]
    att_id = encode_doc_id(cid, "d.md/.att/img.local/ring.png")
    parent_id = encode_doc_id(cid, "d.md")
    assert att_id in ids  # the attachment hit (the sole top_k=1 primary result)
    assert parent_id in ids  # P9: its parent document pulled in on top of the cut
    parent = next(p for p in passages if p.document_id == parent_id)
    assert "Morphology" in parent.text  # the parent's FULL text (its morphology/criteria)


def test_parent_not_duplicated_when_independently_hit(spec: SpecStar):
    # When BOTH the parent text and the attachment caption match, the parent is a
    # primary hit already — the P9 merge must not append a second copy of it.
    url = "http://img.local/x.png"
    ingestor = _ingestor(spec, caption="solder ball void figure", url=url)
    cid = _collection(spec)
    md = f"# Defect\n\nsolder ball void morphology on the pad. Judgement: scrap.\n\n![x]({url})\n"
    ingestor.ingest(collection_id=cid, user="u", filename="d.md", data=md.encode())

    embedder = HashEmbedder(dim=EMBED_DIM)
    passages = Retriever(spec, embedder=embedder, top_k=5).search("solder ball void", [cid])

    ids = [p.document_id for p in passages]
    parent_id = encode_doc_id(cid, "d.md")
    att_id = encode_doc_id(cid, "d.md/.att/img.local/x.png")
    assert att_id in ids
    assert ids.count(parent_id) == 1  # present once — not duplicated by the merge
