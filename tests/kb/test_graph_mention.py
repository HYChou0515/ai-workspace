"""#534 B — the primary layer: one row per (document, surface).

A mention is evidence and is never rewritten by a later judgement. Its identity
is content-addressed — the same document saying the same thing again lands on the
same row — so re-extraction is idempotent and the links the vocabulary layer will
hang off it survive a re-run. That stability is the whole reason the two-layer
split works: merging becomes a link, and a link that survives re-extraction can be
undone without losing anything.
"""

from __future__ import annotations

from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from workspace_app.kb.graph.normalize import norm_surface
from workspace_app.perm import Permission
from workspace_app.perm.model import Visibility
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphMention, mention_id
from workspace_app.resources.kb import Collection


class TestNormSurface:
    """Surface noise only — and LESS aggressive than the metric rule, on purpose."""

    def test_width_case_and_whitespace_are_noise(self):
        assert norm_surface("  Ｒｅｆｌｏｗ   Oven ") == norm_surface("reflow oven")
        assert norm_surface("回　焊　爐") == norm_surface("回焊爐")

    def test_a_parenthetical_is_kept_unlike_a_metric_name(self):
        """ "Revenue (USD)" carries a unit, so the metric rule drops the bracket.
        "回焊爐(Reflow Oven)" carries an ALIAS — the document stating its own
        equivalence, which is the strongest evidence the vocabulary layer has.
        Dropping it here would merge the two surfaces into one row and destroy
        exactly that evidence before anything could read it."""
        assert norm_surface("回焊爐(Reflow Oven)") != norm_surface("回焊爐")

    def test_digits_are_never_touched(self):
        """RO-3 and RO-4 are different machines; RO-3 and RO-03 might not be. The
        rule declines to decide either way — it only removes noise, and a digit is
        never noise."""
        assert norm_surface("RO-3") != norm_surface("RO-4")
        assert norm_surface("RO-3") != norm_surface("RO-03")


class TestMentionIdentity:
    def test_the_same_document_and_surface_land_on_one_row(self):
        assert mention_id("deck-A", "Reflow Oven") == mention_id("deck-A", "  reflow   oven ")

    def test_a_different_document_is_a_different_row(self):
        """Evidence stays attached to where it came from. Whether two documents
        mean the same thing is the vocabulary layer's decision, made as a link."""
        assert mention_id("deck-A", "RO-3") != mention_id("deck-B", "RO-3")

    def test_the_id_carries_no_slash(self):
        """specstar ids cannot hold "/" and a surface can hold anything."""
        assert "/" not in mention_id("deck-A", "a/b (c) 回焊/爐")


def _seed(
    spec: SpecStar,
    *,
    visibility: Visibility = "public",
    read: list[str] | None = None,
) -> str:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(
            Collection(
                name="c",
                permission=Permission(
                    visibility=visibility, read_meta=read or [], read_content=read or []
                ),
            )
        ).resource_id
    rm = spec.get_resource_manager(GraphMention)
    with rm.using("bob"):
        rm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id="deck-A",
                surface="回焊爐",
                norm_surface=norm_surface("回焊爐"),
                kind="機台",
                collection_visibility=visibility,
                collection_read_meta=read or [],
                collection_read_content=read or [],
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id("deck-A", "回焊爐"),
        )
    return cid


def test_a_mention_is_hidden_with_the_deck_it_came_from():
    """A mention is content — it says what a document talks about. It rides the
    SAME rule as a claim, so the two share one scope rather than two that could
    drift apart."""
    spec = make_spec(default_user=lambda: "bob")
    _seed(spec, visibility="restricted", read=[])
    rm = spec.get_resource_manager(GraphMention)
    with rm.using("alice", apply_access_scope=True):  # ty: ignore[unknown-argument]
        assert list(rm.list_resources(QB.all().build())) == []
    with rm.using("bob", apply_access_scope=True):  # ty: ignore[unknown-argument]
        assert len(list(rm.list_resources(QB.all().build()))) == 1


def test_a_mention_with_no_permission_mirror_is_hidden():
    """Fail closed, exactly as a claim does: a writer that forgets the mirror
    loses rows (loud) rather than publishing them (silent)."""
    spec = make_spec(default_user=lambda: "bob")
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    rm = spec.get_resource_manager(GraphMention)
    with rm.using("bob"):
        rid = rm.create(
            GraphMention(collection_id=cid, source_doc_id="deck-A", surface="回焊爐")
        ).resource_id
    with rm.using("bob", apply_access_scope=True):  # ty: ignore[unknown-argument]
        try:
            rm.get(rid)
        except ResourceIDNotFoundError:
            return
    raise AssertionError("a mention with no mirror was visible")
