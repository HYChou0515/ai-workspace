"""The intake workflow profile's run() (manual §20) — produce → review → commit.

Drives the profile's ``run`` directly with a fake handle (fake agent turn that
writes the plan, fake ingest), covering the approve / reject / awaiting-human
branches without an LLM. The live LLM exercise is the P13 canned check."""

import pytest

from workspace_app.apps.playground.profiles.intake.run import run
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.workflow.discovery import load_run_callable
from workspace_app.workflow.gate import AwaitingHuman, record_decision
from workspace_app.workflow.handle import WorkflowHandle


def _handle(*, ingested: list, collection: str = "kb-docs") -> WorkflowHandle:
    store = MemoryFileStore()
    wf = WorkflowHandle(
        store=store, workspace_id="ws", config={"collections": ["kb-docs", "kb-logs"]}, user="u"
    )

    async def drive_turn(prompt: str, tools: list[str] | None) -> str:
        # The agent's decision, recorded as data: write plan/<f>.json (the gate clamps it).
        await wf.write_json("plan/inputs_a.txt.json", {"collection": collection, "digest": "d"})
        return "done"

    async def ingest(coll: str, path: str) -> str:
        ingested.append((coll, path))
        return f"doc:{coll}:{path}"

    wf.drive_turn = drive_turn
    wf._ingest = ingest
    return wf


async def _seed_input(wf: WorkflowHandle) -> None:
    await wf.write("inputs/a.txt", b"hello")
    await wf.write("inputs/input.json", b"{}")


async def test_discovery_loads_the_intake_run():
    loaded = load_run_callable("playground", "intake")
    assert callable(loaded) and loaded.__name__ == "run"


async def test_approve_classifies_then_ingests():
    ingested: list = []
    wf = _handle(ingested=ingested)
    await _seed_input(wf)
    await record_decision(wf, phase="review", choice="approve")  # human already approved
    result = await run(wf, {})
    assert result == {"status": "approved", "committed": 1}
    assert ingested == [("kb-docs", "/inputs/a.txt")]


async def test_reject_commits_nothing():
    ingested: list = []
    wf = _handle(ingested=ingested)
    await _seed_input(wf)
    await record_decision(wf, phase="review", choice="reject")
    result = await run(wf, {})
    assert result == {"status": "rejected", "files": 1}
    assert ingested == []  # nothing reached a collection (the gate sat before commit)


async def test_suspends_at_the_review_gate_with_no_decision():
    ingested: list = []
    wf = _handle(ingested=ingested)
    await _seed_input(wf)
    with pytest.raises(AwaitingHuman):
        await run(wf, {})
    assert ingested == []
