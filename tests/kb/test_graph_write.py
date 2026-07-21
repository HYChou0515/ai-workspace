from collections.abc import Iterator

from specstar import QB

from workspace_app.kb.graph.normalize import norm_metric
from workspace_app.kb.graph.write import write_doc_claims
from workspace_app.kb.llm import ILlm
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim


class _FakeLlm(ILlm):
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield self._reply, False


def _claims(spec, doc: str) -> list[GraphClaim]:
    rm = spec.get_resource_manager(GraphClaim)
    out: list[GraphClaim] = []
    for r in rm.list_resources((QB["source_doc_id"] == doc).build()):
        assert isinstance(r.data, GraphClaim)
        out.append(r.data)
    return out


def _deck(spec, *, cid: str = "c1", doc_id: str = "deck-A") -> str:
    """A real Collection + SourceDoc for the extractor to mirror from. Since #534
    slice 2 a claim carries the deck's read permission, so the writer READS the
    deck — a fabricated id is now an invariant break, not a shortcut."""
    from specstar.types import Binary

    from workspace_app.resources.kb import Collection, SourceDoc

    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        crm.create(Collection(name="c"), resource_id=cid)
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            ),
            resource_id=doc_id,
        )
    return doc_id


def test_norm_metric_collapses_whitespace_and_casefolds():
    assert norm_metric("  Net   Income ") == "net income"
    assert norm_metric("營收") == "營收"


def test_write_doc_claims_persists_with_norm_and_provenance():
    llm = _FakeLlm(
        '[{"metric": "Revenue", "period": "Q3", "value": "1.2M", "unit": "USD"},'
        ' {"metric": "Head Count", "value": "340"}]'
    )
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    n = write_doc_claims(
        spec, llm, collection_id="c1", source_doc_id="deck-A", chunks=[("deck-A#0", "t")]
    )
    assert n == 2
    claims = _claims(spec, "deck-A")
    assert {c.metric for c in claims} == {"Revenue", "Head Count"}
    assert {c.norm_metric for c in claims} == {"revenue", "head count"}
    assert all(c.collection_id == "c1" and c.chunk_id == "deck-A#0" for c in claims)


def test_write_doc_claims_is_idempotent_wipe_then_rewrite():
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_claims(
        spec,
        _FakeLlm('[{"metric": "Revenue", "value": "1.2M"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "x")],
    )
    write_doc_claims(
        spec,
        _FakeLlm('[{"metric": "Revenue", "value": "1.3M"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "x")],
    )
    claims = _claims(spec, "deck-A")
    assert len(claims) == 1  # wiped + rewritten, never doubled
    assert claims[0].value == "1.3M"  # the re-run's value won


def test_write_doc_claims_stamps_the_deck_permission_mirror():
    """#534 slice 2: every claim is written carrying the deck's EFFECTIVE read
    permission, so ``graph_claim_access_scope`` can hide it without a join. The
    extractor is the only writer on the create path, so if it skips the mirror the
    claim is born invisible (the fail-closed default) — the value is not optional."""
    from specstar.types import Binary

    from workspace_app.perm import Permission
    from workspace_app.resources.kb import Collection, SourceDoc

    llm = _FakeLlm('[{"metric": "Revenue", "value": "1.2M"}]')
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(
                name="c", permission=Permission(visibility="restricted", read_meta=["user:amy"])
            )
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        doc_id = drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="restricted",
                collection_read_meta=["user:amy"],
                collection_created_by="bob",
                permission=Permission(visibility="restricted", read_content=["user:amy"]),
            )
        ).resource_id
    write_doc_claims(
        spec, llm, collection_id=cid, source_doc_id=doc_id, chunks=[(f"{doc_id}#0", "t")]
    )
    (claim,) = _claims(spec, doc_id)
    assert claim.collection_visibility == "restricted"
    assert claim.collection_read_meta == ["user:amy"]
    assert claim.collection_created_by == "bob"
    assert claim.doc_visibility == "restricted"
    assert claim.doc_read_content == ["user:amy"]


def test_write_doc_claims_mirrors_an_untightened_deck_as_public():
    """A deck with no override of its own states the verdict explicitly rather than
    leaving the field empty — empty means "never written" and hides the row."""
    from specstar.types import Binary

    from workspace_app.resources.kb import Collection, SourceDoc

    llm = _FakeLlm('[{"metric": "Revenue", "value": "1.2M"}]')
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        doc_id = drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            )
        ).resource_id
    write_doc_claims(
        spec, llm, collection_id=cid, source_doc_id=doc_id, chunks=[(f"{doc_id}#0", "t")]
    )
    (claim,) = _claims(spec, doc_id)
    assert claim.doc_visibility == "public"
    assert claim.doc_read_content == []


