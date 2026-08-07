"""#697 — look at what the graph WOULD be, without changing anything.

The point of the tool is to make the extraction criterion tunable: write a
criterion, run this, read the JSON, adjust. That only works if running it is
free — if it wrote, every experiment would alter the corpus being judged, and
you could only run it where the data is.

So it reads, computes, and writes JSON to a local directory. The computation is
the one production runs (``build_graph``), which is what makes the JSON a
preview rather than a second opinion.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import msgspec
from specstar import QB, SpecStar
from specstar.types import Binary

from workspace_app.kb.graph.preview import preview_collection, read_collection_sources
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import (
    GraphClaim,
    GraphEntity,
    GraphEntityLink,
    GraphMention,
    GraphRelationship,
)
from workspace_app.resources.kb import Collection, DocChunk, SourceDoc

_REPLY = (
    '{"mentions": [{"surface": "回焊爐", "kind": "機台"}, {"surface": "冷焊", "kind": "缺陷"}],'
    ' "relationships": [{"subject": "回焊爐", "predicate": "造成", "object": "冷焊"}],'
    ' "attributes": [{"subject": "回焊爐", "attribute": "良率", "value": "98.7", "unit": "%"}]}'
)


class _FakeLlm(ILlm):
    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield _REPLY, False


def _corpus(spec: SpecStar, *, guidance: str = "") -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="fab", use_graph=True, graph_guidance=guidance)
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    krm = spec.get_resource_manager(DocChunk)
    for doc_id, text in (("deck-A", "回焊爐造成冷焊"), ("deck-B", "回焊爐良率 98.7%")):
        with drm.using("bob"):
            drm.create(
                SourceDoc(
                    collection_id=cid,
                    path=f"{doc_id}.pptx",
                    content=Binary(data=b"x"),
                    collection_visibility="public",
                    collection_created_by="bob",
                ),
                resource_id=doc_id,
            )
        krm.create(
            DocChunk(collection_id=cid, source_doc_id=doc_id, seq=0, start=0, end=1, text=text)
        )
    return cid


_MODELS = (
    GraphMention,
    GraphClaim,
    GraphRelationship,
    GraphEntity,
    GraphEntityLink,
    Collection,
    SourceDoc,
    DocChunk,
)


def _snapshot(spec: SpecStar) -> dict[str, list[tuple[str, bytes]]]:
    """Every row of every model the tool could conceivably touch."""
    out: dict[str, list[tuple[str, bytes]]] = {}
    for model in _MODELS:
        rm = spec.get_resource_manager(model)
        out[model.__name__] = sorted(
            (r.info.resource_id, msgspec.json.encode(r.data))  # ty: ignore[unresolved-attribute]
            for r in rm.list_resources(QB.all().build())
        )
    return out


def test_a_preview_run_changes_nothing_in_the_database(tmp_path):
    """The acceptance criterion the whole tool exists for. Asserted over every
    model it reads AND every model the graph writes — a tool that only promised
    not to write would be a promise, and this is the check that it holds."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)
    before = _snapshot(spec)

    preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path)

    assert _snapshot(spec) == before


def test_the_preview_writes_the_graph_as_json(tmp_path):
    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)

    preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path)

    mentions = json.loads((tmp_path / "mentions.json").read_text())
    assert {m["surface"] for m in mentions} == {"回焊爐", "冷焊"}
    assert {m["source_doc_id"] for m in mentions} == {"deck-A", "deck-B"}

    entities = json.loads((tmp_path / "entities.json").read_text())
    assert {e["canonical_name"] for e in entities} == {"回焊爐", "冷焊", "機台", "缺陷", "造成"}

    claims = json.loads((tmp_path / "claims.json").read_text())
    assert [(c["subject"], c["attribute"], c["value"]) for c in claims] == [
        ("回焊爐", "良率", "98.7"),
        ("回焊爐", "良率", "98.7"),
    ]

    relationships = json.loads((tmp_path / "relationships.json").read_text())
    assert {(r["subject"], r["predicate"], r["object"]) for r in relationships} == {
        ("回焊爐", "造成", "冷焊")
    }


