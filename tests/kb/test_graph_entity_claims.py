"""#534 — the numbers land on the entity (claims join the backbone).

Before this, GraphClaim was a write-only island: "回焊爐 Q3 良率 98.7%" was
extracted but never attached to 回焊爐, so the entity page had names and arrows
but no data. The join is CO-LOCATION, read-time: a claim belongs on the page
when its ``chunk_id`` is one of the chunks a readable mention of this entity
was seen in — the number was stated on a slide that talks about the thing. No
schema change, no re-extraction, and a wrong association costs nothing to fix
(re-linking mentions moves the numbers with them).
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.normalize import norm_metric, norm_surface
from workspace_app.kb.graph.review import entity_page
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim, GraphEntity, GraphMention, mention_id
from workspace_app.resources.kb import Collection


def _seed(spec: SpecStar) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    for doc in ("deck-A", "deck-B"):
        with mrm.using("bob"):
            mrm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id=doc,
                    surface="回焊爐",
                    norm_surface=norm_surface("回焊爐"),
                    kind="機台",
                    norm_kind=norm_surface("機台"),
                    occurrences=2,
                    chunk_ids=[f"{doc}#0"],
                    collection_visibility="public",
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id(doc, "回焊爐"),
            )
    link_identical_mentions(spec)
    erm = spec.get_resource_manager(GraphEntity)
    for r in erm.list_resources():
        if isinstance(r.data, GraphEntity) and r.data.canonical_name == "回焊爐":
            return r.info.resource_id  # ty: ignore[unresolved-attribute]
    raise AssertionError("no entity")


def _claim(
    spec: SpecStar,
    cid: str,
    *,
    doc: str,
    chunk: str,
    metric: str = "良率",
    value: str = "98.7",
    period: str = "Q3",
    doc_visibility: str = "public",
) -> None:
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("bob"):
        rm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id=doc,
                chunk_id=chunk,
                norm_metric=norm_metric(metric),
                metric=metric,
                value=value,
                period=period,
                norm_period=period.casefold(),
                unit="%",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility=doc_visibility,
            )
        )


def _cid(spec: SpecStar) -> str:
    rm = spec.get_resource_manager(Collection)
    for r in rm.list_resources():
        return r.info.resource_id  # ty: ignore[unresolved-attribute]
    raise AssertionError("no collection")


def test_a_number_stated_beside_the_entity_lands_on_its_page():
    spec = make_spec()
    eid = _seed(spec)
    cid = _cid(spec)
    _claim(spec, cid, doc="deck-A", chunk="deck-A#0")  # same slide as the mention

    page = entity_page(spec, eid, as_user="alice")

    assert len(page.claims) == 1
    claim = page.claims[0]
    assert claim.metric == "良率"
    assert claim.value == "98.7"
    assert claim.source_doc_id == "deck-A"


def test_a_number_from_an_unrelated_slide_stays_off_the_page():
    """Same doc, different slide — the co-location rule is the CHUNK, not the
    document: a deck can talk about ten machines and its numbers must not smear
    across all of them."""
    spec = make_spec()
    eid = _seed(spec)
    cid = _cid(spec)
    _claim(spec, cid, doc="deck-A", chunk="deck-A#7")  # a slide with no mention

    page = entity_page(spec, eid, as_user="alice")

    assert page.claims == []


def test_an_unreadable_docs_number_never_arrives():
    """The claim's own access scope filters it — same one the auto routes use."""
    spec = make_spec()
    eid = _seed(spec)
    cid = _cid(spec)
    _claim(spec, cid, doc="deck-A", chunk="deck-A#0", doc_visibility="private")

    page = entity_page(spec, eid, as_user="alice")
    assert page.claims == []

    # the owner still sees it — hidden by permission, not lost
    owner_page = entity_page(spec, eid, as_user="bob")
    assert len(owner_page.claims) == 1
