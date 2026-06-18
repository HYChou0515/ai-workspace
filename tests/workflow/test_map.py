"""wf.map — the parallel for-each (manual §11): concurrency-capped, skip+collect."""

import asyncio

from workspace_app.workflow.engine import fail
from workspace_app.workflow.handle import WorkflowHandle


async def test_map_runs_all_items_concurrently_within_the_cap(wf: WorkflowHandle):
    active = 0
    peak = 0

    async def work(_item):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    await wf.map(work, list(range(10)), concurrency=3)
    assert peak <= 3  # the cap is honored
    assert peak > 1  # and they did overlap (not serialized)


async def test_map_collects_failed_elements_and_continues(wf: WorkflowHandle):
    done: list[int] = []

    async def work(item):
        if item == 2:
            fail(f"element {item} is bad")
        done.append(item)

    failures = await wf.map(work, [1, 2, 3], concurrency=2)
    assert sorted(done) == [1, 3]  # the bad one didn't kill the batch
    assert len(failures) == 1
    assert failures[0]["item"] == "2"
    assert "bad" in failures[0]["error"]
