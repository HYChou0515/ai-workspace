"""The chat-video job: producer + consumer, one object (plan-chat-video-export).

The route queues; whoever consumes ``chat-video`` renders — the API itself
under ``server.run_consumers: true``, a ``rca-worker-chat-video`` pod
otherwise. Everything the job touches is a FILE in the item's workspace, read
and written through the same ``WorkspaceFiles`` the API uses (a worker is
composed from ``build_app`` for exactly this, like ``blob-gc``):

- ``<output>.chat.json`` — the transcript, written by the route at enqueue and
  kept (edit it and resubmit); the job row carries paths only, so no message
  text sits on an unfenced job row (#723).
- ``<output>.progress.json`` — written ``queued`` at enqueue, rewritten every
  ``heartbeat_seconds`` by the worker with the stage and elapsed time; its
  ABSENCE is the cancel, its heartbeat is the liveness (``progress.is_alive``);
  deleted on success, left with the sentence on failure.
- ``<output>`` — the video, written last, once, after the size ceiling and
  the workspace quota (the facade's own rule) have said yes.

The render runs in a thread (``asyncio.to_thread``) and asks ``should_stop``
throughout; the heartbeat task on the loop answers it from the progress
file. The job body is a coroutine and specstar calls the handler from its
consumer THREAD, so it is submitted to ONE loop the coordinator holds for
its whole consuming life: under the API, the API's own loop (captured at
``start_consuming``); on a worker pod, which consumes with no loop running,
a loop of its own on a thread it starts then. Never a loop per job — the
production sandbox is ``kind: http`` and the facade reaches it through one
``httpx.AsyncClient`` whose connection pool belongs to the loop that opened
it, so the second job would read its transcript over a connection whose
loop is closed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from specstar import QB, Schema, SpecStar
from specstar.types import Job, TaskStatus

from ..api.item_authz import load_access_facts
from ..config.schema import ChatVideoSettings
from ..filestore.protocol import FileNotFound
from ..kb.chat_export import build_chat_export, parse_chat_export
from ..perm.authorize import Actor, authorize
from ..resources.groups import groups_of
from . import progress as prog
from .options import VideoOptions
from .render import Cancelled
from .service import render_chat_video
from .timeline import build_timeline

if TYPE_CHECKING:
    from ..files import WorkspaceFiles

logger = logging.getLogger(__name__)

_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02


class InFlight(Exception):
    """A live progress file already claims this output (or this user already
    has one running on the item): the route answers 409."""


class ChatVideoPayload(msgspec.Struct):
    """Paths, and the options — never the transcript (the job row is unfenced,
    #723; the transcript is the source file the worker reads)."""

    item_id: str
    source_path: str
    output_path: str
    progress_path: str
    options: VideoOptions


class ChatVideoJob(Job[ChatVideoPayload]):
    """One render. ``partition_key = item_id`` so two exports of one item
    queue behind each other on a partitioned backend; the route refuses the
    second while the first is alive anyway (409)."""


Render = Callable[..., dict[str, bytes]]


class ChatVideoCoordinator:
    def __init__(
        self,
        spec: SpecStar,
        *,
        limits: ChatVideoSettings | None = None,
        message_queue_factory: object | None = None,
        superusers: frozenset[str] = frozenset(),
        render: Render = render_chat_video,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._spec = spec
        self._limits = limits if limits is not None else ChatVideoSettings()
        self._superusers = superusers
        self._render = render
        self._now = now
        self._files: WorkspaceFiles | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._own_loop: tuple[asyncio.AbstractEventLoop, threading.Thread] | None = None
        self._loop_lock = threading.Lock()  # the queue may call `_handle` from several threads
        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(ChatVideoJob, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(ChatVideoJob)
        self._consuming = False

    def set_files(self, files: WorkspaceFiles) -> None:
        """Injected after the build (like the eval coordinator's retriever):
        ``build_coordinators`` has no facade, ``create_app`` does — and the
        worker, composed from ``build_app``, gets the same one."""
        self._files = files

    @property
    def files(self) -> WorkspaceFiles:
        if self._files is None:
            raise RuntimeError("ChatVideoCoordinator has no WorkspaceFiles (set_files not called)")
        return self._files

    # ── producer ─────────────────────────────────────────────────────
    async def enqueue(
        self,
        *,
        item_id: str,
        title: str,
        messages: list[dict[str, Any]],
        options: VideoOptions,
        output_path: str,
        expected_seconds: int,
        user: str,
    ) -> tuple[str, str]:
        """Write the source and the ``queued`` progress file, queue the job;
        ``(source_path, progress_path)``. :class:`InFlight` when a live
        progress file already claims this output or this user already has one
        running on the item; a stale one (its worker died) is replaced."""
        source_path, progress_path = prog.paths_for(output_path)
        if await self._alive_progress(item_id, progress_path) is not None:
            raise InFlight(f"a video is already being made at {output_path}")
        if (other := await self._running_for(item_id, user)) is not None:
            raise InFlight(f"you already have a video being made at {other}")
        now = self._now()
        await self.files.write(
            item_id, source_path, build_chat_export(title=title, messages=messages)
        )
        await self.files.write(
            item_id,
            progress_path,
            prog.Progress(
                stage="queued",
                expected_seconds=expected_seconds,
                started_at=now,
                heartbeat_at=now,
                output_path=output_path,
                requested_by=user,
            ).dumps(),
        )
        with self._job_rm.using(user=user):
            self._job_rm.create(
                ChatVideoJob(
                    payload=ChatVideoPayload(
                        item_id=item_id,
                        source_path=source_path,
                        output_path=output_path,
                        progress_path=progress_path,
                        options=options,
                    ),
                    partition_key=item_id,
                )
            )
        return source_path, progress_path

    async def _read_progress(self, item_id: str, path: str) -> prog.Progress | None:
        """The file as ours, or None: absent, or not one of ours."""
        try:
            raw = await self.files.read(item_id, path)
        except FileNotFound:
            return None
        try:
            return prog.Progress.loads(raw)
        except ValueError:
            return None

    async def _alive_progress(self, item_id: str, path: str) -> prog.Progress | None:
        p = await self._read_progress(item_id, path)
        if p is None or not prog.is_alive(
            p, now=self._now(), stale_after_seconds=self._limits.stale_after_seconds
        ):
            return None
        return p

    async def _running_for(self, item_id: str, user: str) -> str | None:
        """The output path of a job this user has alive on the item, if any —
        from the queue's own rows (bounded by jobs in flight), then the
        progress file they point at, which is the truth about liveness."""
        rows = self._job_rm.list_resources(QB["status"].in_(_ACTIVE).build())
        for row in rows:
            data = row.data
            assert isinstance(data, ChatVideoJob)
            payload = data.payload
            if payload.item_id != item_id or row.info.created_by != user:  # ty: ignore[unresolved-attribute]
                continue
            if await self._alive_progress(item_id, payload.progress_path) is not None:
                return payload.output_path
        return None

    # ── consumer ─────────────────────────────────────────────────────
    def _handle(self, job) -> None:  # job: Resource[ChatVideoJob]
        payload = job.data.payload
        assert isinstance(payload, ChatVideoPayload)
        requester = job.info.created_by
        body = self._run(payload, requester)
        asyncio.run_coroutine_threadsafe(body, self._loop_for_jobs()).result()

    def _loop_for_jobs(self) -> asyncio.AbstractEventLoop:
        """The one loop every job body runs on (see the module docstring):
        the running loop `start_consuming` was called under, else a loop of
        this coordinator's own, started on its thread the first time it is
        needed and stopped with the consumer."""
        with self._loop_lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=loop.run_forever, name="chat-video-jobs", daemon=True
                )
                thread.start()
                self._own_loop = (loop, thread)
                self._loop = loop
            return self._loop

    async def _run(self, payload: ChatVideoPayload, requester: str) -> None:
        item, files = payload.item_id, self.files
        started = self._now()
        stage: list[prog.Stage] = ["rendering"]
        stop = threading.Event()

        async def fail(sentence: str) -> None:
            logger.warning("chat-video: %s: %s", payload.output_path, sentence)
            if await self._read_progress(item, payload.progress_path) is None:
                return  # cancelled meanwhile: the file is gone, leave it gone
            await files.write(
                item,
                payload.progress_path,
                prog.Progress(
                    stage="failed",
                    expected_seconds=0,
                    started_at=started,
                    heartbeat_at=self._now(),
                    output_path=payload.output_path,
                    requested_by=requester,
                    error=sentence,
                ).dumps(),
            )

        if not self._may(item, requester):
            await fail("not authorized any more to read this item's files and add to them")
            return
        try:
            title, messages = parse_chat_export(await files.read(item, payload.source_path))
        except (FileNotFound, ValueError) as exc:
            await fail(f"the transcript {payload.source_path} could not be read: {exc}")
            return
        timeline = build_timeline(title=title, messages=messages, options=payload.options)
        # The CLI's `load_assets` rule: only the referenced files, and only
        # those that fit `max_asset_bytes` — decided from the size BEFORE the
        # read, so a 300 MB file the page would never inline is not pulled
        # into this pod's memory first. Absent ones are simply not in the
        # result (the page draws a card, or the alt text).
        fitting: list[str] = []
        for path in timeline.referenced_paths():
            size = await files.file_size(item, path)
            if size is not None and size <= payload.options.max_asset_bytes:
                fitting.append(path)
        assets = await files.read_many_existing(item, fitting)
        expected = round(timeline.playback_ms / 1000)

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self._limits.heartbeat_seconds)
                current = await self._read_progress(item, payload.progress_path)
                if current is None:
                    stop.set()  # deleted: the cancel
                    return
                now = self._now()
                await files.write(
                    item,
                    payload.progress_path,
                    prog.Progress(
                        stage=stage[0],
                        expected_seconds=expected,
                        elapsed_seconds=int((now - started).total_seconds()),
                        started_at=started,
                        heartbeat_at=now,
                        output_path=payload.output_path,
                        requested_by=requester,
                    ).dumps(),
                )

        def on_stage(name: str) -> None:
            stage[0] = "encoding" if name == "encoding" else "rendering"

        beat = asyncio.create_task(heartbeat())
        scratch = tempfile.mkdtemp(prefix="chat-video-")
        try:
            result = await asyncio.to_thread(
                self._render,
                title=title,
                messages=messages,
                options=payload.options,
                workdir=Path(scratch),
                assets=assets,
                should_stop=stop.is_set,
                on_stage=on_stage,
            )
        except Cancelled:
            return  # the file is gone; nothing to write, nothing to say
        except Exception as exc:  # noqa: BLE001 — the sentence is the job's whole report
            await fail(str(exc) or type(exc).__name__)
            return
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        if await self._read_progress(item, payload.progress_path) is None:
            return  # cancelled between the render's end and now: discard
        fmt = payload.options.fmt[0]
        data = result[fmt]
        if len(data) > self._limits.max_output_bytes:
            await fail(
                f"the {fmt} is {len(data):,} bytes; at most {self._limits.max_output_bytes:,} — "
                "choose mp4, a smaller size or a shorter range"
            )
            return
        try:
            await files.write(item, payload.output_path, data)
        except Exception as exc:  # noqa: BLE001 — WorkspaceFull, or the store's own refusal
            await fail(f"could not write {payload.output_path}: {exc}")
            return
        await files.delete(item, payload.progress_path)

    def _may(self, item_id: str, requester: str) -> bool:
        """The verdict the route reached at enqueue, reached again now, from
        the same primitive with the same inputs (the import coordinator's
        `_may_write`): the grant may have been withdrawn while the job
        queued, and the worker is the last one who can say no."""
        facts = load_access_facts(self._spec, item_id)
        if facts is None or facts.is_deleted:
            return False
        actor = Actor.human(requester, groups=groups_of(self._spec, requester))
        return all(
            authorize(
                actor,
                verb,
                facts.item.permission,
                created_by=facts.created_by,
                superusers=self._superusers,
            )
            for verb in ("read_content", "add_content")
        )

    # ── lifecycle (the shape every coordinator here shares) ──────────
    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            with contextlib.suppress(RuntimeError):
                self._loop = asyncio.get_running_loop()
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        self._ensure_consuming()

    @property
    def consuming(self) -> bool:
        return self._consuming

    def _stop_consuming(self) -> None:
        self._consuming = False
        self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]
        self._loop = None
        if self._own_loop is not None:
            loop, thread = self._own_loop
            self._own_loop = None
            loop.call_soon_threadsafe(loop.stop)
            thread.join()
            loop.close()

    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    async def aclose(self) -> None:
        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
