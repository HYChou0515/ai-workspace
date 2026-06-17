"""The Topic Hub ``→memory`` workflow (P11, §12) — digest uploads into ``memory/*.md``
and refresh ``MEMORY.md``. Driven directly through the file-path-loaded ``run()`` with
a fake agent turn (the live LLM exercise is a separate canned check)."""

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.discovery import load_run_callable, validate_workflow_profiles
from workspace_app.workflow.handle import WorkflowHandle


def _run():
    return load_run_callable("topic-hub", "default", "memory")


async def test_memory_workflow_is_discovered_and_coherent():
    validate_workflow_profiles("topic-hub")  # boot-time: run.py loads + phase ids present
    fn = _run()
    assert callable(fn) and fn.__name__ == "run"


async def test_memory_workflow_digests_uploads_then_refreshes_the_index():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        if len(calls) == 1:  # the single upload's digest node writes its note
            await wf.write("memory/inputs_doc.md", "Key fact: the oven runs at 250C.")
        else:  # the index node rewrites MEMORY.md
            await wf.write("MEMORY.md", "# Memory\n- [oven](memory/inputs_doc.md)")
        return "done"

    wf.drive_turn = drive_turn
    await wf.write("inputs/doc.txt", b"the oven runs at 250C during reflow")
    await wf.write("inputs/input.json", b"{}")

    result = await run(wf, {})
    assert result == {"status": "done", "notes": 1}
    assert "oven" in await wf.read_text("memory/inputs_doc.md")
    assert "Memory" in await wf.read_text("MEMORY.md")
    assert len(calls) == 2  # one digest + one index turn


async def test_memory_workflow_rerun_skips_completed_steps():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        if len(calls) == 1:
            await wf.write("memory/inputs_doc.md", "note")
        else:
            await wf.write("MEMORY.md", "# Memory")
        return "done"

    wf.drive_turn = drive_turn
    await wf.write("inputs/doc.txt", b"content")
    await wf.write("inputs/input.json", b"{}")
    await run(wf, {})
    before = len(calls)
    await run(wf, {})  # re-run — every step is already journaled
    assert len(calls) == before  # no new agent turns (steps skipped, §9)


async def test_memory_workflow_is_empty_with_no_uploads():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("inputs/input.json", b"{}")
    assert await run(wf, {}) == {"status": "empty", "notes": 0}
