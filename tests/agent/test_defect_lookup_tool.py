"""#513 P1 — the ``lookup_defect`` agent tool: a deterministic, station-scoped
defect-library lookup beside ``lookup_glossary``. It resolves a code within a
caller-supplied scope chain (most-specific first) via ``defect_library.resolve``
and surfaces the matched entry as authoritative context."""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, build_tools, lookup_defect_impl
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.kb.defect_library import scope_key
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard


def _coll(spec, name: str = "Defects") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _card(spec, cid: str, keys: list[str], body: str) -> str:
    return (
        spec.get_resource_manager(ContextCard)
        .create(
            ContextCard(
                collection_id=cid,
                keys=keys,
                norm_keys=derive_norm_keys(keys),
                title=keys[0],
                body=body,
            )
        )
        .resource_id
    )


def test_lookup_defect_surfaces_the_scoped_entry():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    _card(spec, cid, [scope_key("etch", "M4")], "Bridge morphology at etch.")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    out = lookup_defect_impl(ctx, "M4", ["etch"])
    assert "Bridge morphology at etch." in out  # the entry body is surfaced as authoritative


def test_lookup_defect_prefers_the_machine_override():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    _card(spec, cid, [scope_key("etch", "M4")], "shared etch entry")
    _card(spec, cid, [scope_key("etchtool07", "M4")], "tool-07 override")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    out = lookup_defect_impl(ctx, "M4", ["etchtool07", "etch"])
    assert "tool-07 override" in out
    assert "shared etch entry" not in out  # the broader entry is not surfaced


def test_lookup_defect_reports_a_miss():
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    assert "No defect entry found" in lookup_defect_impl(ctx, "M4", ["etch"])


def test_lookup_defect_without_a_spec_errors():
    ctx = RunContextWrapper(AgentToolContext(collection_ids=["c1"]))
    out = lookup_defect_impl(ctx, "M4", ["etch"])
    assert out.startswith("error:")
    assert "collection-scoped context" in out


def test_lookup_defect_is_a_buildable_tool():
    assert "lookup_defect" in {t.name for t in build_tools(["lookup_defect"])}


def test_lookup_defect_surfaces_the_card_id_for_read_before_write():
    # The flywheel updates entries, so the lookup output must carry the hit's id.
    spec = make_spec(default_user="u")
    cid = _coll(spec)
    rid = _card(spec, cid, [scope_key("etch", "M4")], "bridge")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    assert rid in lookup_defect_impl(ctx, "M4", ["etch"])
