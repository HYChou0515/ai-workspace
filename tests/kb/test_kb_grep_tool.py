"""plan-rag-context P3 — the `kb_grep` tool: Ctrl+F over the knowledge base.

It locates, in document-tree order, and is NOT citable: nothing lands in the
passage registry. Citing means reading (P4). Modelled on `search_wiki`."""

from agents import RunContextWrapper
from specstar import SpecStar

from workspace_app.agent import AgentToolContext
from workspace_app.agent.context import KbGrepBudget
from workspace_app.agent.tools import kb_grep_impl
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.ingest import Ingestor
from workspace_app.kb.retriever import Retriever
from workspace_app.resources.kb import Collection


def _kb(spec, chunker, embedder, docs: dict[str, str]):
    cid = spec.get_resource_manager(Collection).create(Collection(name="kb")).resource_id
    ing = Ingestor(spec, chunker=chunker, embedder=embedder)
    for name, text in docs.items():
        ing.ingest(collection_id=cid, user="u", filename=name, data=text.encode())
    return cid


def _ctx(spec, embedder, cid, **kw):
    return RunContextWrapper(
        AgentToolContext(
            spec=spec, retriever=Retriever(spec, embedder=embedder), collection_ids=[cid], **kw
        )
    )


async def test_kb_grep_lists_matching_lines_with_file_and_line_and_registers_nothing(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _kb(
        spec,
        chunker,
        embedder,
        {"paper.md": "intro\nsee Fig. 1 here\nend", "b/notes.md": "fig. 1 again\n"},
    )
    ctx = _ctx(spec, embedder, cid)
    out = kb_grep_impl(ctx, "fig. 1")
    # `path:line: text`, document-tree order (the folder first), the path as
    # shown in the file tree so it can be handed to the read tools as is.
    assert "b/notes.md:1: fig. 1 again" in out
    assert "paper.md:2: see Fig. 1 here" in out
    assert out.index("b/notes.md:1") < out.index("paper.md:2")
    assert "2 matching lines" in out
    assert ctx.context.kb_passages == []  # locate-only: nothing to cite


async def test_kb_grep_shows_the_page_when_the_document_has_pages(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # A paginated document's chunks carry `provenance.page`; the hit shows it so
    # the model can go straight to `read_page`. (Stamped directly — the PDF
    # parser is not needed to prove the plumbing.)
    import msgspec
    from specstar import QB

    from workspace_app.resources.kb import DocChunk

    cid = _kb(spec, chunker, embedder, {"deck.md": "alpha needle beta"})
    rm = spec.get_resource_manager(DocChunk)
    for r in rm.list_resources((QB["collection_id"] == cid).build()):
        ch = r.data
        assert isinstance(ch, DocChunk)
        rm.update(r.info.resource_id, msgspec.structs.replace(ch, provenance={"page": 7}))  # ty: ignore[unresolved-attribute]
    out = kb_grep_impl(_ctx(spec, embedder, cid), "needle")
    assert "deck.md (p.7):1: alpha needle beta" in out


async def test_kb_grep_scopes_to_a_folder_or_a_document(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _kb(
        spec,
        chunker,
        embedder,
        {"2024/a.md": "needle one", "2025/b.md": "needle two", "2025/c.md": "needle three"},
    )
    ctx = _ctx(spec, embedder, cid)
    out = kb_grep_impl(ctx, "needle", folder="2025")
    assert "needle two" in out and "needle three" in out and "needle one" not in out
    out = kb_grep_impl(ctx, "needle", document="c.md")
    assert "needle three" in out and "needle two" not in out
    assert "No documents under folder '2023'" in kb_grep_impl(ctx, "needle", folder="2023")


async def test_kb_grep_says_when_nothing_matches_and_still_spends_a_unit(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _kb(spec, chunker, embedder, {"a.md": "nothing to see"})
    ctx = _ctx(spec, embedder, cid, kb_grep_budget=KbGrepBudget(max_calls=2))
    out = kb_grep_impl(ctx, "absent")
    assert "no lines match" in out.lower()
    assert "1 of 2 used" in out
    kb_grep_impl(ctx, "absent")
    out = kb_grep_impl(ctx, "absent")  # exhausted
    assert "budget" in out.lower() and "do not call kb_grep again" in out


async def test_kb_grep_honours_the_speakers_document_exclusions(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    from workspace_app.kb.doc_id import encode_doc_id

    cid = _kb(spec, chunker, embedder, {"a.md": "needle a", "b.md": "needle b"})
    ctx = _ctx(spec, embedder, cid, exclude_doc_ids=frozenset({encode_doc_id(cid, "a.md")}))
    out = kb_grep_impl(ctx, "needle")
    assert "needle b" in out and "needle a" not in out and "a.md" not in out


async def test_kb_grep_gives_a_denied_document_the_same_answer_as_a_missing_one(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    from workspace_app.kb.doc_id import encode_doc_id

    cid = _kb(spec, chunker, embedder, {"hr/report.md": "needle secret", "pub/notes.md": "x"})
    ctx = _ctx(spec, embedder, cid, exclude_doc_ids=frozenset({encode_doc_id(cid, "hr/report.md")}))
    denied = kb_grep_impl(ctx, "needle", document="hr/report.md")
    missing = kb_grep_impl(ctx, "needle", document="nope/report.md")
    # The same SHAPE of answer (the message echoes the name asked for): no
    # "no lines match" for the denied one vs "no document" for the missing one.
    assert denied.startswith("No document matching") and missing.startswith("No document matching")
    assert "secret" not in denied
    # a folder whose documents are all denied is a folder that does not exist
    assert "No documents under folder 'hr'" in kb_grep_impl(ctx, "needle", folder="hr")


async def test_kb_grep_folder_scope_names_the_in_folder_holder_of_shared_content(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # #104: identical bytes at two paths share ONE chunk set, and attribution
    # picks the earliest holder — which may sit outside the folder. A folder
    # scope must name the holder INSIDE the folder.
    cid = _kb(
        spec,
        chunker,
        embedder,
        {"archive/report.md": "needle shared", "2024/report.md": "needle shared"},
    )
    out = kb_grep_impl(_ctx(spec, embedder, cid), "needle", folder="2024")
    assert "2024/report.md:1: needle shared" in out
    assert "archive/" not in out


# ── review round 3: guards that existed but nothing pinned ────────────────


async def test_kb_grep_explains_a_too_short_query_instead_of_matching_everything(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    cid = _kb(spec, chunker, embedder, {"a.md": "alpha\nbeta\n"})
    ctx = _ctx(spec, embedder, cid)
    out = kb_grep_impl(ctx, "a")
    assert "too short to search for exactly" in out and "at least 2 characters" in out
    assert "a.md" not in out


async def test_kb_grep_output_is_capped_with_middle_truncation(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder
):
    # Every line of a big document matches: the listing is bounded by the same
    # cap every tool output honours, cut in the middle so both ends survive.
    body = "\n".join(f"needle line {i:03d} with some padding text" for i in range(200))
    cid = _kb(spec, chunker, embedder, {"big.md": body})
    ctx = _ctx(spec, embedder, cid, exec_output_max_chars=600)
    out = kb_grep_impl(ctx, "needle")
    assert len(out) <= 600 + 100  # the cap plus the truncation notice
    assert "big.md:1: needle line 000" in out and "needle line 199" in out
    assert "chars omitted" in out


async def test_kb_grep_says_the_search_stopped_early_when_the_chunk_cap_hit(
    spec: SpecStar, chunker: FixedTokenChunker, embedder: HashEmbedder, monkeypatch
):
    # The retriever's `truncated` flag (a full page of candidate chunks) is
    # pinned at the retriever; this is its agent-facing half — the count is a
    # floor and the agent is told to narrow.
    from workspace_app.kb import retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "MAX_CHUNKS", 1)
    body = "\n".join(f"needle {i} " + "pad " * 8 for i in range(6))  # several chunks
    cid = _kb(spec, chunker, embedder, {"many.md": body})
    out = kb_grep_impl(_ctx(spec, embedder, cid), "needle")
    assert "at least 1 matching lines (the search stopped early" in out
