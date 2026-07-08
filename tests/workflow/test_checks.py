"""Built-in gates (manual §6) — the deterministic postconditions that stop an
agent's "I'm done" from counting when it isn't."""

from workspace_app.workflow.checks import artifact_valid, choice_in, file_nonempty
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


async def test_artifact_valid_json_rejects_conversational_pollution(wf: WorkflowHandle):
    """A reply that wraps JSON in chatter ("Sure! {...}") is not valid JSON → the gate
    fails, so the polluted artifact never flows to the next node (plan P1)."""
    await wf.write("/plan.json", 'Sure! Here is the plan:\n{"collection": "notes"}')
    verdict = await artifact_valid("/plan.json", "json")(wf, None)
    assert not verdict.ok
    assert "json" in verdict.reason.lower()


async def test_artifact_valid_json_passes_when_clean(wf: WorkflowHandle):
    await wf.write("/plan.json", '{"collection": "notes"}')
    assert (await artifact_valid("/plan.json", "json")(wf, None)).ok


async def test_artifact_valid_absent_file_fails(wf: WorkflowHandle):
    verdict = await artifact_valid("/plan.json", "json")(wf, None)
    assert not verdict.ok
    assert "not written" in verdict.reason


async def test_artifact_valid_empty_file_fails(wf: WorkflowHandle):
    await wf.write("/report.md", "  \n")
    verdict = await artifact_valid("/report.md", "markdown")(wf, None)
    assert not verdict.ok
    assert "empty" in verdict.reason


async def test_artifact_valid_yaml_rejects_pollution_and_passes_clean(wf: WorkflowHandle):
    await wf.write("/c.yaml", "Here you go: foo: [1, 2")  # unclosed flow → YAMLError
    assert not (await artifact_valid("/c.yaml", "yaml")(wf, None)).ok
    await wf.write("/c.yaml", "name: notes\ncount: 3\n")
    assert (await artifact_valid("/c.yaml", "yaml")(wf, None)).ok


async def test_artifact_valid_csv_rejects_and_passes(wf: WorkflowHandle):
    await wf.write("/t.csv", "")
    assert not (await artifact_valid("/t.csv", "csv")(wf, None)).ok
    await wf.write("/t.csv", "a,b\n1,2\n")
    assert (await artifact_valid("/t.csv", "csv")(wf, None)).ok


async def test_artifact_valid_markdown_passes_any_nonempty(wf: WorkflowHandle):
    """Prose kinds have no strong machine format — L1 only checks non-emptiness; their
    structural strength comes from a producer-declared 'requires' (plan §2.3 L2)."""
    await wf.write("/report.md", "# Report\n\nbody")
    assert (await artifact_valid("/report.md", "markdown")(wf, None)).ok