def test_the_summary_answers_the_question_the_tool_was_built_for(tmp_path):
    """Whether the criterion is working is a question about SHAPE, not about any
    one row: how many distinct names a document yields, and what sorts of thing
    the model decided it was seeing. Reading that off a list of thousands is not
    something anyone does, so the numbers are computed here."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)

    preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["documents"] == 2
    assert summary["chunks"] == 2
    assert summary["mentions"] == 4  # two names in each of two documents
    assert summary["distinct_names"] == 2
    assert summary["mentions_per_document"] == 2.0
    # the histogram that shows a corpus filling up with values or generic nouns
    assert summary["kinds"] == {"機台": 2, "缺陷": 2}
    # and the one way the preview differs from production, said out loud rather
    # than left for a reader to discover by disagreeing with the live graph
    assert "whole corpus" in summary["identity_scope"]
    # #697 P11: zero proposals because the pass is OFF by default here, which is
    # a different fact from the model having been asked and found nothing. A
    # reader comparing two previews has to be able to tell those apart, or they
    # read "the criterion stopped the model conflating things" off a run where
    # nobody asked it anything.
    assert summary["proposals"] == 0
    assert summary["proposals_asked"] is False


def test_the_summary_separates_a_silent_model_from_one_that_found_nothing(tmp_path):
    """#697 P15 — `proposals_asked` is only worth publishing if it means the
    model ANSWERED. Zero proposals from a pass that ran says the criterion is
    holding things apart; zero from a model that refused says nothing at all,
    and read as the first it is evidence for a conclusion nobody established."""

    from workspace_app.kb.graph.preview import preview_collection
    from workspace_app.kb.llm import ILlm

    class _Refuses(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield "I'm sorry, I can't help with that.", False

    class _Adjudicates(ILlm):
        """Answers the question actually asked — and finds nothing to merge,
        which is the whole point: the SAME empty result as the refusal."""

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield '{"groups": []}', False

    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)

    graph = preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path, propose_with=_Adjudicates())
    asked = json.loads((tmp_path / "summary.json").read_text())
    assert asked["proposals_asked"] is True, "a pass that ran must say so"
    assert graph.proposed is True

    preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path, propose_with=_Refuses())
    refused = json.loads((tmp_path / "summary.json").read_text())
    assert refused["proposals"] == 0
    assert refused["proposals_asked"] is False


def test_the_collections_own_criterion_is_what_the_preview_runs_with(tmp_path):
    """Otherwise the tool would preview something nobody is going to run."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec, guidance="只收機台與缺陷。")

    guidance, docs = read_collection_sources(spec, cid)

    assert guidance == "只收機台與缺陷。"
    assert sorted(d.doc_id for d in docs) == ["deck-A", "deck-B"]
    # the permission mirror rides along, so a previewed row is the row that would
    # have been stored — including whether anyone could have seen it
    assert all(d.mirror.get("collection_visibility") == "public" for d in docs)


def test_a_document_whose_deck_is_gone_is_left_out_rather_than_previewed():
    """Chunks outlive their deck (#104 made a chunk content-addressed). A deck
    that has gone has no permission to inherit, so its rows could only be born
    invisible — previewing them would show a graph nobody could ever see."""
    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)
    spec.get_resource_manager(SourceDoc).permanently_delete("deck-B")

    _, docs = read_collection_sources(spec, cid)

    assert [d.doc_id for d in docs] == ["deck-A"]


def test_a_collection_larger_than_one_page_is_read_whole(monkeypatch):
    """`list_resources` with no limit asks the store for everything in one
    statement, which the database refuses once a corpus is large enough (#689).
    Paging is what avoids that, and a paging bug loses documents SILENTLY — the
    preview would simply show a smaller corpus."""
    from workspace_app.kb.graph import paging as paging_mod

    monkeypatch.setattr(paging_mod, "PAGE", 2)
    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)
    krm = spec.get_resource_manager(DocChunk)
    for seq in range(1, 6):
        krm.create(
            DocChunk(
                collection_id=cid, source_doc_id="deck-A", seq=seq, start=0, end=1, text=f"t{seq}"
            )
        )

    _, docs = read_collection_sources(spec, cid)

    by_doc = {d.doc_id: d for d in docs}
    assert len(by_doc["deck-A"].chunks) == 6  # 1 original + 5, across three pages
    assert len(by_doc["deck-B"].chunks) == 1


