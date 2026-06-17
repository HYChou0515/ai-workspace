"""Topic Hub collection tools (§5/§7) — `resolve_collection` and `lookup_glossary`
query specstar resources via `ctx.spec` (no retriever), not the KB pipeline."""

from __future__ import annotations

import json

from agents import RunContextWrapper

from workspace_app.agent import (
    AgentToolContext,
    build_tools,
    lookup_glossary_impl,
    resolve_collection_impl,
)
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard


def _coll(spec, name: str) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _card(spec, cid: str, keys: list[str], body: str) -> None:
    spec.get_resource_manager(ContextCard).create(
        ContextCard(
            collection_id=cid, keys=keys, norm_keys=derive_norm_keys(keys), title=keys[0], body=body
        )
    )


# ── resolve_collection ────────────────────────────────────────────────────


def test_resolve_collection_tool_resolves_via_ctx_spec():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    ctx = RunContextWrapper(AgentToolContext(spec=spec))
    assert json.loads(resolve_collection_impl(ctx, "Defects")) == {
        "status": "ok",
        "id": cid,
        "name": "Defects",
    }


def test_resolve_collection_tool_without_a_spec_errors():
    ctx = RunContextWrapper(AgentToolContext())  # no Hub spec wired
    assert "Topic Hub turn" in resolve_collection_impl(ctx, "anything")


def test_resolve_collection_is_a_buildable_tool():
    assert "resolve_collection" in {t.name for t in build_tools(["resolve_collection"])}


# ── lookup_glossary ───────────────────────────────────────────────────────


def test_lookup_glossary_returns_matching_card_as_authoritative_context():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    _card(spec, cid, ["M4"], "Metal layer 4.")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    out = lookup_glossary_impl(ctx, "what does M4 mean?")
    assert "Metal layer 4." in out  # the card body is surfaced as authoritative


def test_lookup_glossary_reports_a_miss():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    assert "No glossary entries found" in lookup_glossary_impl(ctx, "nothing relevant here")


def test_lookup_glossary_without_a_spec_errors():
    ctx = RunContextWrapper(AgentToolContext(collection_ids=["c1"]))
    assert "Topic Hub turn" in lookup_glossary_impl(ctx, "x")


def test_lookup_glossary_is_a_buildable_tool():
    assert "lookup_glossary" in {t.name for t in build_tools(["lookup_glossary"])}
