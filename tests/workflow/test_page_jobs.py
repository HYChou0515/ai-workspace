"""A page job is a run (`docs/plan-wui-call-agent.md` D5, D6).

What a page starts with `startTool` / `startAgent` (and what `callAgent` waits
on) is one `WorkflowRun`: the same record, stop and retention a workflow run
has, carrying `page_job` instead of a manifest. The work itself is handed in by
the API at start and runs on this pod; nothing re-drives it after a restart, so
a job past its deadline is over whatever its row still says.
"""

import asyncio

import pytest
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from tests.workflow.test_orchestrator import _clock, _orch
from workspace_app.workflow.orchestrator import TooManyPageJobs
from workspace_app.workflow.run import PageJob, RunStatus, WorkflowRun
from workspace_app.workflow.run_identity import RunIdentities, register_run_identity


@pytest.fixture
def spec_instance(spec_instance: SpecStar) -> SpecStar:
    register_run_identity(spec_instance)  # registered post-`make_spec` in the app
    return spec_instance


async def _never(_wf):  # pragma: no cover - the test never lets it finish
    raise AssertionError("must not be reached")


def _job(name: str = "summarise", kind: str = "agent") -> PageJob:
    return PageJob(kind=kind, name=name, input="hello")


async def _start(orch, work, *, item="i1", job=None, chat="c1"):
    return await orch.start_page_job(
        item_id=item,
        captured_user="owner",
        env_user="clicker",
        chat_id=chat,
        job=job or _job(),
        work=work,
    )


async def _settle(orch, run_id: str) -> None:
    task = orch._tasks.get(run_id)
    if task is not None:
        await asyncio.wait({task})


async def test_a_job_is_a_run_record_that_ends_with_the_works_answer(spec_instance: SpecStar):
    seen = []

    async def work(wf):
        seen.append((wf.user, wf.run_id))
        return {"output": "done!", "failed": False}

    orch, _ = _orch(spec_instance, _never)
    run_id = await _start(orch, work)
    await _settle(orch, run_id)

    row = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert isinstance(row, WorkflowRun)
    assert row.status is RunStatus.DONE
    assert row.page_job is not None
    assert row.page_job == PageJob(
        kind="agent", name="summarise", input="hello", deadline=row.page_job.deadline
    )
    assert row.chat_id == "c1" and row.captured_user == "owner"
    assert seen == [("owner", run_id)], "billed to the owner, on its own run's handle"
    state = await orch.page_job_state(run_id, "i1")
    assert (state.state, state.output, state.error) == (
        "done",
        {"output": "done!", "failed": False},
        None,
    )


async def test_the_clicker_is_who_its_private_values_come_from(spec_instance: SpecStar):
    """D11: billed to the owner, the presser's own values — as `startRun`."""
    orch, _ = _orch(spec_instance, _never)
    run_id = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))
    ident = RunIdentities(spec_instance).get(run_id)
    assert ident is not None and ident.env_user == "clicker" and ident.verb == "execute"


async def test_a_running_job_reads_as_running(spec_instance: SpecStar):
    gate = asyncio.Event()

    async def work(_wf):
        await gate.wait()
        return {"output": "x"}

    orch, _ = _orch(spec_instance, _never)
    run_id = await _start(orch, work)
    await asyncio.sleep(0)
    assert (await orch.page_job_state(run_id, "i1")).state == "running"
    gate.set()
    await _settle(orch, run_id)


async def test_a_job_whose_work_raises_reads_as_failed_with_the_reason(spec_instance: SpecStar):
    async def work(_wf):
        raise RuntimeError("sandbox went away")

    orch, fakes = _orch(spec_instance, _never)
    run_id = await _start(orch, work)
    await _settle(orch, run_id)
    state = await orch.page_job_state(run_id, "i1")
    assert state.state == "failed" and "sandbox went away" in (state.error or "")
    assert fakes.released == [], "a page job never tears down the item's sandbox"


