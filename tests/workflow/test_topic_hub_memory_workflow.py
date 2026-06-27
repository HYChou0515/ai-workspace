"""The Topic Hub ``→memory`` workflow (P11, §12) — digest uploads into ``memory/*.md``
and refresh ``MEMORY.md``. Driven directly through the file-path-loaded ``run()`` with
a fake agent turn (the live LLM exercise is a separate canned check)."""

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.discovery import (
    load_preflight_callable,
    load_run_callable,
    validate_workflow_profiles,
)
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.preflight import Severity, can_run


def _run():
    return load_run_callable("topic-hub", "default", "memory")


def _preflight():
    pf = load_preflight_callable("topic-hub", "default", "memory")
    assert pf is not None
    return pf


async def test_memory_workflow_is_discovered_and_coherent():
    validate_workflow_profiles("topic-hub")  # boot-time: run.py loads + phase ids present
    fn = _run()
    assert callable(fn) and fn.__name__ == "run"


async def test_memory_workflow_digests_uploads_then_refreshes_the_index():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        # Decision/action (#107): the agent REPLIES with the content; the step writes
        # it. The fake just returns the text — it must not write files itself.
        calls.append(prompt)
        if len(calls) == 1:  # the digest node's note content
            return "Key fact: the oven runs at 250C."
        return "# Memory\n- [oven](memory/uploads_doc.md)"  # the index content

    wf.drive_turn = drive_turn
    await wf.write("uploads/doc.txt", b"the oven runs at 250C during reflow")
    await wf.write("uploads/input.json", b"{}")

    result = await run(wf, {})
    assert result == {"status": "done", "notes": 1}
    assert "oven" in await wf.read_text("memory/uploads_doc.md")
    assert "Memory" in await wf.read_text("MEMORY.md")
    assert len(calls) == 2  # one digest + one index turn


async def test_memory_workflow_globs_the_handles_upload_dir_not_a_hardcoded_folder():
    """#198: the digest globs ``wf.upload_dir`` (injected from the profile), not the
    old hardcoded ``uploads/`` (#234) — so a profile that stages into a different
    folder still works and stays in sync with where the chat attach lands."""
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u", upload_dir="dropbox")
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        return "note" if len(calls) == 1 else "# Memory\n- x"

    wf.drive_turn = drive_turn
    await wf.write("dropbox/doc.txt", b"content")
    await wf.write("dropbox/input.json", b"{}")  # the control file is excluded, not digested
    result = await run(wf, {})
    assert result == {"status": "done", "notes": 1}
    assert await wf.exists("memory/dropbox_doc.md")


async def test_memory_workflow_overwrites_seeded_files_from_agent_reply():
    """#107 decision/action: MEMORY.md is seeded at Hub creation, but the agent never
    calls write_file (long-content tool args are unreliable). It REPLIES with the
    content and the step writes it — so a seeded file is reliably overwritten, and the
    content-producing nodes carry NO write tools (just read-only)."""
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    tools_per_call: list[list[str]] = []

    async def drive_turn(prompt, tools):
        tools_per_call.append(list(tools or []))
        return "fresh note" if len(tools_per_call) == 1 else "# Memory index\n- fresh"

    wf.drive_turn = drive_turn
    await wf.write("MEMORY.md", "# Memory — STALE SEED")  # pre-existing → must be replaced
    await wf.write("uploads/doc.txt", b"content")
    await wf.write("uploads/input.json", b"{}")
    await run(wf, {})
    assert await wf.read_text("MEMORY.md") == "# Memory index\n- fresh"  # overwritten
    # the content-producing nodes never carry write tools — they reply with the content.
    for tools in tools_per_call:
        assert "write_file" not in tools and "edit_file" not in tools


async def test_memory_workflow_rerun_skips_completed_steps():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        return "note" if len(calls) == 1 else "# Memory"

    wf.drive_turn = drive_turn
    await wf.write("uploads/doc.txt", b"content")
    await wf.write("uploads/input.json", b"{}")
    await run(wf, {})
    before = len(calls)
    await run(wf, {})  # re-run — every step is already journaled
    assert len(calls) == before  # no new agent turns (steps skipped, §9)


async def test_memory_workflow_is_empty_with_no_uploads():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("uploads/input.json", b"{}")
    assert await run(wf, {}) == {"status": "empty", "notes": 0}


async def test_memory_preflight_blocks_when_no_files_staged():
    """#283: the same empty-uploads no-op the run guards against is caught BEFORE launch —
    a failing REQUIRED check so the dialog can disable 'Run' and say why."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("uploads/input.json", b"{}")  # the control file isn't a staged upload
    report = await _preflight()(wf, {})
    assert can_run(report) is False
    blocked = [c for c in report.checks if not c.ok]
    assert blocked and blocked[0].severity is Severity.REQUIRED and blocked[0].reason


async def test_memory_preflight_counts_staged_files_when_ready():
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("uploads/a.txt", b"x")
    await wf.write("uploads/b.txt", b"y")
    await wf.write("uploads/input.json", b"{}")
    report = await _preflight()(wf, {})
    assert can_run(report) is True
    assert "2" in report.summary  # surfaces the concrete count it will act on
