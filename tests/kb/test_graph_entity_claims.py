"""#630 — a statement lands on the thing it NAMES, not the slide it shared.

#628 filed a figure by co-location: the claim carried no subject, so the only
handle available was "it was on a slide this entity was mentioned on". That is a
guess, and it gets worse the more a slide talks about — a deck covering ten
machines smeared every number across all ten.

Since #630 the extraction records whose attribute it is, so the binding is what
the passage said: ``norm_subject`` meets the entity's ``norm_keys``. The two
tests below are deliberately the cases where the old rule and the new one give
OPPOSITE answers — a claim about this thing on a slide with no mention of it
(co-location said no, the subject says yes), and a claim about something else on
the very slide the mention sits on (co-location said yes, the subject says no).
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.normalize import norm_attribute, norm_surface
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
    subject: str,
    doc: str = "deck-A",
    chunk: str = "deck-A#0",
    attribute: str = "良率",
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
                norm_subject=norm_surface(subject),
                subject=subject,
                norm_attribute=norm_attribute(attribute),
                attribute=attribute,
                value=value,
                norm_value=norm_surface(value),
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


def test_a_statement_about_this_thing_lands_even_from_a_slide_that_never_names_it():
    """Co-location said no; the subject says yes. A summary slide can state a
    machine's yield without repeating its name in a form the mention captured."""
    spec = make_spec()
    eid = _seed(spec)
    _claim(spec, _cid(spec), subject="回焊爐", chunk="deck-A#7")

    page = entity_page(spec, eid, as_user="alice")

    assert len(page.claims) == 1
    assert page.claims[0].subject == "回焊爐"
    assert page.claims[0].value == "98.7"


def test_a_statement_about_something_else_stays_off_even_on_the_very_same_slide():
    """Co-location said yes; the subject says no. This is the smearing #630 kills:
    one slide, ten machines, ten numbers — each belongs to exactly one of them."""
    spec = make_spec()
    eid = _seed(spec)
    _claim(spec, _cid(spec), subject="產線三", chunk="deck-A#0")

    page = entity_page(spec, eid, as_user="alice")

    assert page.claims == []


def test_the_subject_is_matched_the_way_names_are_matched():
    """Typing noise is not identity — the subject key is `norm_surface`, the same
    rule the entity's own keys are built with, which is what lets them meet."""
    spec = make_spec()
    eid = _seed(spec)
    _claim(spec, _cid(spec), subject="回 焊 爐")

    page = entity_page(spec, eid, as_user="alice")

    assert len(page.claims) == 1


def test_a_statement_from_an_unreadable_document_never_arrives():
    """The claim's own access scope filters it — the same one the auto routes use."""
    spec = make_spec()
    eid = _seed(spec)
    _claim(spec, _cid(spec), subject="回焊爐", doc_visibility="private")

    assert entity_page(spec, eid, as_user="alice").claims == []
    # the owner still sees it — hidden by permission, not lost
    assert len(entity_page(spec, eid, as_user="bob").claims) == 1
