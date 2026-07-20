"""`query_entity` grows with the entity store, so it pages like `read_file`.

Listing "every record of a type with all its fields" is fine at 5 issues and a
context bomb at 2000 — and the tool is the one an agent is told to call before
it creates or updates anything.
"""

from __future__ import annotations

import json

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, create_entity_impl, query_entity_impl
from workspace_app.filestore.memory import MemoryFileStore

_SCHEMA = b"path: issues\nfields:\n  title: { role: text, required: true }\n"
_SKELETON = b"---\ntitle: {{arg.title}}\n---\n\n{{arg.body?}}\n"


async def _ctx(cap: int = 30_000) -> RunContextWrapper[AgentToolContext]:
    fs = MemoryFileStore()
    await fs.write("ws", "/.entity/issue/schema.yaml", _SCHEMA)
    await fs.write("ws", "/.entity/issue/skeleton.md", _SKELETON)
    return RunContextWrapper(
        AgentToolContext(
            investigation_id="ws", filestore=fs, acting_user="alice", exec_output_max_chars=cap
        )
    )


async def _seed(ctx: RunContextWrapper[AgentToolContext], n: int) -> None:
    for i in range(n):
        await create_entity_impl(ctx, "issue", {"title": f"issue {i}"})


async def test_a_small_store_is_returned_whole_with_its_total():
    ctx = await _ctx()
    await _seed(ctx, 3)

    payload = json.loads(await query_entity_impl(ctx, "issue"))

    assert payload["total"] == 3
    assert [e["number"] for e in payload["entities"]] == [1, 2, 3]
    assert "next_offset" not in payload


async def test_a_big_store_returns_one_page_and_says_where_to_resume():
    ctx = await _ctx()
    await _seed(ctx, 60)

    payload = json.loads(await query_entity_impl(ctx, "issue", limit=10))

    assert payload["total"] == 60
    assert [e["number"] for e in payload["entities"]] == list(range(1, 11))
    assert payload["next_offset"] == 11


async def test_offset_resumes_from_where_the_previous_page_stopped():
    ctx = await _ctx()
    await _seed(ctx, 12)

    payload = json.loads(await query_entity_impl(ctx, "issue", offset=11, limit=10))

    assert [e["number"] for e in payload["entities"]] == [11, 12]
    assert "next_offset" not in payload  # the last page says so by omission


async def test_a_page_that_is_wide_rather_than_long_still_fits_the_budget():
    """The record COUNT is not the thing that overflows a context — a handful of
    records with huge fields does it just as well, so the page also stops on the
    character budget and reports the shorter page it actually returned."""
    ctx = await _ctx(cap=2_000)
    for i in range(20):
        await create_entity_impl(ctx, "issue", {"title": "x" * 500 + str(i)})

    out = await query_entity_impl(ctx, "issue")
    payload = json.loads(out)

    assert len(out) < 6_000
    assert 0 < len(payload["entities"]) < 20
    assert payload["total"] == 20
    assert payload["next_offset"] == len(payload["entities"]) + 1
