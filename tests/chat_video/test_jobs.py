"""The chat-video job (plan-chat-video-export, P5): queued by the route,
rendered by whoever consumes `chat-video`, everything it touches a file in
the item's workspace through the real `WorkspaceFiles`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import msgspec
import pytest
from specstar import QB, SpecStar
from specstar.types import RevisionInfo

from workspace_app.apps.rca.model import RcaInvestigation
from workspace_app.chat_video.jobs import ChatVideoCoordinator, ChatVideoJob, InFlight
from workspace_app.chat_video.options import VideoOptions
from workspace_app.chat_video.progress import Progress
from workspace_app.chat_video.render import Cancelled
from workspace_app.config.schema import ChatVideoSettings
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.protocol import FileNotFound
from workspace_app.kb.chat_export import parse_chat_export
from workspace_app.perm.model import Permission
from workspace_app.resources import make_spec

_PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489")
OUT = "/exports/chat-video/t-20260919-0310.mp4"
MESSAGES = [
    {"role": "user", "content": "hi"},
    {
        "role": "assistant",
        "content": "see ![c](plots/a.png), ![gone](plots/z.png) and ![big](plots/big.png)",
    },
]
T0 = datetime(2026, 9, 19, 3, 10, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t


class _Render:
    """A `render_chat_video` stand-in: records the call, can wait until it is
    stopped (the cancel path) or raise, and reports the stages a real one
    would."""

    def __init__(
        self,
        *,
        result=None,
        raises: Exception | None = None,
        wait_for_stop=False,
        before_return: Callable[[], None] | None = None,
    ):
        self.result = {"mp4": b"MP4"} if result is None else result
        self.raises = raises
        self.wait_for_stop = wait_for_stop
        self.before_return = before_return
        self.calls: list[dict] = []
        self.stopped = False  # `should_stop` turned true while it waited

    def __call__(self, **kw):
        self.calls.append(kw)
        kw["on_stage"]("rendering")
        if self.wait_for_stop:
            deadline = time.monotonic() + 5
            while not kw["should_stop"]():
                if time.monotonic() > deadline:
                    break  # gave up: `stopped` stays False, the test tells the two apart
                time.sleep(0.01)
            else:
                self.stopped = True
            raise self.raises if self.raises is not None else Cancelled("stopped")
        if self.raises is not None:
            raise self.raises
        kw["on_stage"]("encoding")
        if self.before_return is not None:
            self.before_return()
        return self.result


def _item(spec: SpecStar, *, by: str = "alice", permission: Permission | None = None) -> str:
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(user=by):
        return rm.create(
            RcaInvestigation(title="t", owner=by, description="", permission=permission)
        ).resource_id


_MADE: list[ChatVideoCoordinator] = []


@pytest.fixture(autouse=True)
def _put_the_loops_away():
    """A coordinator handed a job outside a running loop starts a loop thread
    of its own; stop each one with the test that made it."""
    yield
    for coord in _MADE:
        coord._stop_consuming()
    _MADE.clear()


def _coordinator(
    spec: SpecStar, files: WorkspaceFiles, render: _Render, *, clock: _Clock, **limits
) -> ChatVideoCoordinator:
    coord = ChatVideoCoordinator(
        spec, limits=ChatVideoSettings(**limits), render=render, now=clock, superusers=frozenset()
    )
    coord.set_files(files)
    _MADE.append(coord)
    return coord


async def _progress(files: WorkspaceFiles, item: str) -> Progress | None:
    try:
        return Progress.loads(await files.read(item, OUT + ".progress.json"))
    except FileNotFound:
        return None


def _job(spec: SpecStar):
    """The job row queued last."""
    rm = spec.get_resource_manager(ChatVideoJob)
    infos = []
    for row in rm.list_resources(QB.all()):  # ty: ignore[invalid-argument-type]
        assert isinstance(row.info, RevisionInfo)
        infos.append(row.info)
    assert infos
    return rm.get(max(infos, key=lambda i: i.created_time).resource_id)


# ─── the producer ────────────────────────────────────────────────────────────


async def test_enqueue_writes_the_transcript_and_a_queued_progress_and_a_job_of_paths():
    """The transcript lands as `<output>.chat.json` (the CLI's format, kept —
    edit and resubmit), the progress file says `queued` with the API's own
    heartbeat, and the job row carries paths + options only: no message text
    on an unfenced row (#723)."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    coord = _coordinator(spec, files, _Render(), clock=clock)

    queued = await coord.enqueue(
        item_id=item, title="t", messages=MESSAGES, options=VideoOptions(fmt=("mp4",)),
        output_path=OUT, expected_seconds=41, user="alice",
    )  # fmt: skip

    source, progress = queued.source_path, queued.progress_path
    assert (source, progress) == (OUT + ".chat.json", OUT + ".progress.json")
    assert parse_chat_export(await files.read(item, source)) == ("t", MESSAGES)
    p = await _progress(files, item)
    assert p is not None and p.stage == "queued" and p.expected_seconds == 41
    assert p.requested_by == "alice" and p.heartbeat_at == T0
    job = _job(spec)
    assert job.info.created_by == "alice"
    assert job.data.partition_key == item  # two exports of one item run one at a time
    row = msgspec.to_builtins(job.data.payload)
    assert set(row) == {
        "item_id",
        "source_path",
        "output_path",
        "progress_path",
        "options",
        "token",
    }
    # The file, the row and the answer carry the same mark: the watcher
    # tells its file from a successor's by it.
    assert row["token"] and row["token"] == p.token == queued.token
    assert "hi" not in json.dumps(row)


async def test_one_video_in_flight_per_item_and_one_per_person():
    """Decision 10: one in flight per ITEM and one per PERSON (409 either
    way), so a second export of the same item — whoever asks — waits, and
    one person cannot fan out over every item they own. The first version
    refused only the same person on the same item; the review found the
    table was a row short."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item, other = _item(spec), _item(spec)
    coord = _coordinator(spec, files, _Render(), clock=clock, stale_after_seconds=60)

    async def ask(item_id: str, output_path: str, *, user: str = "alice") -> None:
        await coord.enqueue(
            item_id=item_id, title="t", messages=MESSAGES, options=VideoOptions(fmt=("mp4",)),
            output_path=output_path, expected_seconds=1, user=user,
        )  # fmt: skip

    await ask(item, OUT)

    with pytest.raises(InFlight, match="already being made at " + OUT):
        await ask(item, OUT)
    with pytest.raises(InFlight, match="a video is already being made on this item"):
        await ask(item, "/bob.mp4", user="bob")  # per item: another person waits too
    with pytest.raises(InFlight, match="you already have a video being made"):
        await ask(other, "/x.mp4")  # per person: alice, on another item
    await ask(other, "/bob.mp4", user="bob")  # bob on the other item: neither rule


async def test_a_queued_file_is_alive_while_its_job_row_is_and_no_longer_when_it_is_not():
    """The review's finding: nobody rewrites a `queued` file's heartbeat
    while the job waits in line behind a long render, so judging it by age
    made the normal backlog "stale" — the second request was accepted and
    the queue held two jobs for one output. A `queued` file is alive while a
    PENDING / PROCESSING row names it; only a running stage is judged by its
    heartbeat."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    coord = _coordinator(spec, files, _Render(), clock=clock, stale_after_seconds=60)

    async def ask() -> None:
        await coord.enqueue(
            item_id=item, title="t", messages=MESSAGES, options=VideoOptions(fmt=("mp4",)),
            output_path=OUT, expected_seconds=1, user="alice",
        )  # fmt: skip

    await ask()
    clock.t = T0 + timedelta(seconds=600)  # ten minutes in line, still queued
    with pytest.raises(InFlight, match="already being made at " + OUT):
        await ask()
    rows = spec.get_resource_manager(ChatVideoJob).list_resources(QB.all())  # ty: ignore[invalid-argument-type]
    assert len(list(rows)) == 1  # no duplicate job

    # The row gone (the queue dropped it): the file is nobody's, replaced.
    rm = spec.get_resource_manager(ChatVideoJob)
    rm.delete(_job(spec).info.resource_id)
    await ask()
    p = await _progress(files, item)
    assert p is not None and p.heartbeat_at == clock.t

    # A running stage IS judged by its heartbeat: a worker that stopped
    # breathing past `stale_after_seconds` is replaced …
    await files.write(
        item, OUT + ".progress.json", msgspec.structs.replace(p, stage="rendering").dumps()
    )
    clock.t += timedelta(seconds=61)
    await ask()
    p = await _progress(files, item)
    assert p is not None and p.stage == "queued" and p.heartbeat_at == clock.t
    # … and a hand-edited file at the path is nobody's claim either.
    await files.write(item, OUT + ".progress.json", b"{not ours")
    await ask()
    assert (await _progress(files, item)) is not None


# ─── the consumer ────────────────────────────────────────────────────────────


async def _queued(
    files: WorkspaceFiles,
    spec: SpecStar,
    coord: ChatVideoCoordinator,
    item: str,
    *,
    output_path: str = OUT,
    user: str = "alice",
):
    await files.write(item, "/plots/a.png", _PNG)
    await files.write(item, "/plots/big.png", _PNG + bytes(VideoOptions().max_asset_bytes))
    await coord.enqueue(
        item_id=item, title="t", messages=MESSAGES, options=VideoOptions(fmt=("mp4",)),
        output_path=output_path, expected_seconds=1, user=user,
    )  # fmt: skip
    return _job(spec)


async def test_the_worker_stops_reading_assets_once_the_total_budget_is_spent():
    """`max_assets_total_bytes` bounds the PAGE (first-fit in reading order,
    `player.decide_assets`); the worker used to read every file that fit
    `max_asset_bytes` on its own and hand the page 60 MB it would then
    discard — bytes in the worker's RAM for nothing. The same first-fit
    walk, before the read."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    for name in ("a", "b", "c"):
        await files.write(item, f"/plots/{name}.png", _PNG + bytes(2_000))
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "![a](plots/a.png) ![b](plots/b.png) ![c](plots/c.png)"},
    ]
    render = _Render()
    coord = _coordinator(spec, files, render, clock=clock)
    await coord.enqueue(
        item_id=item, title="t", messages=messages,
        options=VideoOptions(fmt=("mp4",), max_asset_bytes=3_000, max_assets_total_bytes=4_500),
        output_path=OUT, expected_seconds=1, user="alice",
    )  # fmt: skip

    await asyncio.to_thread(coord._handle, _job(spec))

    # a (2,033 B) fits; b would take the total to 4,066 — fits; c would not.
    assert sorted(render.calls[0]["assets"]) == ["/plots/a.png", "/plots/b.png"]


async def test_the_worker_renders_from_the_source_file_and_writes_only_the_video():
    """The job reads the transcript back through `parse_chat_export`, hands
    the render the workspace files it refers to (a missing one is skipped, and
    one over `max_asset_bytes` is left out by its size, the CLI's rule), writes
    the video, and deletes the progress file — the source stays."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    render = _Render()
    coord = _coordinator(spec, files, render, clock=clock)
    job = await _queued(files, spec, coord, item)

    await asyncio.to_thread(coord._handle, job)

    assert await files.read(item, OUT) == b"MP4"
    assert await _progress(files, item) is None
    assert parse_chat_export(await files.read(item, OUT + ".chat.json"))[1] == MESSAGES
    call = render.calls[0]
    assert (call["title"], call["messages"]) == ("t", MESSAGES)
    assert call["assets"] == {"/plots/a.png": _PNG}  # z.png absent, big.png over budget
    assert isinstance(call["workdir"], Path) and not call["workdir"].exists()  # scratch cleaned


@pytest.mark.parametrize(
    "after_stop",
    [Cancelled("stopped"), RuntimeError("the browser closed under the recording")],
    ids=["cancelled", "some-other-error"],
)
async def test_deleting_the_progress_file_stops_the_render_and_writes_nothing(after_stop):
    """Decision 12: the file's absence is the cancel. The heartbeat notices
    within `heartbeat_seconds`, the render's `should_stop` turns true, it
    raises `Cancelled` — or anything else, a stopped browser reports as it
    likes — and neither a video nor a new progress file appears: a cancelled
    job has nobody to report to."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    render = _Render(wait_for_stop=True, raises=after_stop)
    coord = _coordinator(spec, files, render, clock=clock, heartbeat_seconds=0.05)  # type: ignore[arg-type]
    job = await _queued(files, spec, coord, item)

    async def cancel_soon():
        await asyncio.sleep(0.1)
        await files.delete(item, OUT + ".progress.json")

    await asyncio.gather(asyncio.to_thread(coord._handle, job), cancel_soon())

    assert render.stopped  # by the flag, not by giving up
    assert await _progress(files, item) is None
    with pytest.raises(FileNotFound):
        await files.read(item, OUT)


async def test_a_file_that_is_no_longer_this_jobs_stops_it_and_is_left_alone():
    """Cancel, then resubmit the same output within one heartbeat: the file
    at the path is the NEW job's. The old job must not carry on — it would
    render the old transcript, write it at the output and delete the new
    job's file. Each file carries its job's token; a file that is not mine
    is the same as no file."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    render = _Render(wait_for_stop=True)
    coord = _coordinator(spec, files, render, clock=clock, heartbeat_seconds=0.05)  # type: ignore[arg-type]
    job = await _queued(files, spec, coord, item)
    mine = await _progress(files, item)
    assert mine is not None and mine.token

    async def replace_soon():
        await asyncio.sleep(0.1)
        theirs = msgspec.structs.replace(mine, token="someone-elses-job")
        await files.write(item, OUT + ".progress.json", theirs.dumps())

    await asyncio.gather(asyncio.to_thread(coord._handle, job), replace_soon())

    assert render.stopped  # the flag, not a give-up
    with pytest.raises(FileNotFound):
        await files.read(item, OUT)
    p = await _progress(files, item)
    assert p is not None and p.token == "someone-elses-job" and p.stage == "queued"


async def test_a_job_cancelled_while_it_waited_in_line_never_starts_the_render():
    """The file went while the job was still queued (behind a long render,
    say). The worker's first act is to look: a render that starts anyway
    is Chromium up and recording until the first heartbeat sees the flag —
    ten seconds of work for nobody, per cancelled job."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    render = _Render()
    coord = _coordinator(spec, files, render, clock=clock)
    job = await _queued(files, spec, coord, item)
    await files.delete(item, OUT + ".progress.json")

    await asyncio.to_thread(coord._handle, job)

    assert render.calls == []
    assert await _progress(files, item) is None
    with pytest.raises(FileNotFound):
        await files.read(item, OUT)


async def test_a_cancel_the_render_never_saw_still_writes_nothing():
    """The file can go between the heartbeat's last look and the render's
    return (a heartbeat is every 10 s; an encode ends when it ends). The
    result is then discarded: the person asked for nothing to appear."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)

    def cancel_now():
        asyncio.run(files.delete(item, OUT + ".progress.json"))

    render = _Render(before_return=cancel_now)
    coord = _coordinator(spec, files, render, clock=clock)
    job = await _queued(files, spec, coord, item)

    await asyncio.to_thread(coord._handle, job)

    assert await _progress(files, item) is None
    with pytest.raises(FileNotFound):
        await files.read(item, OUT)


async def test_the_heartbeat_rewrites_the_stage_and_the_elapsed_seconds():
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    seen: list[tuple[str, int]] = []

    class _SlowRender(_Render):
        def __call__(self, **kw):
            self.calls.append(kw)
            kw["on_stage"]("rendering")
            time.sleep(0.12)
            clock.t = T0 + timedelta(seconds=20)
            kw["on_stage"]("encoding")
            time.sleep(0.12)
            return self.result

    coord = _coordinator(spec, files, _SlowRender(), clock=clock, heartbeat_seconds=0.05)  # type: ignore[arg-type]
    job = await _queued(files, spec, coord, item)

    async def watch():
        for _ in range(40):
            await asyncio.sleep(0.02)
            p = await _progress(files, item)
            if p is not None and (not seen or seen[-1] != (p.stage, p.elapsed_seconds)):
                seen.append((p.stage, p.elapsed_seconds))

    await asyncio.gather(asyncio.to_thread(coord._handle, job), watch())

    stages = [s for s, _ in seen]
    assert stages[0] == "queued" and "rendering" in stages and "encoding" in stages
    assert max(e for _, e in seen) == 20  # elapsed comes from the clock, not a guess


async def test_a_heartbeat_write_that_raises_does_not_take_the_job_with_it():
    """A transient store error in the heartbeat used to end the task with the
    exception, which `finally: await beat` re-raised AFTER the render had
    finished: the finished video thrown away, no sentence, and `stop` could
    never be set again. The heartbeat logs and goes on."""
    spec, clock = make_spec(default_user="u"), _Clock()
    writes: list[str] = []

    class _Files(WorkspaceFiles):
        async def write(self, workspace_id, path, data):
            writes.append(path)
            if path.endswith(".progress.json") and writes.count(path) == 3:
                raise OSError("store hiccup")  # one heartbeat, once
            await super().write(workspace_id, path, data)

    files = _Files(MemoryFileStore())
    item = _item(spec)

    class _SlowRender(_Render):
        def __call__(self, **kw):
            self.calls.append(kw)
            kw["on_stage"]("rendering")
            time.sleep(0.3)
            return self.result

    coord = _coordinator(spec, files, _SlowRender(), clock=clock, heartbeat_seconds=0.05)  # type: ignore[arg-type]
    job = await _queued(files, spec, coord, item)

    await asyncio.to_thread(coord._handle, job)

    assert await files.read(item, OUT) == b"MP4"
    assert await _progress(files, item) is None


async def test_the_failure_sentence_is_not_overwritten_by_a_late_heartbeat():
    """`fail()` used to run before the heartbeat task was cancelled; a beat
    that woke during it landed after it and put `rendering` back over
    `failed` — the job over, the pill showing "錄影中" until the stale rule,
    the sentence never seen. The heartbeat is stopped first."""
    spec, clock = make_spec(default_user="u"), _Clock()
    store = MemoryFileStore()

    class _Files(WorkspaceFiles):
        async def write(self, workspace_id, path, data):
            # Every real store writes off the loop (a thread, an HTTP PUT):
            # a write in flight LANDS even if its awaiter is cancelled. The
            # heartbeat's takes longer than the failure's, so a beat that
            # started just before `fail()` lands just after it.
            slow = 0.02 if b'"failed"' in data else 0.05

            def land():
                time.sleep(slow)
                store._files[workspace_id][path] = data

            await asyncio.to_thread(land)

    files = _Files(store)
    item = _item(spec)

    class _FailingRender(_Render):
        def __call__(self, **kw):
            self.calls.append(kw)
            kw["on_stage"]("rendering")
            time.sleep(0.12)
            raise RuntimeError("ffmpeg failed encoding mp4: x")

    coord = _coordinator(spec, files, _FailingRender(), clock=clock, heartbeat_seconds=0.001)  # type: ignore[arg-type]
    job = await _queued(files, spec, coord, item)

    await asyncio.to_thread(coord._handle, job)
    await asyncio.sleep(0.1)  # a straggler, if there were one, would land here

    p = await _progress(files, item)
    assert p is not None and p.stage == "failed" and p.error == "ffmpeg failed encoding mp4: x"


async def test_a_render_that_fails_leaves_the_sentence_in_the_progress_file():
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    coord = _coordinator(
        spec, files, _Render(raises=RuntimeError("ffmpeg failed encoding mp4: x")), clock=clock
    )
    job = await _queued(files, spec, coord, item)

    await asyncio.to_thread(coord._handle, job)

    p = await _progress(files, item)
    assert p is not None and p.stage == "failed" and p.error == "ffmpeg failed encoding mp4: x"
    with pytest.raises(FileNotFound):
        await files.read(item, OUT)
    assert await files.read(item, OUT + ".chat.json")  # the input is kept for a retry


@pytest.mark.parametrize("kept", ["read_content", "add_content"])
async def test_a_grant_withdrawn_while_queued_is_refused_by_the_worker_before_rendering(kept):
    """The route said yes at enqueue; the worker asks again (`_may`) with the
    same primitive, because it is the last one who can say no. Both verbs are
    needed (read the transcript and the pictures; add the video), so losing
    either one refuses."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    granted = Permission(
        visibility="restricted", read_content=["user:alice"], add_content=["user:alice"]
    )
    item = _item(spec, by="bob", permission=granted)  # alice is a grantee, not the owner
    render = _Render()
    coord = _coordinator(spec, files, render, clock=clock)
    job = await _queued(files, spec, coord, item)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(user="bob"):
        left = Permission(visibility="restricted", **{kept: ["user:alice"]})
        rm.update(item, RcaInvestigation(title="t", owner="bob", description="", permission=left))

    await asyncio.to_thread(coord._handle, job)

    p = await _progress(files, item)
    assert p is not None and p.stage == "failed" and "not authorized" in p.error
    assert render.calls == []


@pytest.mark.parametrize("damage", ["deleted", "hand-edited"])
async def test_a_transcript_gone_or_broken_before_its_turn_fails_with_the_reason(damage):
    """The source file is the person's to edit while the job waits; one that
    no longer parses (or is gone) is the job's sentence, not a traceback."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    render = _Render()
    coord = _coordinator(spec, files, render, clock=clock)
    job = await _queued(files, spec, coord, item)
    if damage == "deleted":
        await files.delete(item, OUT + ".chat.json")
    else:
        await files.write(item, OUT + ".chat.json", b"{")

    await asyncio.to_thread(coord._handle, job)

    p = await _progress(files, item)
    assert p is not None and p.stage == "failed"
    assert p.error.startswith(f"the transcript {OUT}.chat.json could not be read: ")
    assert render.calls == []


async def test_an_item_deleted_while_queued_is_refused_by_the_worker():
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    render = _Render()
    coord = _coordinator(spec, files, render, clock=clock)
    job = await _queued(files, spec, coord, item)
    rm = spec.get_resource_manager(RcaInvestigation)
    with rm.using(user="alice"):
        rm.delete(item)

    await asyncio.to_thread(coord._handle, job)

    p = await _progress(files, item)
    assert p is not None and p.stage == "failed" and "not authorized" in p.error
    assert render.calls == []


def test_a_coordinator_without_a_facade_says_so():
    """`set_files` is a post-build injection (create_app's); a bundle used
    before it is a composition bug, named rather than an AttributeError."""
    coord = ChatVideoCoordinator(make_spec(default_user="u"))
    with pytest.raises(RuntimeError, match="set_files"):
        _ = coord.files


async def test_closing_a_coordinator_that_never_consumed_starts_no_consumer():
    """#804: a pure producer's shutdown must not start a consumer "so it still
    flushes" — that would run every pending job on the pod as it exits."""
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    coord = _coordinator(spec, files, _Render(), clock=clock)

    await coord.aclose()

    assert not coord.consuming


async def test_an_output_over_the_ceiling_is_refused_with_the_number_not_written():
    spec, files, clock = make_spec(default_user="u"), WorkspaceFiles(MemoryFileStore()), _Clock()
    item = _item(spec)
    coord = _coordinator(
        spec, files, _Render(result={"mp4": b"x" * 101}), clock=clock, max_output_bytes=100
    )
    job = await _queued(files, spec, coord, item)

    await asyncio.to_thread(coord._handle, job)

    p = await _progress(files, item)
    assert p is not None and p.stage == "failed"
    assert p.error == (
        "the mp4 is 101 bytes; at most 100 — choose mp4, a smaller size or a shorter range"
    )
    with pytest.raises(FileNotFound):
        await files.read(item, OUT)


async def test_a_full_workspace_refuses_the_video_by_the_facades_own_rule():
    """The quota is the facade's chokepoint (#538): the write is refused
    there, and the refusal becomes the job's sentence."""
    spec, clock = make_spec(default_user="u"), _Clock()
    quota = [0]  # set once the inputs are in: exactly the room for the video, minus one
    files = WorkspaceFiles(MemoryFileStore(), quota=lambda _item: quota[0])
    item = _item(spec)
    coord = _coordinator(spec, files, _Render(result={"mp4": b"x" * 150}), clock=clock)
    job = await _queued(files, spec, coord, item)
    quota[0] = await files.workspace_usage(item) + 149

    await asyncio.to_thread(coord._handle, job)

    p = await _progress(files, item)
    assert p is not None and p.stage == "failed" and p.error.startswith("could not write " + OUT)


async def test_under_a_running_loop_the_job_runs_on_that_loop():
    """`start_consuming` under the API's loop captures it and `_handle` runs
    the job there — the facade's asyncio locks never meet a second loop. Seen
    from a facade write: it happens on the test's loop, not a private one."""
    spec, clock = make_spec(default_user="u"), _Clock()
    loops: list[asyncio.AbstractEventLoop] = []

    class _Files(WorkspaceFiles):
        async def write(self, workspace_id, path, data):
            loops.append(asyncio.get_running_loop())
            await super().write(workspace_id, path, data)

    files = _Files(MemoryFileStore())
    item = _item(spec)
    coord = _coordinator(spec, files, _Render(), clock=clock)
    coord.start_consuming()
    try:
        await _queued(files, spec, coord, item)
        await coord.aclose()
    finally:
        coord._stop_consuming()

    assert await files.read(item, OUT) == b"MP4"
    assert loops and all(lp is asyncio.get_running_loop() for lp in loops)


def test_without_a_running_loop_every_job_runs_on_the_coordinators_one_loop():
    """A worker pod consumes with no loop running (it parks on an Event), so
    the coordinator brings its own — ONE loop for every job it will ever run,
    not one per job. The production sandbox is `kind: http`, and the facade
    reaches it through a single `httpx.AsyncClient` whose connection pool
    lives on the loop that opened it: a loop per job leaves job 2 reading
    the transcript over a connection whose loop is closed."""
    spec, clock = make_spec(default_user="u"), _Clock()
    loops: list[asyncio.AbstractEventLoop] = []

    class _Files(WorkspaceFiles):
        async def write(self, workspace_id, path, data):
            loops.append(asyncio.get_running_loop())
            await super().write(workspace_id, path, data)

    files = _Files(MemoryFileStore())
    item, other = _item(spec), _item(spec)
    coord = _coordinator(spec, files, _Render(), clock=clock)
    first = asyncio.run(_queued(files, spec, coord, item))
    second = asyncio.run(_queued(files, spec, coord, other, output_path="/second.mp4", user="bob"))
    del loops[:]  # the enqueues above ran under their own `asyncio.run`s
    done = threading.Event()

    def consume():  # the consumer thread specstar calls the handler from
        coord._handle(first)
        coord._handle(second)
        done.set()

    threading.Thread(target=consume, daemon=True).start()
    assert done.wait(5)
    coord._stop_consuming()  # what the worker's `aclose` ends in

    assert asyncio.run(files.read(item, OUT)) == b"MP4"
    assert asyncio.run(files.read(other, "/second.mp4")) == b"MP4"
    assert len(loops) >= 2 and len({id(lp) for lp in loops}) == 1, "one loop for every job"
    assert all(lp.is_closed() for lp in loops[:1])  # …and it is put away with the consumer