def test_write_doc_claims_wipes_and_skips_a_doc_that_no_longer_exists():
    """#104 made a chunk content-addressed rather than bound to a deletable doc, so
    chunks can outlive their deck. A vanished deck has no permission to mirror, so
    there is nothing to extract FOR — the writer clears whatever it left behind and
    returns 0 rather than failing the whole batch on one dangling doc."""
    spec = make_spec(default_user=lambda: "bob")
    _deck(spec)
    write_doc_claims(
        spec,
        _FakeLlm('[{"metric": "Revenue", "value": "1.2M"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "x")],
    )
    assert len(_claims(spec, "deck-A")) == 1
    from workspace_app.resources.kb import SourceDoc

    spec.get_resource_manager(SourceDoc).permanently_delete("deck-A")
    n = write_doc_claims(
        spec,
        _FakeLlm('[{"metric": "Revenue", "value": "1.2M"}]'),
        collection_id="c1",
        source_doc_id="deck-A",
        chunks=[("deck-A#0", "x")],
    )
    assert n == 0
    assert _claims(spec, "deck-A") == []


def test_write_doc_claims_reads_the_collection_verdict_live_not_the_decks_copy():
    """A deck carries its OWN cached copy of the collection mirror, maintained by a
    fan-out that can lag or fail. Stamping claims from that copy would let the
    extraction pass silently undo the reconcile that just ran — every pass, for
    every doc it touches. The collection is the source of truth."""
    from specstar.types import Binary

    from workspace_app.perm import Permission
    from workspace_app.resources.kb import Collection, SourceDoc

    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(name="c", permission=Permission(visibility="private"))
        ).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        doc_id = drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                # a STALE copy: the collection is private, this still says public
                collection_visibility="public",
                collection_created_by="bob",
            )
        ).resource_id
    write_doc_claims(
        spec,
        _FakeLlm('[{"metric": "Revenue", "value": "1.2M"}]'),
        collection_id=cid,
        source_doc_id=doc_id,
        chunks=[(f"{doc_id}#0", "t")],
    )
    (claim,) = _claims(spec, doc_id)
    assert claim.collection_visibility == "private"


def test_write_doc_claims_stamps_every_comparison_key():
    """All three keys are written at extraction, not just the metric one: they are
    what the grouping reads, and a key left empty groups with every other row that
    also failed to write one."""
    from specstar.types import Binary

    from workspace_app.resources.kb import Collection, SourceDoc

    llm = _FakeLlm(
        '[{"metric": "Revenue (USD)", "period": "2024年第三季", "value": "1.2M", "unit": "美元"}]'
    )
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    drm = spec.get_resource_manager(SourceDoc)
    with drm.using("bob"):
        doc_id = drm.create(
            SourceDoc(
                collection_id=cid,
                path="deck.pptx",
                content=Binary(data=b"x"),
                collection_visibility="public",
                collection_created_by="bob",
            )
        ).resource_id
    write_doc_claims(
        spec, llm, collection_id=cid, source_doc_id=doc_id, chunks=[(f"{doc_id}#0", "t")]
    )
    (claim,) = _claims(spec, doc_id)
    assert claim.metric == "Revenue (USD)"  # the raw surface survives for display
    assert claim.norm_metric == norm_metric("Revenue")
    assert claim.norm_period == "Q:2024:3"
    assert claim.norm_unit == "USD"


def test_migrating_a_claim_recomputes_its_keys_under_the_current_rules(tmp_path):
    """The keys are derived STATE, so a rule change is a schema change: the migrate
    step carries the new algorithm and rewrites rows that predate it.

    The row here is written by a spec with NO Schema for GraphClaim — exactly what
    slice 1 shipped, so its rows sit at version ``None`` holding keys the slice-1
    rule produced and no period/unit key at all. Running migrate on the current
    code brings them onto the current rules, which is what stops an improved rule
    from reaching only new data."""
    from specstar import BackendBinding, BackendConfig, ConnectionProfile, SpecStar

    from workspace_app.resources.graph import GraphClaim
    from workspace_app.resources.kb import Collection

    backend = BackendConfig(
        connections={"local": ConnectionProfile(type="disk", options={"rootdir": str(tmp_path)})},
        meta=BackendBinding(use="local"),
        resource=BackendBinding(use="local"),
        blob=BackendBinding(use="local"),
    )
    spec_old = SpecStar()
    spec_old.configure(default_user="bob", backend=backend)
    spec_old.add_model(Collection)
    spec_old.add_model(GraphClaim, indexed_fields=["collection_id"])  # no Schema ⇒ version None
    rid = (
        spec_old.get_resource_manager(GraphClaim)
        .create(
            GraphClaim(
                collection_id="c1",
                source_doc_id="deck-A",
                norm_metric="revenue (usd)",  # what the slice-1 rule produced
                metric="Revenue (USD)",
                value="1.2M",
                period="2024年第三季",
                unit="美元",
            )
        )
        .resource_id
    )

    rm = make_spec(default_user="bob", backend=backend).get_resource_manager(GraphClaim)
    rm.migrate(rid)  # operator backfill: POST /graph-claim/migrate/execute
    got = rm.get(rid).data
    assert isinstance(got, GraphClaim)
    assert got.norm_metric == "revenue"
    assert got.norm_period == "Q:2024:3"
    assert got.norm_unit == "USD"
