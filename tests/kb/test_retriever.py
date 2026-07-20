from collections.abc import Iterator

import msgspec
import pytest
from specstar import QB, SpecStar
from specstar.types import Binary

from workspace_app.config.schema import (
    EnhancementBool,
    EnhancementInt,
    EnhancementSettings,
)
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.llm import ILlm
from workspace_app.kb.retriever import Enhancements, Retriever
from workspace_app.resources.kb import Collection, DocChunk, SourceDoc


def _ingest(spec, chunker, embedder, name, text):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename=name, data=text.encode()
    )
    return cid


def test_search_vector_io_scales_with_candidates_not_collection_size(
    spec: SpecStar,
    chunker: FixedTokenChunker,
    embedder: HashEmbedder,
    monkeypatch: pytest.MonkeyPatch,
):
    # Perf contract: a search must NOT deserialize every chunk's embedding vector.
    # The bulk corpus load feeds BM25 + passage metadata (no vector) and MMR needs
    # vectors only for the fused candidates — so the number of vectors materialized
    # scales with `candidates`, not the collection's chunk count (the "load the whole
    # collection's 4096-d vectors" hang). Behavioural + mechanism-agnostic: it stays
    # true however the projection is implemented, and only fails if a full-collection
    # vector load is (re)introduced.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    for i in range(20):
        ing.ingest(
            collection_id=cid,
            user="u",
            filename=f"d{i}.md",
            data=f"reflow oven temperature zone {i} thermal profile solder paste".encode(),
        )
    rm = spec.get_resource_manager(DocChunk)
    total_chunks = len(rm.list_resources((QB["collection_id"] == cid).build()))
    assert total_chunks > 40, "need a corpus big enough that a full load is unmistakable"

    counter = {"vectors": 0}

    def _has_vec(data: object) -> bool:
        return bool(getattr(data, "embedding", None) or getattr(data, "embedding_alt", None))

    orig_list = rm.list_resources
    orig_get = rm.get

    def counting_list(*args: object, **kwargs: object) -> list:
        items = orig_list(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        counter["vectors"] += sum(1 for it in items if _has_vec(getattr(it, "data", None)))
        return items

    def counting_get(*args: object, **kwargs: object) -> object:
        res = orig_get(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        if _has_vec(getattr(res, "data", None)):
            counter["vectors"] += 1
        return res

    monkeypatch.setattr(rm, "list_resources", counting_list)
    monkeypatch.setattr(rm, "get", counting_get)

    cand = 5
    Retriever(spec, embedder=embedder, candidates=cand, top_k=3).search("reflow temperature", [cid])

    # Bounded by the candidate pool (a few dense probes + MMR hydration), nowhere
    # near the >40 chunks a full-collection load would touch.
    assert counter["vectors"] <= 4 * cand, (
        f"materialized {counter['vectors']} chunk vectors for {total_chunks} chunks — "
        "retrieval is bulk-loading vectors instead of projecting them away"
    )


def test_sparse_corpus_is_capped_when_a_common_term_matches_everything(
    spec: SpecStar,
    chunker: FixedTokenChunker,
    embedder: HashEmbedder,
    monkeypatch: pytest.MonkeyPatch,
):
    # The trigram pre-narrowing only helps for DISTINCTIVE terms. A query built
    # from common domain vocabulary fuzzy-matches nearly every chunk, so the filter
    # alone narrows nothing and the sparse arm is back to loading the collection —
    # the worst case, and the one real queries actually hit.
    #
    # So the corpus is also CAPPED: the store returns only the most trigram-similar
    # `sparse_corpus_cap` chunks, and BM25 ranks those. Text I/O is therefore
    # bounded by the cap no matter how many chunks match. (Recall is protected by
    # the dense arm, which is independent of this cap and still searches every
    # chunk through the vector index.)
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    # EVERY doc carries the query's vocabulary, so every chunk fuzzy-matches.
    for i in range(30):
        ing.ingest(
            collection_id=cid,
            user="u",
            filename=f"d{i}.md",
            data=f"reflow oven temperature zone {i} thermal profile solder paste".encode(),
        )
    rm = spec.get_resource_manager(DocChunk)
    total_chunks = len(rm.list_resources((QB["collection_id"] == cid).build()))
    assert total_chunks > 60, "need a corpus big enough that an uncapped load is obvious"

    counter = {"texts": 0}
    orig_list = rm.list_resources

    def counting_list(*args: object, **kwargs: object) -> list:
        items = orig_list(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        counter["texts"] += sum(
            1 for it in items if getattr(getattr(it, "data", None), "text", None)
        )
        return items

    monkeypatch.setattr(rm, "list_resources", counting_list)

    cap = 20
    Retriever(
        spec, embedder=embedder, candidates=5, top_k=3, sparse_corpus_cap=cap
    ).search("reflow oven temperature profile", [cid])

    # Bounded by the cap (plus the small fused hydration), NOT by the >60 chunks
    # that all match the query's common terms.
    assert counter["texts"] <= cap + 10, (
        f"materialized {counter['texts']} chunk texts for {total_chunks} matching chunks — "
        "the sparse corpus is not capped, so a common-word query still loads the collection"
    )


def test_sparse_corpus_scales_with_matches_not_collection_size(
    spec: SpecStar,
    chunker: FixedTokenChunker,
    embedder: HashEmbedder,
    monkeypatch: pytest.MonkeyPatch,
):
    # 2a: the BM25 (sparse) arm must not load + tokenize every chunk's text. It
    # pre-narrows its corpus to the chunks trigram-similar to a query term (pg_trgm
    # `.fuzzy`, GIN-served on Postgres), so the amount of chunk TEXT materialized
    # during a search scales with the lexical-match count, not the collection size.
    # Behavioural + mechanism-agnostic: fails only if a whole-collection text load
    # (the pre-2a corpus) returns.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    # Many docs share common vocabulary; ONE doc carries the rare term the query hits.
    # Distinct bodies per doc so #104 content-dedup keeps them as separate chunk sets.
    for i in range(19):
        ing.ingest(
            collection_id=cid,
            user="u",
            filename=f"common{i}.md",
            data=f"solder paste viscosity thixotropic index stencil aperture batch {i}".encode(),
        )
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="rare.md",
        data=b"quibblezorp flangewidget marker only here",
    )
    rm = spec.get_resource_manager(DocChunk)
    total_chunks = len(rm.list_resources((QB["collection_id"] == cid).build()))
    assert total_chunks > 40, "need a corpus big enough that a full text load is unmistakable"

    counter = {"texts": 0}

    def _has_text(data: object) -> bool:
        return bool(getattr(data, "text", None))

    orig_list = rm.list_resources
    orig_get = rm.get

    def counting_list(*args: object, **kwargs: object) -> list:
        items = orig_list(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        counter["texts"] += sum(1 for it in items if _has_text(getattr(it, "data", None)))
        return items

    def counting_get(*args: object, **kwargs: object) -> object:
        res = orig_get(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        if _has_text(getattr(res, "data", None)):
            counter["texts"] += 1
        return res

    monkeypatch.setattr(rm, "list_resources", counting_list)
    monkeypatch.setattr(rm, "get", counting_get)

    Retriever(spec, embedder=embedder, candidates=5, top_k=3).search("quibblezorp", [cid])

    # Only the rare doc's few chunks match the term + a small fused hydration —
    # nowhere near the >40 chunks a whole-collection corpus load would touch.
    assert counter["texts"] <= 15, (
        f"materialized {counter['texts']} chunk texts for {total_chunks} chunks — "
        "the sparse arm is loading the whole collection's text instead of narrowing"
    )


def test_overlay_that_empties_the_candidate_set_returns_no_passages(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #328 overlay edge: when the shadowed doc is the ONLY content and the candidate
    # re-parse yields no virtual chunks, the overlaid candidate set is empty — the
    # search returns nothing rather than falling through to rank an empty pool.
    from workspace_app.kb.retriever import Overlay

    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid, user="u", filename="only.md", data=b"reflow oven temperature zone"
    )
    only_id = encode_doc_id(cid, "only.md")
    overlay = Overlay(virtual_chunks=[], shadow_doc_id=only_id, virtual_text="")
    passages = Retriever(spec, embedder=embedder).search("reflow", [cid], overlay=overlay)
    assert passages == []


def test_search_with_no_query_terms_runs_dense_only(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # A punctuation-only query has no BM25 word tokens, so the trigram corpus
    # narrowing has nothing to fuzzy-match — it yields an empty sparse corpus (like
    # BM25's own no-terms guard) and the search runs on the dense arm alone, without
    # crashing.
    cid = _ingest(spec, chunker, embedder, "a.md", "reflow oven temperature zone three thermal")
    passages = Retriever(spec, embedder=embedder).search("!!! ???", [cid])
    # Dense still ranks (HashEmbedder), so we get results, just no keyword signal.
    assert all(isinstance(p.document_id, str) for p in passages)


def test_search_round_trips_do_not_scale_with_candidate_count(
    spec: SpecStar,
    chunker: FixedTokenChunker,
    embedder: HashEmbedder,
    monkeypatch: pytest.MonkeyPatch,
):
    # Phase 3: the ranking tail — chunk hydration, chunk→doc resolution, and the
    # doc path / quality / text joins — must BATCH its store reads instead of
    # issuing one (or several) per candidate. So the number of store round trips
    # stays ~flat as the candidate pool grows. The in-memory test backend makes
    # per-candidate reads look free; on Postgres each is a network round trip, so
    # an N+1 here is what a search pays after the bulk loads are gone.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    for i in range(30):
        ing.ingest(
            collection_id=cid,
            user="u",
            filename=f"d{i}.md",
            data=f"reflow oven temperature zone {i} thermal profile solder paste stencil".encode(),
        )

    managers = [spec.get_resource_manager(DocChunk), spec.get_resource_manager(SourceDoc)]
    # Capture the REAL methods once, so each measured run wraps the originals
    # rather than stacking a wrapper on a wrapper.
    originals = [(rm, rm.get, rm.list_resources) for rm in managers]

    def trips(candidates: int) -> int:
        counter = {"n": 0}

        def counted(orig: object):
            def call(*args: object, **kwargs: object) -> object:
                counter["n"] += 1
                return orig(*args, **kwargs)  # ty: ignore[call-non-callable]

            return call

        for rm, orig_get, orig_list in originals:
            monkeypatch.setattr(rm, "get", counted(orig_get))
            monkeypatch.setattr(rm, "list_resources", counted(orig_list))
        Retriever(spec, embedder=embedder, candidates=candidates, top_k=5).search(
            "reflow temperature profile", [cid]
        )
        return counter["n"]

    few = trips(5)
    many = trips(25)
    # A 5× bigger candidate pool must not cost 5× the round trips.
    assert many - few <= 5, (
        f"store round trips grew {few} → {many} as candidates went 5 → 25 — "
        "the ranking tail is issuing per-candidate queries instead of batching"
    )


def test_collection_of_only_orphaned_chunks_returns_no_passages(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104: when EVERY candidate is a true orphan (neither its content file_id nor
    # its owner id resolves to a live doc), nothing is citable — the search returns
    # an empty result rather than rendering uncitable passages, and never asks the
    # store for the text of zero documents.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid, user="u", filename="ghost.md", data=b"reflow oven temperature zone three"
    )
    chrm = spec.get_resource_manager(DocChunk)
    for r in chrm.list_resources((QB["collection_id"] == cid).build()):
        chunk = r.data
        assert isinstance(chunk, DocChunk)
        chrm.update(
            r.info.resource_id,  # ty: ignore[unresolved-attribute]
            msgspec.structs.replace(
                chunk, source_doc_id="gone-doc", source_file_id="no-such-content"
            ),
        )

    assert Retriever(spec, embedder=embedder).search("reflow temperature", [cid]) == []


def test_search_drops_a_chunk_whose_source_doc_is_gone(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104 orphan defense: a TRUE orphan is a chunk that resolves through
    # NEITHER path — its content `source_file_id` maps to no live doc AND its
    # `source_doc_id` is dangling (a re-home race, a stale ref — validate_refs is
    # off). Retrieval must DROP that hit, not crash the whole search (`_doc_path`
    # runs for every candidate before the text join that DOES guard). (After P1's
    # coalescing resolver, breaking source_doc_id alone no longer orphans a chunk
    # — file_id would rescue it — so both resolvers must miss here.)
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid, user="u", filename="live.md", data=b"reflow oven temperature zone three"
    )
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="ghost.md",
        data=b"reflow oven temperature zone four extra",
    )
    # Break BOTH resolvers on ghost.md's chunks: a content hash with no live doc
    # AND a dangling owner id (rewrite the row, so no cascade fires).
    chrm = spec.get_resource_manager(DocChunk)
    ghost = encode_doc_id(cid, "ghost.md")
    for r in chrm.list_resources((QB["source_doc_id"] == ghost).build()):
        chunk = r.data
        assert isinstance(chunk, DocChunk)
        rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
        chrm.update(
            rid,
            msgspec.structs.replace(
                chunk, source_doc_id="gone-doc", source_file_id="no-such-content"
            ),
        )

    passages = Retriever(spec, embedder=embedder).search("reflow temperature", [cid])

    ids = [p.document_id for p in passages]
    assert encode_doc_id(cid, "live.md") in ids  # the live doc still surfaces
    assert "gone-doc" not in ids  # the orphan hit is dropped, not a crash


def test_search_resolves_a_chunk_to_its_doc_by_content_file_id(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104 P1: chunk→doc resolution is COALESCING — a chunk carrying a
    # `source_file_id` resolves to the live doc that shares that content
    # (collection, file_id), INDEPENDENT of its own (deletable / stale)
    # `source_doc_id`. This is the guarantee that lets retrieval stop depending
    # on source_doc_id: even a dangling source_doc_id must NOT drop the hit while
    # the content still lives at a real path. (The inverse of the orphan test
    # above, which now needs BOTH resolvers to miss before a hit is dropped.)
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="keep.md",
        data=b"reflow oven temperature zone three thermal profile",
    )
    keep = encode_doc_id(cid, "keep.md")
    # Break the chunk's source_doc_id (as if it held a stale owner id) while it
    # keeps its real content file_id — the row is rewritten, so no cascade fires.
    chrm = spec.get_resource_manager(DocChunk)
    for r in chrm.list_resources((QB["source_doc_id"] == keep).build()):
        chunk = r.data
        assert isinstance(chunk, DocChunk)
        assert chunk.source_file_id, "ingest must stamp the content file_id (#104)"
        rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
        chrm.update(rid, msgspec.structs.replace(chunk, source_doc_id="stale-owner-id"))

    passages = Retriever(spec, embedder=embedder).search("reflow temperature", [cid])

    ids = [p.document_id for p in passages]
    # Resolved to the live doc VIA content file_id, not the dangling source_doc_id.
    assert keep in ids
    assert "stale-owner-id" not in ids


def test_search_cites_the_canonical_earliest_doc_for_shared_content(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104 P1: when identical content lives at several paths, a content-owned
    # chunk cites the CANONICAL doc — the earliest-created path — regardless of
    # which sibling its own source_doc_id names.
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    body = b"stencil aperture area ratio five to one design rule"
    ing.ingest(collection_id=cid, user="u", filename="early.md", data=body)  # canonical (first)
    ing.ingest(collection_id=cid, user="u", filename="late.md", data=body)  # dedup alias (0 chunks)
    early = encode_doc_id(cid, "early.md")
    late = encode_doc_id(cid, "late.md")
    # Point the content-owned chunks at the LATER sibling to prove resolution
    # follows content→canonical, not the chunk's own source_doc_id.
    chrm = spec.get_resource_manager(DocChunk)
    for r in chrm.list_resources((QB["source_doc_id"] == early).build()):
        chunk = r.data
        assert isinstance(chunk, DocChunk)
        rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
        chrm.update(rid, msgspec.structs.replace(chunk, source_doc_id=late))

    passages = Retriever(spec, embedder=embedder).search("stencil aperture", [cid])

    ids = [p.document_id for p in passages]
    assert early in ids  # canonical = earliest created
    assert late not in ids  # not the chunk's own (rewritten) source_doc_id


def test_search_falls_back_to_source_doc_id_for_legacy_chunks(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104 P1: a pre-#104 chunk (source_file_id == "") has no content key, so it
    # resolves via its source_doc_id — the fallback that keeps the whole existing
    # corpus retrievable BEFORE a reindex stamps file_ids (the R1 blackout guard).
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid, user="u", filename="legacy.md", data=b"paste viscosity thixotropic index"
    )
    legacy = encode_doc_id(cid, "legacy.md")
    # Simulate a pre-#104 chunk: strip its content file_id.
    chrm = spec.get_resource_manager(DocChunk)
    for r in chrm.list_resources((QB["source_doc_id"] == legacy).build()):
        chunk = r.data
        assert isinstance(chunk, DocChunk)
        rid = r.info.resource_id  # ty: ignore[unresolved-attribute]
        chrm.update(rid, msgspec.structs.replace(chunk, source_file_id=""))

    passages = Retriever(spec, embedder=embedder).search("paste viscosity", [cid])

    ids = [p.document_id for p in passages]
    assert legacy in ids  # resolved via source_doc_id fallback (source_file_id == "")


def test_hybrid_search_surfaces_the_keyword_matching_document(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # one collection with two docs; the query terms only match doc A
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="cats.md",
        data=b"the cat sat quietly on the warm mat all afternoon",
    )

    passages = Retriever(spec, embedder=embedder).search("reflow temperature", [cid])
    assert passages, "expected at least one passage"
    # keyword-matching doc on top
    assert passages[0].document_id == encode_doc_id(cid, "reflow.md")
    assert "reflow" in passages[0].text


def test_depth_returns_ranks_beyond_top_k(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#328: search(depth=N) widens the internal candidate / MMR caps and returns
    the full ranked passage list (up to N) instead of the top_k slice — so the
    findability probe can see where a doc's chunk lands beyond the 5 a user
    normally sees. depth=None is byte-for-byte the current behaviour."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    for i in range(8):
        ing.ingest(
            collection_id=cid,
            user="u",
            filename=f"d{i}.md",
            data=f"solder void analysis report number {i}".encode(),
        )
    r = Retriever(spec, embedder=embedder)  # top_k defaults to 5
    shallow = r.search("solder void", [cid])
    deep = r.search("solder void", [cid], depth=8)
    assert len(shallow) == 5  # the user-facing top_k slice
    assert len(deep) == 8  # every matching doc ranked, beyond the top 5
    # depth=None inherits the default slice exactly.
    assert [p.document_id for p in r.search("solder void", [cid], depth=None)] == [
        p.document_id for p in shallow
    ]


def test_overlay_swaps_a_docs_chunks_for_virtual_ones(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#328: search(overlay=...) ranks as if the shadowed doc held the supplied
    virtual chunks instead of its real ones — the virtual chunk competes through
    the SAME hybrid pipeline (so a dry-run prompt preview needs no reindex), and
    the shadowed doc's real chunks drop out of the candidate set."""
    from workspace_app.kb.retriever import Overlay
    from workspace_app.resources.kb import DocChunk

    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="a.md",
        data=b"reflow oven temperature drifted in zone three",
    )
    ing.ingest(collection_id=cid, user="u", filename="b.md", data=b"unrelated cat nap content")
    a_id = encode_doc_id(cid, "a.md")

    vtext = "hydraulic actuator pressure loss"
    virtual = DocChunk(
        collection_id=cid,
        source_doc_id=a_id,
        seq=0,
        start=0,
        end=len(vtext),
        text=vtext,
        embedding=embedder.embed_documents([vtext])[0],
    )
    overlay = Overlay(virtual_chunks=[virtual], shadow_doc_id=a_id, virtual_text=vtext)
    r = Retriever(spec, embedder=embedder)

    # the virtual chunk flows through the real pipeline and is retrievable
    found = r.search(vtext, [cid], overlay=overlay)
    assert any("hydraulic" in p.text for p in found)
    # the shadowed doc's REAL chunk no longer competes
    on_old = r.search("reflow temperature", [cid], overlay=overlay)
    assert not any("reflow" in p.text for p in on_old)


def test_overlay_shadows_shared_content_when_probing_an_aliased_doc(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104 P2 / R7: probing an ALIASED doc (its content chunks are owned by a
    # canonical sibling) must still shadow those SHARED content chunks — resolved
    # by the shadow doc's content file_id, not its source_doc_id — else the
    # findability preview double-counts real content alongside the virtual chunk.
    from workspace_app.kb.retriever import Overlay

    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    body = b"reflow oven temperature drifted in zone three"
    ing.ingest(collection_id=cid, user="u", filename="canon.md", data=body)  # owns the chunks
    ing.ingest(collection_id=cid, user="u", filename="alias.md", data=body)  # dedup alias, 0 chunks
    alias = encode_doc_id(cid, "alias.md")

    vtext = "hydraulic actuator pressure loss"
    virtual = DocChunk(
        collection_id=cid,
        source_doc_id=alias,
        seq=0,
        start=0,
        end=len(vtext),
        text=vtext,
        embedding=embedder.embed_documents([vtext])[0],
    )
    overlay = Overlay(virtual_chunks=[virtual], shadow_doc_id=alias, virtual_text=vtext)
    r = Retriever(spec, embedder=embedder)

    on_old = r.search("reflow temperature", [cid], overlay=overlay)
    assert not any("reflow" in p.text for p in on_old)  # shared content shadowed via file_id


def test_image_doc_passage_uses_parsed_text_not_raw_bytes(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#114: an image SourceDoc keeps its raw (non-UTF-8) bytes on `content`
    but the chunk offsets index into the parser's extracted `text`. The
    retriever must slice that stored text, never `content.decode(...)` — else
    the LLM gets U+FFFD image-byte garbage instead of the parsed markdown."""
    cid = _ingest(spec, chunker, embedder, "diagram.png", "alpha beta gamma delta epsilon")
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "diagram.png")
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    # swap the stored bytes for real image bytes (invalid UTF-8); `text` stays
    png = b"\x89PNG\r\n\x1a\n\xff\xd8\xff\xe0\x00\x10garbage\x80\x81\x82"
    rm.update(
        doc_id,
        msgspec.structs.replace(doc, content=Binary(data=png, content_type="image/png")),
    )

    passages = Retriever(spec, embedder=embedder).search("alpha", [cid])
    assert passages, "expected a passage for the image doc"
    assert "�" not in passages[0].text
    assert "alpha" in passages[0].text


def test_legacy_doc_without_stored_text_decodes_clean_utf8_bytes(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """A row predating stored `text` (text=None) but holding clean UTF-8 bytes
    still resolves its passage by decoding `content` — plain-text uploads keep
    working without a reindex."""
    cid = _ingest(spec, chunker, embedder, "legacy.md", "alpha beta gamma delta epsilon")
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "legacy.md")
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    rm.update(doc_id, msgspec.structs.replace(doc, text=None))

    passages = Retriever(spec, embedder=embedder).search("alpha", [cid])
    assert passages
    assert "alpha" in passages[0].text


def test_legacy_binary_doc_without_text_shows_marker_not_byte_garbage(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """A legacy binary row with no extracted text (text=None, non-UTF-8 bytes)
    surfaces a readable marker, never U+FFFD replacement-char garbage (#114)."""
    cid = _ingest(spec, chunker, embedder, "old.png", "alpha beta gamma delta epsilon")
    rm = spec.get_resource_manager(SourceDoc)
    doc_id = encode_doc_id(cid, "old.png")
    doc = rm.get(doc_id).data
    assert isinstance(doc, SourceDoc)
    png = b"\x89PNG\r\n\x1a\n\xff\xd8\xff\xe0\x00\x10garbage\x80\x81\x82"
    rm.update(
        doc_id,
        msgspec.structs.replace(doc, text=None, content=Binary(data=png, content_type="image/png")),
    )

    passages = Retriever(spec, embedder=embedder).search("alpha", [cid])
    assert passages
    assert "�" not in passages[0].text


def test_search_over_empty_collection_returns_nothing(spec: SpecStar, embedder: HashEmbedder):
    cid = spec.get_resource_manager(Collection).create(Collection(name="empty")).resource_id
    assert Retriever(spec, embedder=embedder).search("anything", [cid]) == []


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield self._reply, False


def test_multiquery_widens_recall_via_llm_variants(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon zeta"
    )
    # the query itself matches nothing; the LLM variant "gamma" does
    fake = _FakeLlm("gamma")
    passages = Retriever(spec, embedder=embedder, llm=fake).search("zzz nomatch", [cid])
    assert fake.prompts  # the multi-query step consulted the LLM
    # surfaced via the variant
    assert any(p.document_id == encode_doc_id(cid, "g.md") for p in passages)


def test_search_streams_enhancement_llm_thinking_via_on_progress(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """With all three knobs explicitly raised, every enhancement step
    labels itself through `on_progress` and its (fake) LLM output
    streams. Confirms the wiring; bundled defaults set HyDE off, so
    we raise it here to exercise all three paths together."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma",
        [cid],
        on_progress=lambda t, r: events.append((t, r)),
        enhancements=Enhancements(expand=1, hyde=1, rerank=True),
    )
    text = "".join(t for t, _ in events)
    # each enhancement step is labelled and its LLM output is streamed through
    assert "↻ expanding query" in text
    assert "↻ HyDE" in text
    assert "↻ rerank" in text
    assert "gamma" in text  # the (fake) model's streamed chunk


def test_search_caller_can_skip_all_enhancements_per_call(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """Passing `Enhancements(expand=0, hyde=0, rerank=False)` is the
    explicit "skip everything" path — the dense + BM25 fusion still
    runs. Replaces the legacy `quick=True` knob."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma",
        [cid],
        on_progress=lambda t, r: events.append((t, r)),
        enhancements=Enhancements(expand=0, hyde=0, rerank=False),
    )
    text = "".join(t for t, _ in events)
    assert "↻ expanding query" not in text
    assert "↻ HyDE" not in text
    assert "↻ rerank" not in text


def test_search_default_uses_shipped_enhancements_light(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """Bundled `EnhancementSettings()` is intentionally light: expand=1,
    hyde=0, rerank=on. So a default Retriever() runs expand + rerank
    but NOT HyDE — operators raise the knob explicitly when they want
    HyDE."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma", [cid], on_progress=lambda t, r: events.append((t, r))
    )
    text = "".join(t for t, _ in events)
    assert "↻ expanding query" in text
    assert "↻ HyDE" not in text  # bundled hyde.default == 0
    assert "↻ rerank" in text


def test_search_operator_max_clamps_caller_values(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """An over-eager caller asking for `expand=99` is clamped to the
    operator's `expand.max` (here `2`). Same shape for hyde / rerank."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    captured: list[int] = []

    class _RecordingLlm(_FakeLlm):
        def collect(self, prompt: str, *, on_chunk=None) -> str:  # ty: ignore
            if "alternative phrasings" in prompt:
                # `expand_queries(n=X)` weaves N into the prompt — read it
                # back so the test asserts the threaded value, not just
                # the labels.
                for token in prompt.split():
                    if token.isdigit():
                        captured.append(int(token))
                        break
            return super().collect(prompt, on_chunk=on_chunk)

    Retriever(
        spec,
        embedder=embedder,
        llm=_RecordingLlm("gamma"),
        enhancement_defaults=EnhancementSettings(
            expand=EnhancementInt(default=1, max=2),
            hyde=EnhancementInt(default=0, max=0),
            rerank=EnhancementBool(default=False, max=False),
        ),
    ).search(
        "gamma",
        [cid],
        enhancements=Enhancements(expand=99, hyde=99, rerank=True),
    )
    assert captured == [2]


def test_search_resolution_picks_caller_over_operator_default(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """Caller-set values override the operator default (so long as
    they don't exceed `max`). The bundled default has hyde=0; caller
    asking for hyde=1 (under max) flips it on."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="g.md", data=b"gamma delta epsilon"
    )
    events: list[tuple[str, bool]] = []
    Retriever(spec, embedder=embedder, llm=_FakeLlm("gamma")).search(
        "gamma",
        [cid],
        on_progress=lambda t, r: events.append((t, r)),
        enhancements=Enhancements(hyde=1),  # raise above default 0
    )
    text = "".join(t for t, _ in events)
    assert "↻ HyDE" in text


def test_empty_llm_replies_fall_back_to_the_plain_query(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # an LLM that returns nothing: no extra phrasings, no HyDE doc — still works
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    Ingestor(spec, chunker=chunker, embedder=embedder).ingest(
        collection_id=cid, user="u", filename="reflow.md", data=b"reflow oven temperature drift"
    )
    passages = Retriever(spec, embedder=embedder, llm=_FakeLlm("   ")).search("reflow", [cid])
    assert passages[0].document_id == encode_doc_id(cid, "reflow.md")


def test_search_excludes_denied_docs(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    """#308: `exclude_doc_ids` drops a doc's chunks from BOTH the dense and the
    BM25 paths, so a doc the speaker's per-doc override blocks never reaches
    ranking or the answer."""
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="reflow.md",
        data=b"reflow oven temperature drifted in zone three causing solder voids",
    )
    ing.ingest(
        collection_id=cid,
        user="u",
        filename="second.md",
        data=b"reflow temperature also matters greatly for this second document here",
    )
    blocked = encode_doc_id(cid, "reflow.md")
    r = Retriever(spec, embedder=embedder)
    # baseline: the reflow doc is retrievable
    assert any(p.document_id == blocked for p in r.search("reflow temperature", [cid]))
    # excluding it removes every passage from that doc; the other doc still returns
    got = r.search("reflow temperature", [cid], exclude_doc_ids=frozenset({blocked}))
    assert got, "the non-excluded doc should still return passages"
    assert all(p.document_id != blocked for p in got)


def test_doc_join_batch_loads_docs_outside_the_candidate_set(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # `_DocJoin` is built from the CANDIDATE chunks, but the attachment merge (#513
    # P9) discovers parent docs only AFTER ranking — a parent whose own chunks never
    # made the candidate cut is not in the join yet, so `load` batches it in. That
    # end-to-end shape needs a parent that loses to its own attachment, which the
    # hash embedder can't be made to produce reliably, so the contract is pinned
    # directly: unknown ids are fetched, already-known ids cost nothing.
    from workspace_app.kb.retriever import _DocJoin

    cid = _ingest(spec, chunker, embedder, "a.md", "reflow oven temperature zone three")
    doc_id = encode_doc_id(cid, "a.md")

    join = _DocJoin(spec, [])  # built over no candidates — knows nothing yet
    assert join.path_of(doc_id) is None

    join.load([doc_id])
    assert join.path_of(doc_id) == "a.md"

    join.load([doc_id])  # already known — the no-op path, no second fetch
    assert join.path_of(doc_id) == "a.md"
