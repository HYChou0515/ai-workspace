"""P3 (#435) — M1-AI cross-origin dedup for create_entity: when journal-first self-dedup
finds nothing, the model matches the new entity against ones an OTHER origin filed. A
match is enriched NON-DESTRUCTIVELY (fenced block + fill-empty frontmatter), never
clobbering the human's fields. fail-open: any AI failure/hallucination → NEW (决议8).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.handle import WorkflowHandle


async def _wf(ask_llm: Callable[[str], Awaitable[str]] | None) -> WorkflowHandle:
    store = MemoryFileStore()
    await store.write(
        "ws",
        "/.entity/issue/schema.yaml",
        b"path: issues\nfields:\n  title: {role: text}\n  status: {role: status}\n",
    )
    await store.write(
        "ws",
        "/.entity/issue/skeleton.md",
        b"---\ntitle: {{arg.title}}\nstatus: {{arg.status?}}\n---\n",
    )
    return WorkflowHandle(
        store=store, workspace_id="ws", workflow_id="pm", user="alice", ask_llm=ask_llm
    )


async def _human_issue(wf: WorkflowHandle, body: bytes) -> None:
    """A record filed by another origin (a human) — written directly, so it has NO
    ``created.json`` self-memory for any workflow site."""
    await wf._store.write("ws", "/issues/1.md", body)  # type: ignore[attr-defined]


async def test_cross_match_enriches_via_fenced_block_without_clobbering() -> None:
    async def ask(_prompt: str) -> str:
        return "1"

    wf = await _wf(ask)
    await _human_issue(wf, b"---\ntitle: Login 500s\nstatus: open\n---\nHuman notes here.\n")

    n = await wf.create_entity("issue", {"title": "Login broken"}, name="wf_bug")
    assert n == 1  # matched the existing one, no #2
    store = wf._store  # type: ignore[attr-defined]
    assert not await store.exists("ws", "/issues/2.md")
    body = (await store.read("ws", "/issues/1.md")).decode()
    assert "title: Login 500s" in body  # human's title NOT clobbered
    assert "Human notes here." in body  # human's prose preserved
    assert "<!-- wf:wf_bug begin -->" in body  # workflow owns a fenced block
    assert "Login broken" in body  # the workflow's contribution is inside it


async def test_cross_merge_is_idempotent_across_revise() -> None:
    async def ask(_prompt: str) -> str:
        return "1"

    wf = await _wf(ask)
    await _human_issue(wf, b"---\ntitle: Login 500s\nstatus: open\n---\nnotes\n")

    await wf.create_entity("issue", {"title": "Login broken"}, name="wf_bug")
    await wf.create_entity("issue", {"title": "Login broken v2"}, name="wf_bug")  # a revise
    store = wf._store  # type: ignore[attr-defined]
    body = (await store.read("ws", "/issues/1.md")).decode()
    assert body.count("<!-- wf:wf_bug begin -->") == 1  # replaced, not accumulated
    assert "Login broken v2" in body and "Login broken\n" not in body


async def test_cross_merge_fills_only_empty_frontmatter() -> None:
    async def ask(_prompt: str) -> str:
        return "1"

    wf = await _wf(ask)
    await _human_issue(wf, b"---\ntitle: Login 500s\n---\nnotes\n")  # no status set

    await wf.create_entity("issue", {"title": "Login broken", "status": "triaged"}, name="wf_bug")
    store = wf._store  # type: ignore[attr-defined]
    body = (await store.read("ws", "/issues/1.md")).decode()
    assert "title: Login 500s" in body  # non-empty human field kept
    assert "status: triaged" in body  # empty field filled


async def test_hallucinated_id_falls_open_to_new() -> None:
    async def ask(_prompt: str) -> str:
        return "999"  # not a candidate

    wf = await _wf(ask)
    await _human_issue(wf, b"---\ntitle: Login 500s\nstatus: open\n---\nnotes\n")

    n = await wf.create_entity("issue", {"title": "Unrelated thing"}, name="wf_bug")
    assert n == 2  # fail-open → NEW → a fresh entity, never merged into a bogus id
    store = wf._store  # type: ignore[attr-defined]
    assert await store.exists("ws", "/issues/2.md")


async def test_ai_error_falls_open_to_new() -> None:
    async def ask(_prompt: str) -> str:
        raise RuntimeError("llm down")

    wf = await _wf(ask)
    await _human_issue(wf, b"---\ntitle: Login 500s\nstatus: open\n---\nnotes\n")

    n = await wf.create_entity("issue", {"title": "Login broken"}, name="wf_bug")
    assert n == 2  # AI failure never blocks a legitimate create (决议8)


async def test_no_llm_wired_skips_cross_dedup() -> None:
    wf = await _wf(None)  # no AI classifier
    await _human_issue(wf, b"---\ntitle: Login 500s\nstatus: open\n---\nnotes\n")

    n = await wf.create_entity("issue", {"title": "Login broken"}, name="wf_bug")
    assert n == 2  # cross-origin dedup inert without an LLM; self-dedup unaffected


async def test_cross_match_with_no_existing_candidates_creates() -> None:
    async def ask(_prompt: str) -> str:  # pragma: no cover - never called (no candidates)
        return "1"

    wf = await _wf(ask)  # empty workspace, no existing issues
    n = await wf.create_entity("issue", {"title": "First"}, name="wf_bug")
    assert n == 1
