"""Topic Hub collection tools (§5/§7) — `resolve_collection` (and, later,
`lookup_glossary`) query specstar resources via `ctx.spec`, not a retriever."""

from __future__ import annotations

import json

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, build_tools, resolve_collection_impl
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection


def _coll(spec, name: str) -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


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
