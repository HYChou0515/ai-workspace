"""Topic Hub collection tools (§5/§7) — `resolve_collection` and `lookup_glossary`
query specstar resources via `ctx.spec` (no retriever), not the KB pipeline."""

from __future__ import annotations

import json

from agents import RunContextWrapper

from workspace_app.agent import (
    AgentToolContext,
    build_tools,
    create_context_card_impl,
    lookup_glossary_impl,
    resolve_collection_impl,
    update_context_card_impl,
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
    out = lookup_glossary_impl(ctx, "x")
    assert out.startswith("error:")
    assert "collection-scoped context" in out


def test_lookup_glossary_is_a_buildable_tool():
    assert "lookup_glossary" in {t.name for t in build_tools(["lookup_glossary"])}


def test_lookup_glossary_surfaces_the_card_id_for_read_before_write():
    """#111: the agent must be able to target a matched card for update — so the
    lookup output carries each hit's resource id, not just its body."""
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    rid = (
        spec.get_resource_manager(ContextCard)
        .create(
            ContextCard(
                collection_id=cid, keys=["M4"], norm_keys=derive_norm_keys(["M4"]), body="b"
            )
        )
        .resource_id
    )
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid]))
    out = lookup_glossary_impl(ctx, "what does M4 mean?")
    assert rid in out  # the card id is present so update_context_card can target it


# ── update_context_card (agent tool, #111) ────────────────────────────────


def _make_card(spec, cid, keys, body) -> str:
    return (
        spec.get_resource_manager(ContextCard)
        .create(
            ContextCard(collection_id=cid, keys=keys, norm_keys=derive_norm_keys(keys), body=body)
        )
        .resource_id
    )


def test_update_context_card_tool_overwrites_when_expected_body_matches():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    rid = _make_card(spec, cid, ["M4"], "old")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid], acting_user="alice"))
    out = update_context_card_impl(
        ctx, card_id=rid, keys=["M4"], title="", body="merged new", expected_body="old"
    )
    assert spec.get_resource_manager(ContextCard).get(rid).data.body == "merged new"
    assert rid in out  # confirms which card was updated


def test_update_context_card_tool_blocks_on_stale_expected_body():
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    rid = _make_card(spec, cid, ["M4"], "current")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid], acting_user="alice"))
    out = update_context_card_impl(
        ctx, card_id=rid, keys=["M4"], title="", body="x", expected_body="STALE"
    )
    assert out.startswith("error:")  # returned, not raised
    assert "re-read" in out.lower() or "changed" in out.lower()
    assert spec.get_resource_manager(ContextCard).get(rid).data.body == "current"  # untouched


def test_update_context_card_tool_errors_on_missing_id():
    spec = make_spec(default_user="u")
    _coll(spec, "Defects")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[], acting_user="alice"))
    out = update_context_card_impl(
        ctx, card_id="no-such", keys=["x"], title="", body="b", expected_body=""
    )
    assert out.startswith("error:")
    assert "no-such" in out or "not found" in out.lower()


def test_update_context_card_tool_without_a_spec_errors():
    ctx = RunContextWrapper(AgentToolContext(acting_user="alice"))
    out = update_context_card_impl(
        ctx, card_id="x", keys=["k"], title="", body="b", expected_body=""
    )
    assert out.startswith("error:")


def test_update_context_card_is_a_buildable_tool():
    assert "update_context_card" in {t.name for t in build_tools(["update_context_card"])}


# ── create_context_card (agent tool, #111) ────────────────────────────────


def test_create_context_card_tool_creates_a_findable_card():
    from workspace_app.kb.context_cards import lookup

    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid], acting_user="alice"))
    out = create_context_card_impl(
        ctx, collection="Defects", keys=["TDDB"], title="TDDB", body="time-dependent breakdown"
    )
    hits = lookup(spec, cid, ["tddb"])["tddb"]
    assert [c.body for c in hits] == ["time-dependent breakdown"]
    assert "error" not in out.lower()


def test_create_context_card_tool_refuses_when_an_exact_key_already_exists():
    """#111: same-term-same-meaning should be an update, not a duplicate — so create
    refuses an existing key and points the AI at the existing card id."""
    spec = make_spec(default_user="u")
    cid = _coll(spec, "Defects")
    rid = _make_card(spec, cid, ["M4"], "existing")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[cid], acting_user="alice"))
    out = create_context_card_impl(ctx, collection="Defects", keys=["M4"], title="", body="dupe")
    assert out.startswith("error:")
    assert rid in out  # the AI is told which card to update instead
    # no duplicate was created
    from workspace_app.kb.context_cards import find_cards_by_key

    assert len(find_cards_by_key(spec, cid, "m4")) == 1


def test_create_context_card_tool_unknown_collection_errors():
    spec = make_spec(default_user="u")
    _coll(spec, "Defects")
    ctx = RunContextWrapper(AgentToolContext(spec=spec, collection_ids=[], acting_user="alice"))
    out = create_context_card_impl(ctx, collection="no-such", keys=["x"], title="", body="b")
    assert out.startswith("error:")


def test_create_context_card_tool_without_a_spec_errors():
    ctx = RunContextWrapper(AgentToolContext(acting_user="alice"))
    out = create_context_card_impl(ctx, collection="c", keys=["k"], title="", body="b")
    assert out.startswith("error:")


def test_create_context_card_is_a_buildable_tool():
    assert "create_context_card" in {t.name for t in build_tools(["create_context_card"])}


def test_card_tool_docstrings_explain_key_search_and_markdown_body():
    """#182/#183: the create/update tool descriptions tell the model HOW keys are matched
    (exact membership, so give every alias as its own key) and that the body is markdown —
    so it authors findable, readable cards. (Behaviour is verified live; this locks the
    guidance into the tool descriptions the model actually reads.)"""
    for fn in (create_context_card_impl, update_context_card_impl):
        doc = (fn.__doc__ or "").lower()
        assert "exact" in doc  # exact-membership lookup semantics
        assert "alias" in doc or "surface form" in doc  # ask for multiple keys
        assert "markdown" in doc  # body is markdown (#183)
