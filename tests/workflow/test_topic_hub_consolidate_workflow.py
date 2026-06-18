"""The Topic Hub ``→consolidate`` workflow (P13, §12) — rewrite the Hub's memory:
merge / summarise / drop stale. Run-triggered, self-referential (last-write-wins on
memory/). Driven through the file-path-loaded run() with a fake agent turn."""

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.discovery import load_run_callable, validate_workflow_profiles
from workspace_app.workflow.handle import WorkflowHandle


def _run():
    return load_run_callable("topic-hub", "default", "consolidate")


async def test_consolidate_workflow_is_discovered_and_coherent():
    validate_workflow_profiles("topic-hub")
    fn = _run()
    assert callable(fn) and fn.__name__ == "run"


async def test_consolidate_rewrites_memory_dropping_stale_entries():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("MEMORY.md", "# Memory\n- fact A\n- STALE fact B (superseded)")
    await wf.write("memory/notes.md", "detail about A")

    async def drive_turn(prompt, tools):
        # The agent consolidates: keep A, drop the stale line (last-write-wins).
        await wf.write("MEMORY.md", "# Memory\n- fact A")
        return "done"

    wf.drive_turn = drive_turn
    result = await run(wf, {})
    assert result == {"status": "done", "notes": 1}
    mem = await wf.read_text("MEMORY.md")
    assert "fact A" in mem
    assert "STALE" not in mem  # the superseded entry was dropped


async def test_consolidate_folds_recent_context_into_the_prompt():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("MEMORY.md", "# Memory")
    captured: list[str] = []

    async def drive_turn(prompt, tools):
        captured.append(prompt)
        await wf.write("MEMORY.md", "# Memory\nconsolidated")
        return "done"

    wf.drive_turn = drive_turn
    await run(wf, {"context": "RECENT-CHAT-TOKEN"})
    assert "RECENT-CHAT-TOKEN" in captured[0]  # the optional recent-chat input is folded in
