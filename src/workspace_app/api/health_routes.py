"""Health-check + replay HTTP routes (#51 P3/P4) — the FE diagnostics
surfaces' API.

GET serves every REGISTERED check (the page lists all probes from
first paint, run or not) joined with its latest cached result; POST
triggers a round (all checks or one) asynchronously. One round at a
time — HealthService refuses overlap and the FE shows `running`.

The replay endpoints re-run one past LLM interaction (a turn's answer /
tool call, or a document's extraction) against the current model and
return the raw output for human comparison — pure probe, no tool
execution, no writes (plan Q4).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..health.replay import (
    ReplayInvalidTarget,
    ReplayResult,
    ReplayService,
    ReplayUnsupported,
)
from ..health.service import HealthService


class CheckRow(BaseModel):
    """One check as the diagnostics page renders it: identity always
    present; result fields None until the first run."""

    check_id: str
    description: str
    fast: bool
    status: str | None = None  # pass | fail | skip | error | None=never run
    detail: str = ""
    latency_ms: int | None = None
    checked_at: int | None = None  # epoch ms


class HealthOut(BaseModel):
    running: bool
    checks: list[CheckRow]


class _RunBody(BaseModel):
    check_id: str | None = None  # None = full round


class RunStartedOut(BaseModel):
    started: bool  # False = a round was already in flight


def register_health_routes(app: FastAPI, service: HealthService) -> None:
    @app.get("/health/checks")
    async def get_checks() -> HealthOut:
        latest = {r.check_id: r for r in service.results()}
        rows: list[CheckRow] = []
        for check in service.registry.checks():
            result = latest.get(check.check_id)
            rows.append(
                CheckRow(
                    check_id=check.check_id,
                    description=check.description,
                    fast=check.fast,
                    status=result.status if result else None,
                    detail=result.detail if result else "",
                    latency_ms=result.latency_ms if result else None,
                    checked_at=result.checked_at if result else None,
                )
            )
        return HealthOut(running=service.running, checks=rows)

    @app.post("/health/checks/run", status_code=202)
    async def run_checks(body: _RunBody) -> RunStartedOut:
        if body.check_id is not None and not any(
            c.check_id == body.check_id for c in service.registry.checks()
        ):
            raise HTTPException(status_code=404, detail=f"unknown check {body.check_id!r}")
        # Fire-and-forget: the round runs off the loop; the FE polls
        # GET /health/checks for progress (`running` + fresh results).
        task = asyncio.create_task(service.run_round(only=body.check_id))
        # A refused round (one already in flight) resolves immediately —
        # report it so the FE can say "already running".
        await asyncio.sleep(0)
        started = True if not task.done() else bool(task.result())
        return RunStartedOut(started=started)


# ── replay (#51 P4) ──────────────────────────────────────────────────


class ReplayToolCallOut(BaseModel):
    """A tool call the replayed model WANTED to make — intent only."""

    name: str
    arguments: dict[str, Any]


class ReplayOriginalOut(BaseModel):
    """The persisted message the replay re-derives, echoed for the
    side-by-side view."""

    role: str
    content: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


class ReplayRequestOut(BaseModel):
    """What the replay sent the model (#69 observability) — the tool-calling
    knobs an operator compares against the live turn's logged trace."""

    model: str
    endpoint: str
    tools: list[str] = []
    parallel_tool_calls: str = "unset"
    tool_choice: str = "auto (unset)"


class ReplayOut(BaseModel):
    text: str
    reasoning: str = ""
    tool_calls: list[ReplayToolCallOut] = []
    model: str = ""
    latency_ms: int = 0
    note: str = ""
    original: ReplayOriginalOut | None = None  # turn replays only
    request: ReplayRequestOut | None = None  # turn replays only


class _ReplayTurnBody(BaseModel):
    source: Literal["rca", "kb"]
    thread_id: str  # investigation id (rca) / kb chat id (kb)
    message_index: int


class _ReplayDocBody(BaseModel):
    document_id: str  # opaque SourceDoc id — never parsed


# What the route layer must look up for a turn replay: the thread's
# messages plus the agent assembly inputs the live turn would resolve
# (config / packages / template profile). `None` → unknown thread.
LoadTurn = Callable[[str, str], tuple[list[Any], Any, list[Any] | None, str | None] | None]
# Doc replay inputs: (path, mime, original blob). `None` → unknown doc.
LoadDoc = Callable[[str], tuple[str, str, bytes] | None]


def _to_out(result: ReplayResult, original: ReplayOriginalOut | None = None) -> ReplayOut:
    req = result.request
    return ReplayOut(
        text=result.text,
        reasoning=result.reasoning,
        tool_calls=[
            ReplayToolCallOut(name=tc.name, arguments=tc.arguments) for tc in result.tool_calls
        ],
        model=result.model,
        latency_ms=result.latency_ms,
        note=result.note,
        original=original,
        request=(
            ReplayRequestOut(
                model=req.model,
                endpoint=req.endpoint,
                tools=req.tools,
                parallel_tool_calls=req.parallel_tool_calls,
                tool_choice=req.tool_choice,
            )
            if req is not None
            else None
        ),
    )


def register_replay_routes(
    app: FastAPI,
    *,
    service: ReplayService | None,
    load_turn: LoadTurn,
    load_doc: LoadDoc,
) -> None:
    def _service() -> ReplayService:
        if service is None:
            raise HTTPException(
                status_code=503, detail="replay diagnostics are not available on this deployment"
            )
        return service

    @app.post("/health/replay/turn")
    async def replay_turn(body: _ReplayTurnBody) -> ReplayOut:
        svc = _service()
        loaded = load_turn(body.source, body.thread_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail=f"unknown thread {body.thread_id!r}")
        messages, config, packages, template_profile = loaded
        try:
            # Off the loop: a capability probe against a local model can
            # take tens of seconds; it must not block other requests.
            result = await asyncio.to_thread(
                svc.replay_turn,
                messages=messages,
                index=body.message_index,
                config=config,
                packages=packages,
                template_profile=template_profile,
            )
        except ReplayInvalidTarget as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        target = messages[body.message_index]
        return _to_out(
            result,
            ReplayOriginalOut(
                role=target.role,
                content=target.content,
                tool_name=target.tool_name,
                tool_args=target.tool_args,
            ),
        )

    @app.post("/health/replay/doc")
    async def replay_doc(body: _ReplayDocBody) -> ReplayOut:
        svc = _service()
        loaded = load_doc(body.document_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail=f"unknown document {body.document_id!r}")
        path, mime, blob = loaded
        try:
            result = await asyncio.to_thread(svc.replay_doc, path=path, mime=mime, blob=blob)
        except ReplayUnsupported as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _to_out(result)


# ─── model-sanity battery (Diagnostics matrix) ──────────────────────────────
class SanityLevelOut(BaseModel):
    level: str  # none | low | medium | high
    label: str  # Off | Low | Medium | High


class SanityQuestionOut(BaseModel):
    key: str  # stable hash of the messages (the cell's question_key)
    category: str
    messages: list[dict[str, str]]  # litellm/openai message list
    expected: str
    auto_run: bool
    auto_levels: list[str]


class SanityMetaOut(BaseModel):
    models: list[str]
    levels: list[SanityLevelOut]
    questions: list[SanityQuestionOut]


class SanityResultRow(BaseModel):
    """One filled matrix cell, FE-shaped (over the SanityResult resource)."""

    question_key: str
    level: str
    output: str
    reasoned: bool
    grade: str  # "pass" | "fail" | "" (eyeball)
    aux: str
    error: str
    latency_ms: int


class SanityRunBody(BaseModel):
    model: str
    scope: Literal["cell", "battery"] = "cell"
    question_key: str = ""  # required for scope=cell
    level: str = ""  # required for scope=cell


class SanityRunStartedOut(BaseModel):
    queued: bool


def register_sanity_routes(app: FastAPI, models: list[str], coordinator: Any) -> None:
    """The model-sanity matrix API: GET the question/level/model metadata (the FE
    draws the empty grid), POST a run (cell or auto battery). Cell results
    themselves are a specstar resource — the FE lists ``/sanity-result`` directly
    (auto route), so there's no custom read for them here."""
    from ..health.sanity.questions import (
        ALL_LEVELS,
        LEVEL_LABELS,
        QUESTIONS,
        find_question,
        question_key,
    )

    @app.get("/sanity/questions")
    async def get_sanity_meta() -> SanityMetaOut:
        return SanityMetaOut(
            models=list(models),
            levels=[SanityLevelOut(level=lvl, label=LEVEL_LABELS[lvl]) for lvl in ALL_LEVELS],
            questions=[
                SanityQuestionOut(
                    key=question_key(q),
                    category=q.category,
                    messages=q.messages,
                    expected=q.expected,
                    auto_run=q.auto_run,
                    auto_levels=list(q.auto_levels),
                )
                for q in QUESTIONS
            ],
        )

    @app.get("/sanity/results")
    async def get_sanity_results(model: str) -> list[SanityResultRow]:
        return [
            SanityResultRow(
                question_key=r.question_key,
                level=r.level,
                output=r.output,
                reasoned=r.reasoned,
                grade=r.grade,
                aux=r.aux,
                error=r.error,
                latency_ms=r.latency_ms,
            )
            for r in coordinator.list_results(model)
        ]

    @app.post("/sanity/run", status_code=202)
    async def run_sanity(body: SanityRunBody) -> SanityRunStartedOut:
        if body.scope == "cell":
            if find_question(body.question_key) is None:
                raise HTTPException(404, detail=f"unknown question {body.question_key!r}")
            if body.level not in ALL_LEVELS:
                raise HTTPException(422, detail=f"unknown level {body.level!r}")
            coordinator.run_cell(body.model, body.question_key, body.level)
        else:
            coordinator.run_battery(body.model)
        return SanityRunStartedOut(queued=True)