async def test_a_stopped_job_reads_as_stopped(spec_instance: SpecStar):
    async def work(_wf):
        await asyncio.Event().wait()

    orch, _ = _orch(spec_instance, _never)
    run_id = await _start(orch, work)
    await asyncio.sleep(0)
    assert await orch.cancel(run_id, "i1")
    assert (await orch.page_job_state(run_id, "i1")).state == "stopped"


async def test_a_job_has_a_total_time_cap(spec_instance: SpecStar):
    async def work(_wf):
        await asyncio.Event().wait()

    orch, _ = _orch(spec_instance, _never, page_job_timeout_s=0.01)
    run_id = await _start(orch, work)
    await _settle(orch, run_id)
    state = await orch.page_job_state(run_id, "i1")
    assert state.state == "failed" and "0.01" in (state.error or "")


async def test_at_most_four_jobs_run_at_once_on_one_item(spec_instance: SpecStar):
    """D6 — the fifth is refused, a sibling item is not affected, and a
    finished job frees its place."""
    gates = [asyncio.Event() for _ in range(4)]

    def waiting(gate):
        async def work(_wf):
            await gate.wait()
            return {"output": ""}

        return work

    orch, _ = _orch(spec_instance, _never)
    ids = [await _start(orch, waiting(g), chat=f"c{n}") for n, g in enumerate(gates)]
    with pytest.raises(TooManyPageJobs):
        await _start(orch, waiting(asyncio.Event()), chat="c9")
    other = await _start(orch, waiting(gates[0]), item="i2", chat="c10")

    gates[0].set()
    await _settle(orch, ids[0])
    await _settle(orch, other)
    fifth = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}), chat="c11")
    await _settle(orch, fifth)
    for g in gates:
        g.set()
    for rid in ids:
        await _settle(orch, rid)


async def test_the_cap_is_a_setting(spec_instance: SpecStar):
    orch, _ = _orch(spec_instance, _never, page_job_limit=1)
    gate = asyncio.Event()

    async def work(_wf):
        await gate.wait()
        return {"output": ""}

    first = await _start(orch, work)
    with pytest.raises(TooManyPageJobs):
        await _start(orch, work, chat="c2")
    gate.set()
    await _settle(orch, first)


async def test_a_workflow_run_does_not_take_a_page_jobs_place(spec_instance: SpecStar):
    gate = asyncio.Event()

    async def run(_wf, _inputs):
        await gate.wait()
        return {}

    orch, _ = _orch(spec_instance, run, page_job_limit=1)
    wf_run = await orch.start(slug="rca", item_id="i1", profile="p", captured_user="u", chat_id="w")
    job = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))
    await _settle(orch, job)
    gate.set()
    await _settle(orch, wf_run)


async def test_a_job_lost_with_its_pod_is_over_once_past_its_deadline(spec_instance: SpecStar):
    """Nothing re-drives a page job (its work lived in the dead pod's memory),
    so a row still RUNNING past its deadline is read as failed, settled as such,
    and no longer holds one of the item's places."""
    clock = [10_000]
    orch, _ = _orch(spec_instance, _never, now=lambda: clock[0], page_job_limit=1)
    rm = spec_instance.get_resource_manager(WorkflowRun)
    lost = rm.create(
        WorkflowRun(
            item_id="i1",
            captured_user="owner",
            status=RunStatus.RUNNING,
            chat_id="c0",
            page_job=PageJob(kind="tool", name="t", input="{}", deadline=10_500),
        )
    ).resource_id
    assert (await orch.page_job_state(lost, "i1")).state == "running"
    with pytest.raises(TooManyPageJobs):
        await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))

    clock[0] = 10_500 + 60_001
    state = await orch.page_job_state(lost, "i1")
    assert state.state == "failed" and state.error
    assert rm.get(lost).data.status is RunStatus.ERROR
    fresh = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))
    await _settle(orch, fresh)


async def test_a_lost_job_frees_its_place_even_if_nobody_ever_asks_about_it(
    spec_instance: SpecStar,
):
    clock = [10_000]
    orch, _ = _orch(spec_instance, _never, now=lambda: clock[0], page_job_limit=1)
    spec_instance.get_resource_manager(WorkflowRun).create(
        WorkflowRun(
            item_id="i1",
            captured_user="owner",
            status=RunStatus.RUNNING,
            page_job=PageJob(kind="tool", name="t", deadline=10_500),
        )
    )
    clock[0] = 10_500 + 60_001
    fresh = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))
    await _settle(orch, fresh)


