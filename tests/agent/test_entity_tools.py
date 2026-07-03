"""Agent-facing entity tools (#419 P5) — the AI write path into the file-first
entity framework. They run through the SAME `EntityStore` pipeline as the
quick-create UI + workflows (permanent numbering, skeleton render, lint), so an
AI-authored record is indistinguishable from a UI-authored one; the agent no
longer has to hand-write `issues/N.md` and bypass numbering/validation.
"""

from __future__ import annotations

import json

from agents import RunContextWrapper

from workspace_app.agent import (
    AgentToolContext,
    build_tools,
    create_entity_impl,
    link_entity_impl,
    query_entity_impl,
    update_entity_impl,
)
from workspace_app.filestore.memory import MemoryFileStore

_SCHEMA = (
    b"path: issues\n"
    b"fields:\n"
    b"  title: { role: text, required: true }\n"
    b"  status: { role: status, values: [open, done] }\n"
    b"  milestone: { role: ref, to: milestone }\n"
)
_SKELETON = (
    b"---\n"
    b"title: {{arg.title}}\n"
    b"status: open\n"
    b"milestone: {{arg.milestone?}}\n"
    b"---\n\n"
    b"{{arg.body?}}\n"
)


async def _ctx_with_issue_schema() -> tuple[RunContextWrapper, MemoryFileStore]:
    fs = MemoryFileStore()
    await fs.write("ws", "/.entity/issue/schema.yaml", _SCHEMA)
    await fs.write("ws", "/.entity/issue/skeleton.md", _SKELETON)
    ctx = RunContextWrapper(
        AgentToolContext(investigation_id="ws", filestore=fs, acting_user="alice")
    )
    return ctx, fs


async def test_create_entity_allocates_number_and_reports_it() -> None:
    ctx, _fs = await _ctx_with_issue_schema()
    out = await create_entity_impl(ctx, "issue", {"title": "Login broken"})
    assert "issue #1" in out


async def test_query_entity_lists_created_records_with_fields() -> None:
    ctx, _fs = await _ctx_with_issue_schema()
    await create_entity_impl(ctx, "issue", {"title": "A"})
    await create_entity_impl(ctx, "issue", {"title": "B"})

    payload = json.loads(await query_entity_impl(ctx, "issue"))
    assert [e["number"] for e in payload["entities"]] == [1, 2]
    assert payload["entities"][0]["fields"]["title"] == "A"
    assert payload["invalid"] == []


async def test_update_entity_patches_a_field() -> None:
    ctx, _fs = await _ctx_with_issue_schema()
    await create_entity_impl(ctx, "issue", {"title": "A"})

    out = await update_entity_impl(ctx, "issue", 1, {"status": "done"})
    assert "Updated issue #1" in out
    payload = json.loads(await query_entity_impl(ctx, "issue"))
    assert payload["entities"][0]["fields"]["status"] == "done"


async def test_link_entity_sets_a_reference() -> None:
    ctx, _fs = await _ctx_with_issue_schema()
    await create_entity_impl(ctx, "issue", {"title": "A"})

    out = await link_entity_impl(ctx, "issue", 1, "milestone", 3)
    assert "→ #3" in out
    payload = json.loads(await query_entity_impl(ctx, "issue"))
    assert payload["entities"][0]["fields"]["milestone"] == 3


async def test_update_entity_on_missing_record_returns_error_not_raise() -> None:
    ctx, _fs = await _ctx_with_issue_schema()
    out = await update_entity_impl(ctx, "issue", 99, {"status": "done"})
    assert out.startswith("error:") and "#99" in out


async def test_unknown_type_returns_a_helpful_error_listing_declared_types() -> None:
    ctx, _fs = await _ctx_with_issue_schema()
    out = await create_entity_impl(ctx, "sprint", {"title": "x"})
    assert out.startswith("error:")
    assert "sprint" in out and "issue" in out  # names the bad type + what IS declared


async def test_status_outside_the_closed_vocab_still_creates_but_warns() -> None:
    """§C7 lint-not-block: a status outside the schema's values lands as a
    warning appended to the tool result, not a rejected write."""
    ctx, _fs = await _ctx_with_issue_schema()
    await create_entity_impl(ctx, "issue", {"title": "A"})
    warned = await update_entity_impl(ctx, "issue", 1, {"status": "bogus"})
    assert "Warnings" in warned


def test_entity_tools_are_buildable_by_name() -> None:
    names = {
        t.name
        for t in build_tools(["create_entity", "update_entity", "query_entity", "link_entity"])
    }
    assert names == {"create_entity", "update_entity", "query_entity", "link_entity"}


def test_entity_tools_require_function_workspace() -> None:
    """The entity tools read/write the item's files, so an app that lists one
    without `function.workspace` fails the startup coherence gate (like the file
    tools do) — a mis-configured app can't ship a dead tool."""
    import msgspec
    import pytest

    from workspace_app.apps.catalog import validate_function_coherence
    from workspace_app.apps.manifest import AppManifest

    raw = (
        b'{"slug":"x","title":"X",'
        b'"agent":{"prompt_file":"p.md","tools":["query_entity"]},'
        b'"item":{"noun":"I","noun_plural":"Is"},'
        b'"function":{"workspace":false,"sandbox":false,"terminal":false}}'
    )
    manifest = msgspec.json.decode(raw, type=AppManifest)
    with pytest.raises(ValueError, match="entity tools"):
        validate_function_coherence(manifest)