def test_a_run_over_an_empty_collection_still_writes_readable_files(tmp_path):
    """Zero documents must not become a division by zero on the one number the
    tool is read for."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="empty", use_graph=True)).resource_id

    preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["documents"] == 0
    assert summary["mentions_per_document"] == 0.0


def test_the_preview_reads_as_the_person_it_was_told_to_read_as(tmp_path):
    """The flag is `--as-user`, and its help promises the preview shows the
    corpus THAT user can see. Setting the spec's default user alone does not do
    that — `default_user` decides who a write is attributed to, not what a read
    may return — so without the scope the flag reads as an assurance the tool was
    not giving."""
    from workspace_app.perm import Permission

    spec = make_spec(default_user=lambda: "alice")
    crm = spec.get_resource_manager(Collection)
    with crm.using("alice"):
        cid = crm.create(
            Collection(name="secret", use_graph=True, permission=Permission(visibility="private"))
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    krm = spec.get_resource_manager(DocChunk)
    with drm.using("alice"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="secret.pptx",
                content=Binary(data=b"x"),
                collection_visibility="private",
                collection_created_by="alice",
            ),
            resource_id="deck-S",
        )
    krm.create(
        DocChunk(collection_id=cid, source_doc_id="deck-S", seq=0, start=0, end=1, text="機密製程")
    )

    _, mine = read_collection_sources(spec, cid, as_user="alice")
    assert [d.doc_id for d in mine] == ["deck-S"]

    _, theirs = read_collection_sources(spec, cid, as_user="mallory")
    assert theirs == [], "an outsider was handed the contents of a private collection"


def test_being_allowed_to_know_a_corpus_exists_is_not_being_allowed_to_read_it(tmp_path):
    """#697 P16 — `--as-user` was scoped on read_meta, and the thing it hands
    over is content.

    "Discoverable" is a role the product models on purpose and offers in the
    share dialog: read_meta WITHOUT read_content — you may know this exists and
    ask for access, you may not read it. The scopes the preview entered
    (`collection_access_scope`, `source_doc_access_scope`) are read_meta-only,
    and `DocChunk` carries no access scope at all, so a preview taken as such a
    person wrote every verbatim chunk of the corpus into a local JSON file.

    Production's own rule for exactly this data says why, in
    `graph_evidence_access_scope`: "with only read_meta, a claim would hand over
    content the reader is not allowed to read."
    """
    from workspace_app.perm import Permission

    spec = make_spec(default_user=lambda: "alice")
    crm = spec.get_resource_manager(Collection)
    with crm.using("alice"):
        cid = crm.create(
            Collection(
                name="restricted",
                use_graph=True,
                # Bob may DISCOVER it. Nobody granted him the content.
                permission=Permission(visibility="restricted", read_meta=["user:bob"]),
            )
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    krm = spec.get_resource_manager(DocChunk)
    with drm.using("alice"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="secret.pptx",
                content=Binary(data=b"x"),
                collection_visibility="restricted",
                collection_read_meta=["user:bob"],
                collection_created_by="alice",
            ),
            resource_id="deck-S",
        )
    krm.create(
        DocChunk(
            collection_id=cid,
            source_doc_id="deck-S",
            seq=0,
            start=0,
            end=1,
            text="機密製程 SECRET",
        )
    )

    _, discoverable = read_collection_sources(spec, cid, as_user="bob")
    assert discoverable == [], "a read_meta-only grantee was handed the passages"

    preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path, as_user="bob")
    assert "SECRET" not in (tmp_path / "mentions.json").read_text()
    assert json.loads((tmp_path / "summary.json").read_text())["documents"] == 0

    # …and the owner is unaffected.
    _, owner = read_collection_sources(spec, cid, as_user="alice")
    assert [d.doc_id for d in owner] == ["deck-S"]


def test_a_document_tightened_on_its_own_is_left_out_too(tmp_path):
    """#308 lets one deck tighten inside a readable collection. The collection
    gate alone would wave it through — and the per-doc override is precisely the
    case where someone took the trouble to say no."""
    from workspace_app.perm import Permission

    spec = make_spec(default_user=lambda: "alice")
    crm = spec.get_resource_manager(Collection)
    with crm.using("alice"):
        cid = crm.create(Collection(name="open", use_graph=True)).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    krm = spec.get_resource_manager(DocChunk)
    # The deck is DISCOVERABLE to mallory and not readable — the only shape the
    # read_content check catches. A `private` override would be excluded by the
    # read_meta scope already, and a test using one passes without this gate.
    shut = Permission(visibility="restricted", read_meta=["user:mallory"])
    for doc_id, override in (("deck-open", None), ("deck-shut", shut)):
        with drm.using("alice"):
            drm.create(
                SourceDoc(
                    collection_id=cid,
                    path=f"{doc_id}.pptx",
                    content=Binary(data=b"x"),
                    permission=override,
                    collection_visibility="public",
                    collection_created_by="alice",
                ),
                resource_id=doc_id,
            )
        krm.create(
            DocChunk(collection_id=cid, source_doc_id=doc_id, seq=0, start=0, end=1, text="回焊爐")
        )

    _, docs = read_collection_sources(spec, cid, as_user="mallory")
    assert [d.doc_id for d in docs] == ["deck-open"], "a deck tightened on its own was previewed"


def test_chunks_reach_the_extractor_in_the_documents_own_order():
    """Nothing orders the store's answer, and passage order decides which kind and
    which declared quote a mention keeps (first non-empty wins) as well as the
    order every statement is written in. Read unordered, a re-run that changed
    nothing still produces a different graph."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c", use_graph=True)).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="d.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            ),
            resource_id="deck-A",
        )
    krm = spec.get_resource_manager(DocChunk)
    for seq in (2, 0, 3, 1):  # written out of order, as a re-index leaves them
        krm.create(
            DocChunk(
                collection_id=cid, source_doc_id="deck-A", seq=seq, start=0, end=1, text=f"t{seq}"
            )
        )

    _, (doc,) = read_collection_sources(spec, cid)

    assert [text for _, text in doc.chunks] == ["t0", "t1", "t2", "t3"]