async def test_a_job_stopped_while_waiting_for_a_slot_is_stopped_not_pending(
    spec_instance: SpecStar,
):
    """The platform-wide run cap (`concurrency`) can hold a job in `pending`;
    a Stop then cancels a driver that never ran, and nothing else would move
    the row — it would hold one of the item's places until its deadline."""
    gate = asyncio.Event()

    async def work(_wf):
        await gate.wait()
        return {"output": ""}

    orch, _ = _orch(spec_instance, _never, concurrency=1, page_job_limit=2)
    first = await _start(orch, work)
    queued = await _start(orch, work, chat="c2")
    await asyncio.sleep(0)
    assert await orch.cancel(queued, "i1")
    assert (await orch.page_job_state(queued, "i1")).state == "stopped"
    assert orch._live_page_jobs("i1") == 1
    gate.set()
    await _settle(orch, first)


async def test_a_page_job_cannot_be_steered_and_is_left_as_it_was(spec_instance: SpecStar):
    """A page job has no workflow: its `workflow_id` is "", which the manifest
    lookup reads as "the profile's FIRST workflow" — so a steer would stop the
    job and run a steerer against an unrelated workflow. Refused before any
    write, through the same 409 a workflow that cannot be loaded gets."""
    from workspace_app.workflow.orchestrator import WorkflowUnavailable

    gate = asyncio.Event()

    async def work(_wf):
        await gate.wait()
        return {"output": ""}

    orch, _ = _orch(spec_instance, _never)
    run_id = await _start(orch, work)
    await asyncio.sleep(0)
    with pytest.raises(WorkflowUnavailable, match="page"):
        await orch.steer(slug="rca", item_id="i1", profile="p", run_id=run_id, instruction="x")
    assert (await orch.page_job_state(run_id, "i1")).state == "running"
    gate.set()
    await _settle(orch, run_id)


async def test_a_stuck_page_job_is_never_re_driven_as_a_workflow(spec_instance: SpecStar):
    clock = [10_000]
    orch, _ = _orch(spec_instance, _never, now=lambda: clock[0])
    lost = (
        spec_instance.get_resource_manager(WorkflowRun)
        .create(
            WorkflowRun(
                item_id="i1",
                captured_user="owner",
                status=RunStatus.RUNNING,
                progress_at=1,
                page_job=PageJob(kind="tool", name="t", deadline=10_500),
            )
        )
        .resource_id
    )
    assert not await orch.resume(lost, slug="rca", profile="p", grace_ms=1)
    assert lost not in orch._tasks


class _Ends:
    """`end_page_job` and the turn epoch, recorded."""

    def __init__(self) -> None:
        self.ended: list[tuple[str, str]] = []
        self.epochs: dict[str, int] = {}

    async def end(self, item_id: str, key: str) -> None:
        self.ended.append((item_id, key))

    async def epoch(self, key: str) -> int:
        return self.epochs.get(key, 0)


@pytest.mark.parametrize("how", ["done", "failed", "stopped"])
async def test_a_page_job_frees_its_own_session_and_never_the_items_sandbox(
    spec_instance: SpecStar, how: str
):
    """Defect review round 1: a job ending went through the workflow release,
    which closes the ITEM's sandbox when no other run is RUNNING — a `callTool`
    or a chat turn is not a run, so a `callAgent` finishing could take the
    sandbox out from under them. A job frees its own turn session only."""
    ends = _Ends()
    gate = asyncio.Event()

    async def work(_wf):
        if how == "failed":
            raise RuntimeError("x")
        await gate.wait()
        return {"output": ""}

    orch, fakes = _orch(spec_instance, _never, end_page_job=ends.end)
    run_id = await _start(orch, work)
    await asyncio.sleep(0)
    if how == "stopped":
        assert await orch.cancel(run_id, "i1")
    gate.set()
    await _settle(orch, run_id)
    assert fakes.released == []
    assert ends.ended == [("i1", "c1")]


