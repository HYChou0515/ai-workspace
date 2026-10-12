"""docs/plan-friendly-errors.md P8 (D13): every failure the plan names, made for
real by an HTTP endpoint and met through the path a turn takes —
`LitellmAgentRunner` → `CallRetryModel` → the SDK's `LitellmModel` → litellm →
its async HTTP client.

The rest of this feature's tests use doubles that raise at the model layer, so
"a dropped connection arrives as an exception" was a premise built into the
double. It was false: litellm's aiohttp transport turns a mid-answer drop into
a normal end, and its wrappers put an invented 5xx over a refused connection.
Both live under every double; this file is the gate that can see them. A
litellm upgrade or a transport change that alters a shape reddens it."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import MessageDelta, OutputRetracted, RunError
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.resources import AgentConfig

Reply = Callable[[], Response]


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    body = {
        "id": "chatcmpl-shape",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "demo",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n"


def _say(text: str) -> Reply:
    async def body() -> AsyncIterator[str]:
        yield _chunk({"role": "assistant", "content": ""})
        for word in text.split(" "):
            yield _chunk({"content": word + " "})
            await asyncio.sleep(0.01)
        yield _chunk({}, "stop")
        yield "data: [DONE]\n\n"

    return lambda: StreamingResponse(body(), media_type="text/event-stream")


def _drop_after(text: str) -> Reply:
    """Stream some text, then close the connection mid-body."""

    async def body() -> AsyncIterator[str]:
        yield _chunk({"role": "assistant", "content": ""})
        yield _chunk({"content": text})
        await asyncio.sleep(0.05)
        raise ConnectionResetError("dropped mid-answer")

    return lambda: StreamingResponse(body(), media_type="text/event-stream")


def _stall_after(text: str) -> Reply:
    """Stream some text, then go silent (no bytes, connection kept open)."""

    async def body() -> AsyncIterator[str]:
        yield _chunk({"role": "assistant", "content": ""})
        yield _chunk({"content": text})
        await asyncio.sleep(5)
        yield "data: [DONE]\n\n"

    return lambda: StreamingResponse(body(), media_type="text/event-stream")


def _silent() -> Reply:
    """Accept the request and never send a byte."""

    async def body() -> AsyncIterator[str]:
        await asyncio.sleep(5)
        yield "data: [DONE]\n\n"

    return lambda: StreamingResponse(body(), media_type="text/event-stream")


def _status(status: int, message: str, err_type: str, **extra: Any) -> Reply:
    headers = extra.pop("headers", None)
    return lambda: JSONResponse(
        {"error": {"message": message, "type": err_type, **extra}},
        status_code=status,
        headers=headers,
    )


class _Endpoint:
    """An OpenAI-compatible endpoint that plays one reply per request, in order,
    repeating the last, and counts the requests it got."""

    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.hits = 0
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def completions(_: Request) -> Response:
            reply = self.replies[min(self.hits, len(self.replies) - 1)]
            self.hits += 1
            return reply()

        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> _Endpoint:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            assert time.monotonic() < deadline, "stand-in endpoint did not start"
            time.sleep(0.01)
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def base_url(self) -> str:
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/v1"


@pytest.fixture
def endpoint() -> Iterator[_Endpoint]:
    with _Endpoint() as e:
        yield e


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


async def _no_sleep(_s: float) -> None:
    return None


async def _turn(base_url: str) -> list[Any]:
    # The bounds production always sets (`factories.py`: total_deadline_s,
    # idle_timeout_s), shortened so a silent endpoint costs a second, not minutes.
    runner = LitellmAgentRunner(sleep=_no_sleep, max_retries=2, stream_deadlines=(1.0, 0.5))
    config = AgentConfig(name="t", model="openai/demo", llm_base_url=base_url, llm_api_key="x")
    ctx = AgentToolContext(investigation_id="i", agent_config=config)
    return [ev async for ev in runner.run("go", ctx)]


def _answer(events: list[Any]) -> str:
    text = ""
    for ev in events:
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            text += ev.text
        elif isinstance(ev, OutputRetracted):
            text = text[: len(text) - ev.text_chars]
    return text.strip()


def _kinds(events: list[Any]) -> list[str | None]:
    return [e.kind for e in events if isinstance(e, RunError)]


FULL = "the whole answer"

# (case, replies in order, the turn's error kinds, requests the endpoint got)
RECOVERED = [
    (
        "429 then an answer",
        [_status(429, "slow down", "rate_limit_error", headers={"retry-after": "1"}), _say(FULL)],
        2,
    ),
    ("503 then an answer", [_status(503, "busy", "server_error"), _say(FULL)], 2),
    ("drop mid-answer then an answer", [_drop_after("half an "), _say(FULL)], 2),
    ("stall mid-answer then an answer", [_stall_after("half an "), _say(FULL)], 2),
    ("silent then an answer", [_silent(), _say(FULL)], 2),
]

GIVEN_UP = [
    ("503 every time", [_status(503, "busy", "server_error")], "provider_error", 3),
    (
        "401",
        [
            _status(
                401, "Incorrect API key provided", "invalid_request_error", code="invalid_api_key"
            )
        ],
        "auth",
        1,
    ),
    (
        "400",
        [_status(400, "Unrecognized request argument supplied: foo", "invalid_request_error")],
        "bad_request",
        1,
    ),
    ("drop mid-answer every time", [_drop_after("half an ")], "connection", 3),
    ("stall mid-answer every time", [_stall_after("half an ")], "timeout", 3),
    ("silent every time", [_silent()], "timeout", 3),
]


@pytest.mark.parametrize(("case", "replies", "hits"), RECOVERED, ids=[c[0] for c in RECOVERED])
async def test_a_failure_that_clears_is_resent_and_the_answer_is_whole(
    endpoint, case, replies, hits
):
    endpoint.replies = replies

    events = await _turn(endpoint.base_url)

    assert _kinds(events) == [], case
    assert _answer(events) == FULL, case
    assert endpoint.hits == hits, case


@pytest.mark.parametrize(
    ("case", "replies", "kind", "hits"), GIVEN_UP, ids=[c[0] for c in GIVEN_UP]
)
async def test_a_failure_that_does_not_clear_ends_the_turn_named(
    endpoint, case, replies, kind, hits
):
    endpoint.replies = replies

    events = await _turn(endpoint.base_url)

    assert _kinds(events) == [kind], case
    assert endpoint.hits == hits, case


async def test_an_endpoint_nobody_listens_on_is_named_as_unreachable():
    events = await _turn(_closed_port_url())

    assert _kinds(events) == ["connection"]
