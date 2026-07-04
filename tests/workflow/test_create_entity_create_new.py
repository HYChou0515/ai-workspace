"""P4 (#435) — ``create_new`` (M2 token): a fresh entity per invocation. Mechanically it
is ``update`` with M1-AI cross-origin matching turned OFF, so it never dedups against
another origin's entity; WITHIN an invocation the journal-first self-dedup still makes a
revise reuse (never double-create). The cross-invocation "fresh per trigger" needs #429,
so the DSL author surface is gated (tested in test_dsl_create_entity) — these exercise the
handle mechanism directly.
"""

from __future__ import annotations

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.handle import WorkflowHandle


async def _wf(ask_llm=None) -> WorkflowHandle:  # type: ignore[no-untyped-def]
    store = MemoryFileStore()
    await store.write(
        "ws", "/.entity/issue/schema.yaml", b"path: issues\nfields:\n  title: {role: text}\n"
    )
    await store.write("ws", "/.entity/issue/skeleton.md", b"---\ntitle: {{arg.title}}\n---\n")
    return WorkflowHandle(
        store=store, workspace_id="ws", workflow_id="pm", user="alice", ask_llm=ask_llm
    )


async def test_create_new_skips_cross_origin_match_and_mints_fresh() -> None:
    """Even when an existing entity would match, ``create_new`` never cross-matches — it
    mints a fresh one (and the AI classifier is not even consulted)."""
    calls = {"n": 0}

    async def ask(_prompt: str) -> str:
        calls["n"] += 1
        return "1"

    wf = await _wf(ask)
    # a record another origin filed that WOULD match under `update`
    await wf._store.write(  # type: ignore[attr-defined]
        "ws", "/issues/1.md", b"---\ntitle: Login 500s\n---\nnotes\n"
    )
    n = await wf.create_entity(
        "issue", {"title": "Login broken"}, name="daily", on_duplicate="create_new"
    )
    assert n == 2  # a fresh entity, not merged into #1
    assert calls["n"] == 0  # M1-AI cross-match skipped entirely


async def test_create_new_within_invocation_revise_reuses_not_double_creates() -> None:
    """Within one invocation the created.json self-dedup still holds: a revise (same site,
    changed content) reuses the entity minted this invocation, never a second one."""
    wf = await _wf()
    first = await wf.create_entity(
        "issue", {"title": "Report v1"}, name="daily", on_duplicate="create_new"
    )
    assert first == 1
    again = await wf.create_entity(
        "issue", {"title": "Report v2"}, name="daily", on_duplicate="create_new"
    )
    assert again == 1  # reused within the invocation
    store = wf._store  # type: ignore[attr-defined]
    assert not await store.exists("ws", "/issues/2.md")
    assert "Report v2" in (await store.read("ws", "/issues/1.md")).decode()  # revise overlaid