def test_the_preview_shows_the_merges_people_have_already_accepted(tmp_path):
    """Otherwise a preview of any corpus anyone has curated shows every accepted
    merge come apart again — and a reader diffing two runs reads that as
    something the criterion did. Reading the decisions is still only reading, so
    the tool stays what it says it is."""
    from workspace_app.kb.graph.link import reconcile_vocabulary
    from workspace_app.kb.graph.review import accept_proposal, list_proposals
    from workspace_app.resources.graph import entity_id

    class _Judge(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield '{"groups": [{"names": ["回焊爐", "冷焊"], "why": "one thing"}]}', False

    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)
    # give the store a real graph, then have a person merge two of its identities
    from workspace_app.kb.graph.doc_write import write_doc_graph

    for doc in ("deck-A", "deck-B"):
        write_doc_graph(
            spec,
            _FakeLlm(),
            collection_id=cid,
            source_doc_id=doc,
            chunks=[(f"{doc}#0", "回焊爐造成冷焊")],
        )
    reconcile_vocabulary(spec, llm=_Judge())
    (proposal,) = list_proposals(spec)
    accept_proposal(spec, proposal.entity_id, proposal.proposed_from, by="amy")

    before = _snapshot(spec)
    graph = preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path)

    assert _snapshot(spec) == before, "reading the decisions was not a read"
    live = {e.canonical_name for e in graph.entities.values() if e.collection_ids}
    assert "冷焊" not in live or "回焊爐" not in live, (
        "the preview shows an accepted merge as two identities again"
    )
    merged = [e for e in graph.entities.values() if len(e.norm_keys) > 1]
    assert merged and set(merged[0].norm_keys) == {"回焊爐", "冷焊"}
    assert entity_id("回焊爐") in graph.entities


