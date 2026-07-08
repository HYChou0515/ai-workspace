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

from msgspec.structs import replace as struct_replace
from specstar import QB, Schema, SpecStar
from specstar.types import TaskStatus

from ...kb.llm import ILlm
from ...resources import (
    CustomSanityQuestion,
    SanityResult,
    SanityVerdict,
    sanity_result_id,
    sanity_verdict_id,
)
from .jobs import SanityRun, SanityRunPayload
from .judge import judge_cell, judge_verdict
from .questions import (
    ALL_LEVELS,
    QUESTIONS,
    SanityQuestion,
    auto_run_cells,
    coverage_levels,
    messages_to_prompt,
    question_key,
    user,
)


def _custom_to_question(c: CustomSanityQuestion) -> SanityQuestion:
    """Adapt a user-authored ``CustomSanityQuestion`` to the in-code
    ``SanityQuestion`` shape so it runs/judges/covers exactly like a built-in —
    minus a mechanical grader (``grade=None`` ⇒ AI-only). Invalid effort strings
    are dropped; ``auto_run`` follows whether any valid level remains."""
    levels = tuple(lvl for lvl in c.levels if lvl in ALL_LEVELS)
    return SanityQuestion(
        category=c.category,
        messages=[user(c.prompt)],
        expected=c.expected,
        auto_run=bool(levels),
        auto_levels=levels,  # ty: ignore[invalid-argument-type]
        grade=None,
        aux=None,
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
        self._verdict_rm = spec.get_resource_manager(SanityVerdict)
        self._custom_rm = spec.get_resource_manager(CustomSanityQuestion)
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
        _LOGGER.info(
            "sanity: enqueued cell model=%s question=%s level=%s", model, question_key, level
        )

    def run_battery(self, model: str) -> None:
        """Queue the auto-run battery for one model (every auto_run question at
        its auto_levels)."""
        self._job_rm.create(
            SanityRun(payload=SanityRunPayload(model=model, scope="battery"), partition_key=model)
        )
        _LOGGER.info("sanity: enqueued battery for model=%s", model)

    # ── read (the FE hydrates the matrix from this) ──────────────────
    def list_results(self, model: str) -> list[SanityResult]:
        """Every stored cell for one model (the indexed query the FE matrix
        lists). Empty cells simply have no row yet."""
        return [
            r.data
            for r in self._result_rm.list_resources((QB["model"] == model).build())
            if isinstance(r.data, SanityResult)
        ]

    # ── question source: built-ins ∪ enabled custom (#231) ───────────
    def all_questions(self) -> list[SanityQuestion]:
        """Every question in the matrix: the built-in 19 plus the enabled
        user-authored ones (adapted to the same shape). The route's meta + the
        coverage/run/judge paths all read this so custom questions are first-class."""
        customs = [
            r.data
            for r in self._custom_rm.list_resources(QB.all().build())
            if isinstance(r.data, CustomSanityQuestion) and r.data.enabled
        ]
        return [*QUESTIONS, *(_custom_to_question(c) for c in customs)]

    def find_question(self, key: str) -> SanityQuestion | None:
        return next((q for q in self.all_questions() if question_key(q) == key), None)

    # ── custom-question CRUD (the 題目管理 panel's backend) ───────────
    def list_custom(self) -> list[tuple[str, CustomSanityQuestion]]:
        """Every user-authored question with its resource id (for edit/delete)."""
        return [
            (r.info.resource_id, r.data)  # ty: ignore[unresolved-attribute]
            for r in self._custom_rm.list_resources(QB.all().build())
            if isinstance(r.data, CustomSanityQuestion)
        ]

    def create_custom(self, cq: CustomSanityQuestion) -> str:
        return self._custom_rm.create(cq).resource_id

    def update_custom(self, qid: str, cq: CustomSanityQuestion) -> bool:
        if not self._custom_rm.exists(qid):
            return False
        self._custom_rm.update(qid, cq)
        return True

    def delete_custom(self, qid: str) -> bool:
        if not self._custom_rm.exists(qid):
            return False
        self._custom_rm.permanently_delete(qid)
        return True

    # ── coverage: fill the never-run blanks (#231) ───────────────────
    def run_missing(self, model: str, *, category: str | None = None) -> int:
        """Enqueue a cell job for every *expected* coverage cell of ``model`` that
        has no result yet (optionally narrowed to one 題組/``category``). Returns the
        count enqueued — this is the one-click 'fill all blanks'."""
        have = {(c.question_key, c.level) for c in self.list_results(model)}
        queued = 0
        for q in self.all_questions():
            if category is not None and q.category != category:
                continue
            for level in coverage_levels(q):
                if (question_key(q), level) not in have:
                    self.run_cell(model, question_key(q), level)
                    queued += 1
        _LOGGER.info("sanity: enqueued %d missing cell(s) for model=%s", queued, model)
        return queued

    def rescore(self, model: str) -> int:
        """Re-judge every existing cell of ``model`` against its STORED output (no
        model re-run) and refresh the verdict — the '重新 AI 評分' action. Returns
        the number of cells re-judged. No judge ⇒ 0."""
        if self._judge is None:
            _LOGGER.debug("sanity: rescore skipped for model=%s, no judge wired", model)
            return 0
        rejudged = 0
        for c in self.list_results(model):
            q = self.find_question(c.question_key)
            if c.error or q is None:
                continue  # nothing to judge / question gone
            ai_grade, ai_note = judge_cell(
                self._judge,
                prompt=messages_to_prompt(q.messages),
                expected=q.expected,
                output=c.output,
            )
            self._result_rm.update(
                sanity_result_id(model, c.question_key, c.level),
                struct_replace(c, ai_grade=ai_grade, ai_note=ai_note),
            )
            rejudged += 1
        self.generate_verdict(model)
        _LOGGER.info("sanity: rescored %d cell(s) for model=%s", rejudged, model)
        return rejudged

    # ── per-model fitness verdict (#231) ─────────────────────────────
    def list_verdicts(self) -> list[SanityVerdict]:
        """Every model's stored fitness verdict (the FE renders one card each)."""
        return [
            r.data
            for r in self._verdict_rm.list_resources(QB.all().build())
            if isinstance(r.data, SanityVerdict)
        ]

    def generate_verdict(self, model: str) -> None:
        """Judge reads ALL of ``model``'s cells and upserts its fitness verdict
        (score + summary). No judge / no cells / unusable reply ⇒ no-op."""
        if self._judge is None:
            return
        cells = self.list_results(model)
        if not cells:
            return
        score, summary = judge_verdict(self._judge, model=model, digest=self._verdict_digest(cells))
        if not summary:
            _LOGGER.warning("sanity: verdict skipped for model=%s, judge reply unusable", model)
            return  # the judge produced nothing usable — don't write a misleading verdict
        verdict = SanityVerdict(model=model, score=score, summary=summary)
        _LOGGER.info("sanity: upserting verdict for model=%s score=%s", model, score)
        rid = sanity_verdict_id(model)
        if self._verdict_rm.exists(rid):
            self._verdict_rm.update(rid, verdict)
        else:
            self._verdict_rm.create(verdict, resource_id=rid)

    def _verdict_digest(self, cells: list[SanityResult]) -> str:
        """A compact, judge-readable digest of one model's cells — one line per
        cell: 題組 | effort | 機械評分 | AI評分 | 回答節錄."""
        lines: list[str] = []
        for c in cells:
            q = self.find_question(c.question_key)
            cat = q.category if q is not None else "?"
            snippet = (c.output or c.error).replace("\n", " ")[:160]
            lines.append(
                f"[{cat}] {c.level} | 機械:{c.grade or '-'} | AI:{c.ai_grade or '-'} | {snippet}"
            )
        return "\n".join(lines)

    # ── consume (handler — runs on the consumer thread) ──────────────
    def _handle(self, job) -> None:  # job: Resource[SanityRun]
        payload = job.data.payload
        _LOGGER.info("sanity: handling job scope=%s model=%s", payload.scope, payload.model)
        if payload.scope == "battery":
            # #227: fan out one short cell job per cell instead of running the
            # whole battery inline. A full battery (every auto question × level,
            # each a live LLM call) can run for many minutes and trip RabbitMQ's
            # consumer-ack timeout; one cell per job keeps every job short, and
            # each cell upserts its own SanityResult so no join is needed.
            for q, level in auto_run_cells():
                self.run_cell(payload.model, question_key(q), level)
            _LOGGER.info("sanity: fanned out battery cells for model=%s", payload.model)
            return
        q = self.find_question(payload.question_key)
        if q is None:
            _LOGGER.info(
                "sanity: skip stale cell, question %s no longer exists", payload.question_key
            )
            return  # the prompt was edited away between enqueue and run
        self._run_one(payload.model, q, payload.level)

    def _run_one(self, model: str, q: SanityQuestion, level: str) -> None:
        """Run one cell through the real ``ILlm`` seam, grade it, and upsert the
        result (current-only). ``reasoned`` = the model emitted thinking, exactly
        as ``ILlm.stream`` flags it for kb_search."""
        key = question_key(q)
        _LOGGER.info("sanity: running cell model=%s question=%s level=%s", model, key, level)
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
        _LOGGER.info(
            "sanity: cell done model=%s question=%s level=%s grade=%s latency=%dms",
            model,
            key,
            level,
            grade,
            latency,
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
            _LOGGER.warning("sanity: aux extractor raised for %s", q.category, exc_info=True)
            return ""

    # ── lifecycle (mirror IndexCoordinator) ──────────────────────────
    def _active_count(self) -> int:
        return self._job_rm.count_resources(QB["status"].in_(_ACTIVE).build())

    def _ensure_consuming(self) -> None:
        if not self._consuming:
            self._consuming = True
            self._job_rm.start_consume(block=False)
            _LOGGER.info("sanity: background consumer started")

    def start_consuming(self) -> None:
        self._ensure_consuming()

    @property
    def consuming(self) -> bool:
        """Whether the background consumer is running (#312) — observable so the
        API's ``run_consumers`` gate can be asserted and a worker can report it
        is draining its JobType."""
        return self._consuming

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
        _LOGGER.info("sanity: background consumer stopped")
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
