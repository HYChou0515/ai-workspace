"""The Topic Hub ``→collections`` workflow (P12, §12) — produce → review → commit:
classify uploads into the Hub's collections, collect glossary terms into a fill-in
file, gate on a human, then ingest the docs + author a context card per filled entry.

Driven through the file-path-loaded ``run()`` with a fake agent turn + fake ingest /
card-author / collection-check capabilities (no LLM, no KB)."""

import pytest

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.discovery import load_run_callable, validate_workflow_profiles
from workspace_app.workflow.gate import AwaitingHuman, record_decision
from workspace_app.workflow.handle import WorkflowHandle


def _run():
    return load_run_callable("topic-hub", "default", "collections")


def _handle() -> tuple[WorkflowHandle, list, list, list[str]]:
    """A handle whose agent turn writes the classify plan (call 1) + a glossary
    template (call 2), with fake ingest / card-author / landed-check capabilities."""
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    ingested: list = []
    cards: list = []
    calls: list[str] = []

    async def drive_turn(prompt, tools):
        calls.append(prompt)
        if len(calls) == 1:  # classify the single upload
            await wf.write_json(
                "plan/inputs_a.txt.json",
                {"collection": "Defects", "digest": "a defect report", "terms": ["M4"]},
            )
        else:  # write the glossary template (the human fills it later)
            await wf.write("glossary.todo.md", "## M4\n")
        return "done"

    async def ingest(coll, path):
        ingested.append((coll, path))
        return f"doc:{coll}:{path}"

    async def upsert_card(coll, keys, title, body):
        cards.append((coll, list(keys), title, body))
        return f"card:{title}"

    async def landed(_coll, _path):
        return True

    wf.drive_turn = drive_turn
    wf._ingest = ingest
    wf._upsert_card = upsert_card
    wf._collection_has = landed
    return wf, ingested, cards, calls


async def _seed(wf: WorkflowHandle) -> None:
    await wf.write("inputs/a.txt", b"the M4 layer delaminated")
    await wf.write("inputs/input.json", b"{}")
    await wf.write("collections.json", b'[{"id": "col-1", "name": "Defects"}]')


async def test_collections_workflow_is_discovered_and_coherent():
    validate_workflow_profiles("topic-hub")
    fn = _run()
    assert callable(fn) and fn.__name__ == "run"


async def test_no_collection_set_short_circuits():
    run = _run()
    wf = WorkflowHandle(store=MemoryFileStore(), workspace_id="ws", user="u")
    await wf.write("inputs/a.txt", b"x")
    await wf.write("inputs/input.json", b"{}")
    await wf.write("collections.json", b"[]")  # empty set → nothing to classify into
    assert await run(wf, {}) == {"status": "no_collections"}


async def test_classify_then_suspend_at_the_gate_committing_nothing():
    run = _run()
    wf, ingested, cards, _calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    # classified + wrote the glossary, but nothing committed before approval.
    assert (await wf.read_json("plan/inputs_a.txt.json"))["collection"] == "Defects"
    assert "## M4" in await wf.read_text("glossary.todo.md")
    assert ingested == [] and cards == []


async def test_human_fills_glossary_then_approve_ingests_and_authors_cards():
    run = _run()
    wf, ingested, cards, calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})  # classify + glossary, then pause
    # The human fills the glossary in the IDE (shared FileStore, §3.1) ...
    await wf.write("glossary.todo.md", "## M4\nThe fourth metal layer.")
    # ... approves, and the run resumes (classify + glossary skip; commit runs).
    await record_decision(wf, phase="review", choice="approve")
    result = await run(wf, {})
    assert result == {"status": "approved", "ingested": 1, "cards": 1}
    assert ingested == [("Defects", "/inputs/a.txt")]
    assert cards == [("Defects", ["M4"], "M4", "The fourth metal layer.")]


async def test_rerun_with_an_edited_definition_upserts_the_card_again():
    """#111: if the human refines a definition and re-runs, the commit re-fires for that
    term (the step receipt reflects the body) so the card is upserted to the new text —
    not skipped as already-done. Other completed steps still replay from the journal."""
    run = _run()
    wf, ingested, cards, _calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await wf.write("glossary.todo.md", "## M4\nThe fourth metal layer.")
    await record_decision(wf, phase="review", choice="approve")
    await run(wf, {})
    assert cards == [("Defects", ["M4"], "M4", "The fourth metal layer.")]
    # The human refines the definition and re-runs the (approved) workflow.
    await wf.write("glossary.todo.md", "## M4\nThe fourth metal interconnect layer.")
    await run(wf, {})
    # The card commit re-fired with the new body (upsert → same card, new text); ingest
    # (unchanged) replayed from its receipt, so it did NOT run a second time.
    assert cards == [
        ("Defects", ["M4"], "M4", "The fourth metal layer."),
        ("Defects", ["M4"], "M4", "The fourth metal interconnect layer."),
    ]
    assert ingested == [("Defects", "/inputs/a.txt")]  # ingest stayed idempotent


async def test_reject_commits_nothing():
    run = _run()
    wf, ingested, cards, _calls = _handle()
    await _seed(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    await record_decision(wf, phase="review", choice="reject")
    result = await run(wf, {})
    assert result == {"status": "rejected", "files": 1}
    assert ingested == [] and cards == []