def test_the_summary_counts_the_names_that_are_really_measurements(tmp_path):
    """The suspected shape of the problem, made a number.

    A corpus whose extractor treats values as things grows a name per slide that
    never repeats — and those names start with a digit. Counting them is what
    turns "the vocabulary looks like junk" into something a criterion can be
    judged against, before and after.

    Two counts, because the obvious one under-reports: a name made only of
    value characters misses 「245°C」 and 「90 cm/min」, which are exactly the
    ones a process corpus produces most.
    """

    class _Values(ILlm):
        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            yield (
                '{"mentions": [{"surface": "回焊爐", "kind": "機台"},'
                ' {"surface": "245°C", "kind": "parameter"},'
                ' {"surface": "90 cm/min", "kind": "parameter"},'
                ' {"surface": "98.7%", "kind": "measurement"}]}',
                False,
            )

    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)

    preview_collection(spec, _Values(), cid, out_dir=tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["mentions"] == 8  # four names in each of two documents
    # 245°C / 90 cm/min / 98.7% — every one of them, in both documents
    assert summary["mentions_starting_with_a_digit"] == 6
    # the stricter shape only catches the one with no unit letters
    assert summary["mentions_that_are_only_a_value"] == 2


def test_a_documents_chunks_join_back_into_the_text_they_came_from():
    """Sampling dumps DOCUMENTS, not chunks, so the chunk size stays a knob
    somebody can turn — it is one of the live suspects for why the extractor
    answers at the level of "knowledge" and "structure" rather than naming
    things. Joining has to remove the overlap: the chunker advances by
    `max_tokens - overlap`, so consecutive chunks share their ends, and a naive
    concatenation would feed the model the same sentences twice.
    """
    from workspace_app.kb.chunker import FixedTokenChunker
    from workspace_app.kb.graph.preview import join_chunks

    canonical = " ".join(f"w{i}" for i in range(600))
    chunks = FixedTokenChunker(max_tokens=64, overlap_tokens=16).chunk(canonical)
    assert len(chunks) > 3, "this corpus does not exercise a single boundary"

    rebuilt = join_chunks([(c.seq, c.start, c.end, c.text) for c in reversed(chunks)])

    assert rebuilt == canonical


def _many_docs(spec, n: int) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="fab", use_graph=True)).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    krm = spec.get_resource_manager(DocChunk)
    for i in range(n):
        doc = f"deck-{i}"
        with drm.using("bob"):
            drm.create(
                SourceDoc(
                    collection_id=cid,
                    path=f"{doc}.pptx",
                    content=Binary(data=b"x"),
                    collection_visibility="public",
                    collection_created_by="bob",
                ),
                resource_id=doc,
            )
        for seq in range(2):
            krm.create(
                DocChunk(
                    collection_id=cid,
                    source_doc_id=doc,
                    seq=seq,
                    start=seq * 10,
                    end=seq * 10 + 10,
                    text=f"{doc}-p{seq} ",
                )
            )
    return cid


def test_sampling_splits_the_corpus_into_a_tuning_set_and_a_holdout(tmp_path):
    """A criterion tuned on the passages you looked at is a criterion that works
    on the passages you looked at. The holdout is what tells the difference —
    so no document may appear in both, and drawing it has to be the same one
    read, not a second trip that could see a corpus that moved."""
    from workspace_app.kb.graph.preview import dump_samples

    spec = make_spec(default_user=lambda: "bob")
    _many_docs(spec, 10)
    before = _snapshot(spec)

    counts = dump_samples(spec, "collection:missing", out_dir=tmp_path, tune=1, holdout=1)
    assert counts == (0, 0)  # an unreadable collection samples nothing

    cid = next(
        r.info.resource_id  # ty: ignore[unresolved-attribute]
        for r in spec.get_resource_manager(Collection).list_resources(QB.all().build())
    )
    tune, holdout = dump_samples(spec, cid, out_dir=tmp_path, tune=6, holdout=3, seed=7)

    assert (tune, holdout) == (6, 3)
    tuned = {p.stem for p in (tmp_path / "tune").glob("*.txt")}
    held = {p.stem for p in (tmp_path / "holdout").glob("*.txt")}
    assert len(tuned) == 6 and len(held) == 3
    assert not (tuned & held), "a document was in both sets, so the holdout proves nothing"
    # the file is the document's text, rejoined
    sample = next(iter(tuned))
    assert (tmp_path / "tune" / f"{sample}.txt").read_text() == f"{sample}-p0 {sample}-p1 "
    assert _snapshot(spec) == before, "sampling wrote to the store"


