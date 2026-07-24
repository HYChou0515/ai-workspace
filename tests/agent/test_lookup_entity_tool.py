"""#628 — `lookup_entity`: the KB agent's deterministic graph-dossier tool.

Same family as `lookup_glossary`: exact-key, zero LLM, reads via `ctx.spec` AS
the acting user. The dossier assembly itself is tested in
tests/kb/test_graph_lookup.py; here we prove the tool wrapper — context
plumbing, the no-spec error, and that the tool is buildable."""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, build_tools, lookup_entity_impl
from workspace_app.kb.graph.link import link_identical_mentions
from workspace_app.kb.graph.normalize import norm_metric, norm_surface
from workspace_app.resources import make_spec
from workspace_app.resources.graph import GraphClaim, GraphMention, mention_id
from workspace_app.resources.kb import Collection


def _seed(spec) -> None:
    crm = spec.get_resource_manager(Collection)
    with crm.using("bob"):
        cid = crm.create(Collection(name="c")).resource_id
    mrm = spec.get_resource_manager(GraphMention)
    with mrm.using("bob"):
        mrm.create(
            GraphMention(
                collection_id=cid,
                source_doc_id="deck-A",
                surface="回焊爐",
                norm_surface=norm_surface("回焊爐"),
                occurrences=2,
                chunk_ids=["deck-A#0"],
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            ),
            resource_id=mention_id("deck-A", "回焊爐"),
        )
    link_identical_mentions(spec)
    grm = spec.get_resource_manager(GraphClaim)
    with grm.using("bob"):
        grm.create(
            GraphClaim(
                collection_id=cid,
                source_doc_id="deck-A",
                chunk_id="deck-A#0",
                norm_metric=norm_metric("良率"),
                metric="良率",
                value="98.7",
                period="Q3",
                norm_period="q3",
                unit="%",
                collection_visibility="public",
                collection_created_by="bob",
                doc_visibility="public",
            )
        )


def test_the_tool_returns_the_dossier_as_the_acting_user():
    spec = make_spec(default_user="u")
    _seed(spec)
    ctx = RunContextWrapper(AgentToolContext(spec=spec, acting_user="alice"))
    card = lookup_entity_impl(ctx, "回焊爐")
    assert "回焊爐" in card
    assert "98.7" in card
    assert "deck-A" in card


def test_the_tool_without_a_spec_errors_plainly():
    ctx = RunContextWrapper(AgentToolContext())
    assert "error" in lookup_entity_impl(ctx, "anything")


def test_lookup_entity_is_a_buildable_tool():
    assert "lookup_entity" in {t.name for t in build_tools(["lookup_entity"])}