async def test_a_job_stopped_from_another_pod_still_frees_its_session_here(
    spec_instance: SpecStar,
):
    """A Stop from another pod reaches the work as a `CancelledError` the work
    raises itself (the turn or the command saw the epoch move). The pod that
    pressed Stop holds no task and frees nothing, so this one must."""
    ends = _Ends()

    async def work(_wf):
        raise asyncio.CancelledError

    orch, _ = _orch(spec_instance, _never, end_page_job=ends.end)
    run_id = await _start(orch, work)
    await _settle(orch, run_id)
    assert (await orch.page_job_state(run_id, "i1")).state == "stopped"
    assert ends.ended == [("i1", "c1")]


async def test_a_stop_from_another_pod_while_waiting_for_a_slot_is_kept(
    spec_instance: SpecStar,
):
    """Defect review round 1: a Stop on a pod that does not hold the job only
    advances the chat's epoch. The job's work samples it when it STARTS — after
    the slot — so a Stop that landed while the job queued became the baseline
    and the job ran anyway. The epoch is now sampled when the job is accepted."""
    ends = _Ends()
    gate = asyncio.Event()
    ran: list[str] = []

    async def first(_wf):
        await gate.wait()
        return {"output": ""}

    async def queued_work(_wf):
        ran.append("queued")
        return {"output": ""}

    orch, _ = _orch(
        spec_instance,
        _never,
        concurrency=1,
        end_page_job=ends.end,
        stop_epoch=ends.epoch,
    )
    holder = await _start(orch, first)
    queued = await _start(orch, queued_work, chat="c2")
    await asyncio.sleep(0)
    ends.epochs["c2"] = 1  # the Stop, pressed on another pod
    gate.set()
    await _settle(orch, holder)
    await _settle(orch, queued)
    assert ran == []
    assert (await orch.page_job_state(queued, "i1")).state == "stopped"


async def test_a_lost_job_is_settled_by_the_next_start_even_if_nobody_reads_it(
    spec_instance: SpecStar,
):
    """Defect review round 1: only a status read settled a lost row, so a page
    closed after its pod died left it RUNNING in the run history for good."""
    clock = [10_000]
    orch, _ = _orch(spec_instance, _never, now=lambda: clock[0])
    rm = spec_instance.get_resource_manager(WorkflowRun)
    lost = rm.create(
        WorkflowRun(
            item_id="i1",
            captured_user="owner",
            status=RunStatus.RUNNING,
            page_job=PageJob(kind="tool", name="t", deadline=10_500),
        )
    ).resource_id
    clock[0] = 10_500 + 60_001
    fresh = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))
    await _settle(orch, fresh)
    assert rm.get(lost).data.status is RunStatus.ERROR


async def test_another_items_job_or_a_workflow_run_is_not_found(spec_instance: SpecStar):
    """The id comes from the page, the item from the URL: a job of item A is
    not readable through B, and a workflow run is not a page job at all."""
    orch, _ = _orch(spec_instance, lambda _wf, _i: asyncio.sleep(0, {}))
    job = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))
    wf_run = await orch.start(slug="rca", item_id="i1", profile="p", captured_user="u", chat_id="w")
    with pytest.raises(ResourceIDNotFoundError):
        await orch.page_job_state(job, "i2")
    with pytest.raises(ResourceIDNotFoundError):
        await orch.page_job_state(wf_run, "i1")
    await _settle(orch, job)
    await _settle(orch, wf_run)


async def test_the_deadline_is_stamped_from_the_cap(spec_instance: SpecStar):
    orch, _ = _orch(spec_instance, _never, now=_clock(), page_job_timeout_s=30)
    run_id = await _start(orch, lambda _wf: asyncio.sleep(0, {"output": ""}))
    row = spec_instance.get_resource_manager(WorkflowRun).get(run_id).data
    assert row.page_job is not None and row.page_job.deadline >= 1000 + 30_000
    await _settle(orch, run_id)