def test_sampling_is_repeatable_for_a_seed(tmp_path):
    """Two people tuning against 'the sample' have to be looking at the same
    passages, and a re-run after a crash must not silently change the set."""
    from workspace_app.kb.graph.preview import dump_samples

    spec = make_spec(default_user=lambda: "bob")
    cid = _many_docs(spec, 10)

    dump_samples(spec, cid, out_dir=tmp_path / "a", tune=4, holdout=2, seed=42)
    dump_samples(spec, cid, out_dir=tmp_path / "b", tune=4, holdout=2, seed=42)

    for half in ("tune", "holdout"):
        assert {p.name for p in (tmp_path / "a" / half).glob("*")} == {
            p.name for p in (tmp_path / "b" / half).glob("*")
        }


def test_a_folder_of_text_files_previews_without_a_store_at_all(tmp_path):
    """The tuning half. Once a sample has been drawn, iterating on a criterion
    should cost a model call per passage and nothing else — no database, no
    permissions, no environment that has to be reachable."""
    from workspace_app.kb.graph.preview import preview_samples

    samples = tmp_path / "in"
    samples.mkdir()
    (samples / "deck-A.txt").write_text("回焊爐造成冷焊")
    (samples / "deck-B.txt").write_text("回焊爐良率 98.7%")

    graph = preview_samples(_FakeLlm(), samples, out_dir=tmp_path / "out")

    assert {m.source_doc_id for m in graph.mentions} == {"deck-A", "deck-B"}
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["documents"] == 2


def test_the_sample_run_cuts_the_documents_itself(tmp_path):
    """So the chunk size is a knob this loop can turn — it is one of the live
    suspects for why the extractor answers at the level of "knowledge" rather
    than naming things, and it cannot be tested if the cut is baked into what
    was dumped."""
    from workspace_app.kb.graph.preview import preview_samples

    samples = tmp_path / "in"
    samples.mkdir()
    (samples / "d.txt").write_text(" ".join(f"w{i}" for i in range(200)))

    counts = []
    for max_tokens in (25, 200):
        graph = preview_samples(
            _CountingLlm(counts),
            samples,
            out_dir=tmp_path / f"out{max_tokens}",
            max_tokens=max_tokens,
            overlap_tokens=0,
        )
        assert graph is not None
    assert counts[0] > counts[1], "the chunk size did not change how the document was cut"


class _CountingLlm(ILlm):
    def __init__(self, sink: list[int]) -> None:
        self._n = 0
        self._sink = sink
        sink.append(0)

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self._n += 1
        self._sink[-1] = self._n
        yield _REPLY, False


def test_each_document_lands_on_disk_as_it_finishes(tmp_path):
    """A run is one model call per passage over a whole corpus — measured at ~99
    seconds per document on the real thing, so a 71-document collection is two
    hours. Writing only at the end means an interrupted run, a killed pod or an
    impatient Ctrl-C costs all of it and leaves nothing to look at.

    So every document is appended as it completes. The full JSON still lands at
    the end; this is what survives when the end never comes.
    """
    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)

    preview_collection(spec, _FakeLlm(), cid, out_dir=tmp_path)

    lines = [
        json.loads(line)
        for line in (tmp_path / "progress.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [row["document"] for row in lines] == ["deck-A", "deck-B"]
    assert all(row["names"] for row in lines), "a row that lists no names says nothing"
    assert "回焊爐" in lines[0]["names"]


def test_a_run_that_stops_partway_still_leaves_what_it_had(tmp_path):
    """The property the file exists for, asserted the way it actually happens:
    the model dies in the middle and nothing writes the final JSON."""

    class _DiesOnTheSecond(ILlm):
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("the model went away")
            yield _REPLY, False

    import pytest

    spec = make_spec(default_user=lambda: "bob")
    cid = _corpus(spec)

    with pytest.raises(RuntimeError):
        preview_collection(spec, _DiesOnTheSecond(), cid, out_dir=tmp_path)

    assert not (tmp_path / "summary.json").exists()  # the run never finished
    lines = (tmp_path / "progress.jsonl").read_text().splitlines()
    assert len(lines) == 1, "the document that DID finish was lost with the one that did not"


def test_a_collection_the_reader_cannot_open_previews_as_nothing():
    """Unreadable is indistinguishable from absent, by design — the scope hides
    the row rather than refusing it. A preview must not become the one channel
    that confirms a collection exists."""
    spec = make_spec(default_user=lambda: "bob")

    assert read_collection_sources(spec, "collection:does-not-exist") == ("", [])
