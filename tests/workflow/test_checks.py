"""Built-in gates (manual §6) — the deterministic postconditions that stop an
agent's "I'm done" from counting when it isn't."""

from workspace_app.workflow.checks import choice_in, file_nonempty
from workspace_app.workflow.handle import WorkflowHandle


async def test_file_nonempty_fails_when_file_absent(wf: WorkflowHandle):
    """The agent claimed done but never wrote the file → the gate fails."""
    verdict = await file_nonempty("/out.txt")(wf, None)
    assert not verdict.ok
    assert "not written" in verdict.reason


async def test_file_nonempty_fails_when_empty(wf: WorkflowHandle):
    await wf.write("/out.txt", "   \n")
    verdict = await file_nonempty("/out.txt")(wf, None)
    assert not verdict.ok
    assert "empty" in verdict.reason


async def test_file_nonempty_passes_with_content(wf: WorkflowHandle):
    await wf.write("/out.txt", "data")
    assert (await file_nonempty("/out.txt")(wf, None)).ok


async def test_choice_in_fails_when_file_absent(wf: WorkflowHandle):
    verdict = await choice_in("/plan.json", key="collection", allowed=["a"])(wf, None)
    assert not verdict.ok
    assert "not written" in verdict.reason


async def test_choice_in_reports_the_bad_value(wf: WorkflowHandle):
    await wf.write_json("/plan.json", {"collection": "z"})
    verdict = await choice_in("/plan.json", key="collection", allowed=["a", "b"])(wf, None)
    assert not verdict.ok
    assert "'z'" in verdict.reason
