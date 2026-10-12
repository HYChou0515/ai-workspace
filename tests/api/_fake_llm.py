"""A provider stand-in that speaks the REAL Responses stream events, so a test
can drive `LitellmAgentRunner.run` through the real `Runner.run_streamed`.

Swapped in for `litellm_runner.LitellmModel`, it sits where the provider does —
under every wrapper the runner builds — so what is under test is the runner's
own composition, not a re-statement of it. Each model call plays the next step
of the script."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from agents.models.interface import Model
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)


@dataclass
class Say:
    """Answer with text, streamed as these deltas."""

    deltas: list[str]


@dataclass
class Call:
    """Call a tool."""

    name: str
    args: dict[str, Any]
    call_id: str = "call-1"


@dataclass
class Fail:
    """Stream these deltas, then raise."""

    exc: BaseException
    deltas: list[str] = field(default_factory=list)


Step = Say | Call | Fail
#: A step decided by what the call was sent — "reject while it is too long".
Judge = Callable[[list[Any]], Step]


def _response(output: list[Any]) -> Response:
    return Response(
        id="resp",
        created_at=0,
        model="fake",
        object="response",
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
    )


class ScriptedLlm(Model):
    def __init__(self, steps: list[Step | Judge] | Judge) -> None:
        #: A lone judge answers every call.
        self.steps: list[Step | Judge] | Judge = steps if callable(steps) else list(steps)
        self.inputs: list[Any] = []

    def __call__(self, *_a: Any, **_k: Any) -> ScriptedLlm:
        # Stands in for the `LitellmModel` CLASS: every construction is this one
        # script, however many models the runner builds.
        return self

    async def get_response(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("the runner streams")

    async def stream_response(
        self, system_instructions: Any, input: Any, *_a: Any, **_k: Any
    ) -> AsyncIterator[Any]:
        sent = json.loads(json.dumps(input, default=str))
        self.inputs.append(sent)
        next_step = self.steps.pop(0) if isinstance(self.steps, list) else self.steps
        if isinstance(next_step, (Say, Call, Fail)):
            step: Step = next_step
        else:
            judge = cast("Judge", next_step)
            step = judge(sent if isinstance(sent, list) else [sent])
        seq = 0
        yield ResponseCreatedEvent(
            response=_response([]), sequence_number=seq, type="response.created"
        )
        deltas = step.deltas if isinstance(step, (Say, Fail)) else []
        for d in deltas:
            seq += 1
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=d,
                item_id="msg",
                logprobs=[],
                output_index=0,
                sequence_number=seq,
                type="response.output_text.delta",
            )
            # A real stream has the network between deltas, so a reader sees
            # each one before whatever comes next. Without this yield the SDK
            # surfaces a raise before the deltas ahead of it were ever read —
            # a stream nobody saw, which is not the case under test.
            await asyncio.sleep(0.01)
        if isinstance(step, Fail):
            raise step.exc
        if isinstance(step, Say):
            item: Any = ResponseOutputMessage(
                id="msg",
                content=[
                    ResponseOutputText(
                        annotations=[], text="".join(step.deltas), type="output_text"
                    )
                ],
                role="assistant",
                status="completed",
                type="message",
            )
        else:
            item = ResponseFunctionToolCall(
                arguments=json.dumps(step.args),
                call_id=step.call_id,
                name=step.name,
                type="function_call",
                id="fc",
            )
        seq += 1
        yield ResponseOutputItemDoneEvent(
            item=item, output_index=0, sequence_number=seq, type="response.output_item.done"
        )
        seq += 1
        yield ResponseCompletedEvent(
            response=_response([item]), sequence_number=seq, type="response.completed"
        )
