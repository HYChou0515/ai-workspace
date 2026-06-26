"""SanityBatteryCoordinator — enqueue + background-consume model-sanity runs.

Mirrors ``IndexCoordinator`` (#82): the auto ``POST /sanity-run`` route (or
``run_cell`` / ``run_battery`` here) enqueues a ``SanityRun`` job and returns;
a background consumer drains it on its own thread, runs the model, grades the
answer, and upserts the ``SanityResult`` cell. ``partition_key`` = model, so a
model's cells run serially.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable

from specstar import QB, Schema, SpecStar
from specstar.types import TaskStatus

from ...kb.llm import ILlm
from ...resources import SanityResult, sanity_result_id
from .jobs import SanityRun, SanityRunPayload
from .judge import judge_cell
from .questions import (
    SanityQuestion,
    auto_run_cells,
    find_question,
    messages_to_prompt,
    question_key,
)

_LOGGER = logging.getLogger(__name__)
_ACTIVE = [TaskStatus.PENDING, TaskStatus.PROCESSING]
_DRAIN_INTERVAL = 0.02

# (model, reasoning level) → the SAME ``ILlm`` kb_search uses (production:
# ``LitellmLlm`` with that model + endpoint + reasoning_effort). Injecting a
# factory keeps the battery on the real seam — tests pass a fake ``ILlm``.
LlmFactory = Callable[[str, str], ILlm]


class SanityBatteryCoordinator:
    def __init__(
        self,
        spec: SpecStar,
        llm_factory: LlmFactory,
        *,
        judge: ILlm | None = None,
        message_queue_factory: object | None = None,
    ) -> None:
        self._spec = spec
        self._llm_factory = llm_factory
        # #231: optional LLM-as-judge. None ⇒ AI scoring off (ai_grade/ai_note
        # stay empty). When wired, every cell is judged after it runs.
        self._judge = judge
        self._result_rm = spec.get_resource_manager(SanityResult)
        if message_queue_factory is None:
            from specstar.message_queue import SimpleMessageQueueFactory

            message_queue_factory = SimpleMessageQueueFactory()
        spec.add_model(
            Schema(SanityRun, "v1"),
            job_handler=self._handle,
            indexed_fields=["status", "partition_key"],
            message_queue_factory=message_queue_factory,  # ty: ignore[invalid-argument-type]
        )
        self._job_rm = spec.get_resource_manager(SanityRun)
        self._consuming = False

    # ── enqueue (producer) ───────────────────────────────────────────
    def run_cell(self, model: str, question_key: str, level: str) -> None:
        """Queue one matrix cell (model × question × level)."""
        self._job_rm.create(
            SanityRun(
                payload=SanityRunPayload(
                    model=model, scope="cell", question_key=question_key, level=level
                ),
                partition_key=model,
            )
        )

    def run_battery(self, model: str) -> None:
        """Queue the auto-run battery for one model (every auto_run question at
        its auto_levels)."""
        self._job_rm.create(
            SanityRun(payload=SanityRunPayload(model=model, scope="battery"), partition_key=model)
        )

    # ── read (the FE hydrates the matrix from this) ──────────────────
    def list_results(self, model: str) -> list[SanityResult]:
        """Every stored cell for one model (the indexed query the FE matrix
        lists). Empty cells simply have no row yet."""
        return [
            r.data
            for r in self._result_rm.list_resources((QB["model"] == model).build())
            if isinstance(r.data, SanityResult)
        ]

    # ── consume (handler — runs on the consumer thread) ──────────────
    def _handle(self, job) -> None:  # job: Resource[SanityRun]
        payload = job.data.payload
        if payload.scope == "battery":
            # #227: fan out one short cell job per cell instead of running the
            # whole battery inline. A full battery (every auto question × level,
            # each a live LLM call) can run for many minutes and trip RabbitMQ's
            # consumer-ack timeout; one cell per job keeps every job short, and
            # each cell upserts its own SanityResult so no join is needed.
            for q, level in auto_run_cells():
                self.run_cell(payload.model, question_key(q), level)
            return
        q = find_question(payload.question_key)
        if q is None:
            return  # the prompt was edited away between enqueue and run
        self._run_one(payload.model, q, payload.level)

    def _run_one(self, model: str, q: SanityQuestion, level: str) -> None:
        """Run one cell through the real ``ILlm`` seam, grade it, and upsert the
        result (current-only). ``reasoned`` = the model emitted thinking, exactly
        as ``ILlm.stream`` flags it for kb_search."""
        key = question_key(q)
        output, reasoned, latency, error = "", False, 0, ""
        try:
            llm = self._llm_factory(model, level)
            start = time.monotonic()
            parts: list[str] = []
            for text, is_reasoning in llm.stream(messages_to_prompt(q.messages)):
                if is_reasoning:
                    reasoned = True
                else:
                    parts.append(text)
            output = "".join(parts).strip()
            latency = int((time.monotonic() - start) * 1000)
        except Exception as exc:  # noqa: BLE001 — a live model error becomes the cell's error
            output, reasoned, latency = "", False, 0
            error = f"{type(exc).__name__}: {exc!s}"[:240]
            _LOGGER.warning("sanity run failed for %s / %s / %s: %s", model, key, level, exc)

        grade = self._grade(q, output) if not error else ""
        aux = self._aux(q, output) if not error else ""
        # #231: AI judge grades the answer (when wired + the run produced output).
        ai_grade, ai_note = "", ""
        if self._judge is not None and not error:
            ai_grade, ai_note = judge_cell(
                self._judge,
                prompt=messages_to_prompt(q.messages),
                expected=q.expected,
                output=output,
            )
        result = SanityResult(
            model=model,
            question_key=key,
            level=level,
            output=output,
            reasoned=reasoned,
            grade=grade,
            ai_grade=ai_grade,
            ai_note=ai_note,
            aux=aux,
            error=error,
            latency_ms=latency,
        )
        rid = sanity_result_id(model, key, level)
        if self._result_rm.exists(rid):
            self._result_rm.update(rid, result)
        else:
            self._result_rm.create(result, resource_id=rid)

    @staticmethod
    def _grade(q: SanityQuestion, output: str) -> str:
        if q.grade is None:
            return ""  # eyeball-only question → no dot
        try:
            return "pass" if q.grade(output) else "fail"
        except Exception:  # noqa: BLE001 — a grader bug must not wreck the cell
            _LOGGER.warning("sanity grader raised for %s", q.category, exc_info=True)
            return ""

    @staticmethod
    def _aux(q: SanityQuestion, output: str) -> str:
        if q.aux is None:
            return ""
        try:
            return q.aux(output)
        except Exception:  # noqa: BLE001
            return ""

    # ── lifecycle (mirror IndexCoordinator) ──────────────────────────
    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)

    def start_consuming(self) -> None:
        self._ensure_consuming()

    def wait_idle(self, timeout: float = 30.0) -> None:
        """Block until the queue drains, without stopping the consumer (a sync
        drain point for tests + graceful ops)."""
        self._ensure_consuming()
        deadline = time.monotonic() + timeout
        while self._active_count() != 0:
            if time.monotonic() >= deadline:  # pragma: no cover — safety net
                raise TimeoutError(f"sanity queue did not drain within {timeout:.0f}s")
            time.sleep(_DRAIN_INTERVAL)

    def _stop_consuming(self) -> None:
        self._consuming = False
        with contextlib.suppress(RuntimeError):
            self._job_rm.message_queue.stop_consuming()  # ty: ignore[unresolved-attribute]

    async def aclose(self) -> None:
        import asyncio

        if self._active_count() == 0 and not self._consuming:
            return
        self._ensure_consuming()
        while self._active_count() != 0:
            await asyncio.sleep(_DRAIN_INTERVAL)
        self._stop_consuming()
