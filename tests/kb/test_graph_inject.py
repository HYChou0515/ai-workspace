"""#633 P3 — the block that rides along with a question, and its limits.

Everything here is a bound. An injected block competes for the same context the
user's own documents need, so the rules are: only entities with something to
SAY get in, each contributes a few facts, the block stops at a ceiling, and
every one of those cuts is stated in the text rather than applied silently — a
block that quietly dropped half its content reads exactly like a complete one.
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.kb.graph.inject import entity_block
from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.name_index import NameIndex
from workspace_app.kb.graph.normalize import norm_attribute, norm_surface
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim, GraphEntity, GraphMention, mention_id
from workspace_app.resources.kb import Collection


def _collection(spec: SpecStar, *, private: bool = False) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        return crm.create(
            Collection(name="c", permission=Permission(visibility="private") if private else None)
        ).resource_id


def _mention(spec: SpecStar, cid: str, surface: str, *, doc="deck-A", kind="", private=False):
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        mrm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id=doc,
                surface=surface,
                norm_surface=norm_surface(surface),
                kind=kind,
                norm_kind=norm_surface(kind),
                occurrences=1,
                chunk_ids=[f"{doc}#0"],
                collection_visibility="private" if private else "public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id(doc, surface),
        )


def _claim(
    spec, cid, subject, attribute, value, *, doc="deck-A", unit="", period="", private=False
):
    rm = spec.get_resource_manager(GraphClaim)
    with rm.using("bob"):
        rm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id=doc,
                chunk_id=f"{doc}#0",
                norm_subject=norm_surface(subject),
                subject=subject,
                norm_attribute=norm_attribute(attribute),
                attribute=attribute,
                value=value,
                norm_value=norm_surface(value),
                unit=unit,
                period=period,
                collection_visibility="private" if private else "public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        )


def _index(spec: SpecStar) -> NameIndex:
    erm = spec.get_resource_manager(GraphEntity)
    names: dict[str, tuple[str, ...]] = {}
    for r in erm.list_resources():
        e = r.data
        assert isinstance(e, GraphEntity)
        for k in e.norm_keys:
            names[k] = names.get(k, ()) + (r.info.resource_id,)  # ty: ignore[unresolved-attribute]
    return NameIndex(names)


def test_a_named_thing_with_facts_arrives_with_them():
    spec = make_spec()
    cid = _collection(spec)
    _mention(spec, cid, "回焊爐", kind="機台")
    _claim(spec, cid, "回焊爐", "良率", "98.7", unit="%", period="Q3")
    _claim(spec, cid, "回焊爐", "POR recipe", "PPOOIXUX")
    link_identical_mentions(spec)

    block = entity_block(spec, _index(spec), "回焊爐是什麼?", as_user="alice")

    assert "回焊爐" in block
    assert "良率" in block and "98.7%" in block and "Q3" in block
    assert "POR recipe" in block and "PPOOIXUX" in block
    assert "deck-A" in block  # every line carries where it came from


def test_a_name_with_nothing_to_say_is_not_injected():
    """The rule that keeps 「機台」/「良率」 — generic short names the extractor
    mints by the hundred — from riding along with every single question."""
    spec = make_spec()
    cid = _collection(spec)
    _mention(spec, cid, "機台", kind="")
    link_identical_mentions(spec)

    assert entity_block(spec, _index(spec), "這台機台怎麼了?", as_user="alice") == ""


def test_a_question_naming_nothing_produces_nothing():
    spec = make_spec()
    cid = _collection(spec)
    _mention(spec, cid, "回焊爐", kind="機台")
    _claim(spec, cid, "回焊爐", "良率", "98.7")
    link_identical_mentions(spec)

    assert entity_block(spec, _index(spec), "今天天氣如何?", as_user="alice") == ""


def test_facts_beyond_the_per_entity_limit_are_counted_not_dropped_quietly():
    spec = make_spec()
    cid = _collection(spec)
    _mention(spec, cid, "回焊爐", kind="機台")
    for i in range(20):
        _claim(spec, cid, "回焊爐", f"參數{i}", str(i))
    link_identical_mentions(spec)

    block = entity_block(spec, _index(spec), "回焊爐?", as_user="alice", max_facts=5)

    assert block.count("參數") == 5
    assert "15" in block  # "…and 15 more" — the reader is told what is missing


def test_an_unreadable_thing_never_rides_along():
    """Injection is a read like any other. It must not become the one channel
    that leaks a name or a figure the caller cannot open."""
    spec = make_spec()
    cid = _collection(spec, private=True)
    _mention(spec, cid, "回焊爐", kind="機台", private=True)
    _claim(spec, cid, "回焊爐", "良率", "98.7", private=True)
    link_identical_mentions(spec)
    idx = _index(spec)

    assert entity_block(spec, idx, "回焊爐?", as_user="alice") == ""
    assert "98.7" in entity_block(spec, idx, "回焊爐?", as_user="bob")


def test_an_ambiguous_name_says_so_rather_than_picking_one():
    """Two things can share a name (concurrent pods, a merge tombstone, two
    collections). Picking the first silently answers about the wrong one and
    nobody finds out."""
    spec = make_spec()
    cid = _collection(spec)
    erm = spec.get_resource_manager(GraphEntity)
    ids = []
    for suffix in ("a", "b"):
        _mention(spec, cid, "PPOO", doc=f"deck-{suffix}")
        _claim(spec, cid, "PPOO", "維護單位", f"部門{suffix}", doc=f"deck-{suffix}")
    link_identical_mentions(spec)
    # force the ambiguity the vocabulary layer would normally prevent
    with erm.using("bob"):
        ids.append(
            erm.create(
                GraphEntity(canonical_name="PPOO", norm_keys=["ppoo"], collection_ids=[cid])
            ).resource_id
        )

    block = entity_block(spec, _index(spec), "PPOO 是什麼?", as_user="alice")

    assert "PPOO" in block
    assert "2" in block  # the count of things sharing the name is stated
