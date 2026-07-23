"""#628 — `entity_card`: the dossier behind the KB agent's `lookup_entity` tool.

"What is X" from a slide-heavy corpus is answered by ASSEMBLY, not by a
dictionary sentence: the name (with every spelling), the numbers stated beside
it, every document that mentions it, and what it connects to — each line with
the slide it came from. Deterministic, zero LLM, and it reads AS THE CALLER, so
an identity nothing readable vouches for is simply "not found" (a bare name can
leak) — including in the did-you-mean candidates.
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.graph.link import link_identical_mentions, reconcile_vocabulary
from workspace_app.kb.graph.lookup import entity_card
from workspace_app.kb.graph.normalize import norm_attribute, norm_surface
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import (
    GraphClaim,
    GraphMention,
    GraphRelationship,
    mention_id,
    relationship_id,
)
from workspace_app.resources.kb import Collection


def _seed(spec: SpecStar, *, private: bool = False) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(
                name="c",
                permission=Permission(visibility="private") if private else None,
            )
        ).resource_id
    vis = "private" if private else "public"
    mrm = spec.get_resource_manager(GraphMention)
    # deck-C writes the same name with typing noise (CJK gaps) — one entity,
    # two surfaces. deck-B's "Reflow Oven" is a DIFFERENT norm key, so it stays
    # its own entity (merging those is the alias/merge machinery's job, not
    # this seed's).
    for doc, surface in (
        ("deck-A", "回焊爐"),
        ("deck-B", "Reflow Oven"),
        ("deck-C", "回 焊 爐"),
    ):
        with mrm.using("bob"):
            mrm.create(
                GraphMention(
                    collection_id=cid,
                    source_doc_id=doc,
                    surface=surface,
                    norm_surface=norm_surface(surface),
                    kind="機台",
                    norm_kind=norm_surface("機台"),
                    occurrences=2,
                    chunk_ids=[f"{doc}#0"],
                    collection_visibility=vis,
                    collection_created_by="bob",
                    doc_visibility="public",
                ),
                resource_id=mention_id(doc, surface),
            )
    link_identical_mentions(spec)
    reconcile_vocabulary(spec, llm=None)  # kinds become entities with evidence
    rrm = spec.get_resource_manager(GraphRelationship)
    with rrm.using("bob"):
        rrm.create(
            GraphRelationship(
                collection_id=cid,
                source_doc_id="deck-A",
                subject="回焊爐",
                predicate="造成",
                object="空洞",
                norm_subject=norm_surface("回焊爐"),
                norm_predicate=norm_surface("造成"),
                norm_object=norm_surface("空洞"),
                chunk_id="deck-A#0",
                quote="回焊爐溫度過高造成空洞",
                collection_visibility=vis,
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=relationship_id("deck-A", "deck-A#0", "回焊爐", "造成", "空洞"),
        )
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                chunk_id="deck-A#0",
                norm_subject=norm_surface("回焊爐"),
                subject="回焊爐",
                norm_attribute=norm_attribute("良率"),
                attribute="良率",
                value="98.7",
                period="Q3",
                norm_period="q3",
                unit="%",
                collection_visibility=vis,
                collection_created_by="bob",
                doc_visibility="public",
            )
        )
    return cid


def test_the_card_assembles_name_numbers_documents_and_connections():
    spec = make_spec()
    _seed(spec)

    card = entity_card(spec, "回焊爐", as_user="alice")

    assert "回焊爐" in card
    assert "回 焊 爐" in card  # the other spelling, verbatim as deck-C wrote it
    assert "機台" in card  # the kind, once the vocabulary has run
    # the numbers, verbatim, with slide provenance
    assert "良率" in card
    assert "98.7" in card and "%" in card and "Q3" in card
    # every document that names THIS entity ("Reflow Oven" is its own key)
    assert "deck-A" in card and "deck-C" in card
    # what it connects to, with the sentence that said so
    assert "造成" in card and "空洞" in card
    assert "回焊爐溫度過高造成空洞" in card


def test_lookup_normalises_the_asked_name_like_any_surface():
    """Width, case and stray whitespace are typing noise, not identity."""
    spec = make_spec()
    _seed(spec)
    card = entity_card(spec, "REFLOW  OVEN", as_user="alice")
    assert "Reflow Oven" in card and "deck-B" in card


def test_an_unreadable_identity_is_not_found_and_never_hinted():
    """Unknown and unreadable must look the same — and the did-you-mean list
    must not leak a name the caller cannot open."""
    spec = make_spec()
    _seed(spec, private=True)

    card = entity_card(spec, "回焊爐", as_user="alice")
    assert "not found" in card.lower()
    hint = entity_card(spec, "回焊", as_user="alice")
    assert "回焊爐" not in hint

    # the owner still finds it — hidden by permission, not missing
    assert "98.7" in entity_card(spec, "回焊爐", as_user="bob")


def test_close_names_are_offered_when_nothing_matches_exactly():
    spec = make_spec()
    _seed(spec)

    card = entity_card(spec, "回焊爐 溫度", as_user="alice")
    assert "not found" in card.lower()
    assert "回焊爐" in card  # the near miss, so the agent can re-ask


def test_the_card_answers_who_holds_this_value():
    """#630 P5 — the dossier reads the statement table from both ends, so asking
    about a recipe tells you which machines run it (「PPOO 系列被哪些機台使用」)."""
    from workspace_app.kb.graph.normalize import norm_attribute as _na

    spec = make_spec()
    cid = _seed(spec)
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                chunk_id="deck-A#0",
                norm_subject=norm_surface("回焊爐"),
                subject="回焊爐",
                norm_attribute=_na("recipe"),
                attribute="recipe",
                value="PPOOIXUX",
                norm_value=norm_surface("PPOOIXUX"),
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        )
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        mrm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id="deck-D",
                surface="PPOOIXUX",
                norm_surface=norm_surface("PPOOIXUX"),
                kind="recipe",
                norm_kind=norm_surface("recipe"),
                occurrences=1,
                chunk_ids=["deck-D#0"],
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id("deck-D", "PPOOIXUX"),
        )
    link_identical_mentions(spec)

    card = entity_card(spec, "PPOOIXUX", as_user="alice")

    assert "回焊爐" in card
    assert "recipe" in card
    assert "deck-A" in card
