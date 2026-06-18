"""DecideThenActModel — a structured decide-then-act ``Model`` for the Agents SDK.

The native tool-calling path is unreliable on many models (small *and* large): for
content-heavy tool calls the model emits the args as plain text / invalid JSON, or
doesn't call the tool at all — and the streaming aggregator can mangle long arg
deltas. The "repair the args" approach (our old ``RepairingModel``/``args_recovery``,
and opencode's #29142) can't help when there's **no tool call to repair**, and worse,
guessing the model's intent (peeling concatenated JSON) silently runs a tool with the
wrong args.

This replaces "ask the model, hope for a clean native tool_call" with **structured
output, valid by construction**, at the ``Model.get_response`` seam the SDK already
calls (no loop rewrite, no monkey-patch):

  1. ``items_to_messages(input)`` — reuse the SDK converter.
  2. DECISION: a ``response_format`` enum over the tool names + ``"final"`` — the model
     is grammar-constrained to pick an action; it can't ramble prose instead.
  3. If a tool: ARGS via ``response_format`` = that tool's own ``params_json_schema`` —
     valid JSON by construction (incl. long ``content``); no guessing, no repair.
  4. Synthesize a ``ChatCompletionMessage`` carrying the tool_call and hand it back as a
     ``ModelResponse`` via ``message_to_output_items`` — the Runner then executes the
     tool, appends the result, and calls ``get_response`` again.

Provider-uniform: the decision + args calls are plain ``response_format`` json_schema
completions, which vLLM enforces via guided decoding and Ollama via its native
``format`` (verified both forward the param + return valid JSON). Only the
tool-call generation (decision + args) is non-streaming — that is the unreliable
long-arg path the structured calls fix, and a tool call renders as a card (not
token-streamed anyway). The **final text answer streams**: ``stream_response``
delegates to the inner ``LitellmModel``'s native streaming (with tools stripped so
it can only produce text), and a chosen tool call is emitted through
``ChatCmplStreamHandler`` so the Runner executes it. Every sub-call still flows
through litellm → the LlmCallLogger, so each step stays observable.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import litellm
from agents import FunctionTool
from agents.items import ModelResponse, TResponseStreamEvent
from agents.models.chatcmpl_converter import Converter
from agents.models.chatcmpl_stream_handler import ChatCmplStreamHandler
from agents.models.fake_id import FAKE_RESPONSES_ID
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessage
from openai.types.chat.chat_completion_chunk import (
    Choice as ChunkChoice,
)
from openai.types.chat.chat_completion_chunk import (
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from openai.types.responses import Response

from .reasoning import reasoning_off_kwargs

_FINAL = "final"
_TOOL_CALL_ID = "call_dta_1"


def _json_schema_format(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}


class DecideThenActModel(Model):
    """Wrap a model id + endpoint and drive turns via structured decide-then-act.

    ``inner`` is kept only so attribute reads (``.model``, the #69 trace, etc.) keep
    working; the actual generation is our own structured ``litellm`` calls against
    ``model``/``base_url``/``api_key`` (the same the inner ``LitellmModel`` would use)."""

    def __init__(
        self,
        inner: Model,
        *,
        model: str,
        base_url: str | None,
        api_key: str | None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._inner = inner
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        # "none" is the OFF signal → splat the provider-correct disable param
        # (Ollama think=False / others vLLM enable_thinking=False) into each
        # sub-call. low|medium|high|None leave thinking at the model default.
        self._reasoning_effort = reasoning_effort

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _reasoning_off(self) -> dict[str, Any]:
        """Disable-thinking kwargs when the level is the OFF signal, else ``{}``."""
        if self._reasoning_effort == "none":
            return reasoning_off_kwargs(self._model)
        return {}

    async def _structured(self, messages: list[Any], schema: dict[str, Any]) -> Any:
        """One non-streaming response_format json_schema completion → parsed object."""
        resp = await litellm.acompletion(
            model=self._model,
            messages=messages,
            base_url=self._base_url,
            api_key=self._api_key,
            response_format=_json_schema_format(schema, "out"),
            stream=False,
            temperature=0,
            **self._reasoning_off(),
        )
        return json.loads(resp.choices[0].message.content or "{}")

    async def _free(self, messages: list[Any]) -> str:
        """An unconstrained (final-answer) completion — no schema."""
        resp = await litellm.acompletion(
            model=self._model,
            messages=messages,
            base_url=self._base_url,
            api_key=self._api_key,
            stream=False,
            **self._reasoning_off(),
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _prep(input: Any, system_instructions: str | None) -> list[Any]:
        """Shared turn setup: input → messages with the system prompt prepended."""
        messages = Converter.items_to_messages(input)
        if system_instructions:
            messages = [{"role": "system", "content": system_instructions}, *messages]
        return messages

    async def _decide(self, messages: list[Any], fn_tools: dict[str, FunctionTool]) -> str | None:
        """The DECISION step (structured, non-stream): pick a tool name or ``final``."""
        names = list(fn_tools)
        decision = await self._structured(
            [
                *messages,
                {
                    "role": "user",
                    "content": (
                        f"Decide the SINGLE next step. Available tools: {names}. Choose "
                        f"{_FINAL!r} when the task is already done (e.g. the file was written)."
                    ),
                },
            ],
            {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": [*names, _FINAL]}},
                "required": ["action"],
                "additionalProperties": False,
            },
        )
        return decision.get("action") if isinstance(decision, dict) else None

    async def _tool_args_json(self, messages: list[Any], tool: FunctionTool, action: str) -> str:
        """The ARGS step (structured, non-stream): valid JSON for ``action`` by construction."""
        args = await self._structured(
            [
                *messages,
                {"role": "user", "content": f"Produce the arguments for `{action}` as JSON."},
            ],
            dict(tool.params_json_schema),
        )
        return json.dumps(args)

    async def get_response(  # noqa: PLR0913 — matches the SDK Model interface
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: Any,
        tools: list[Any],
        output_schema: Any,
        handoffs: Any,
        tracing: Any,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: Any = None,
        **kwargs: Any,
    ) -> ModelResponse:
        messages = self._prep(input, system_instructions)
        fn_tools = {t.name: t for t in tools if isinstance(t, FunctionTool)}

        msg: ChatCompletionMessage
        action = await self._decide(messages, fn_tools) if fn_tools else _FINAL
        if action in fn_tools:
            args_json = await self._tool_args_json(messages, fn_tools[action], action)
            msg = ChatCompletionMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ChatCompletionMessageFunctionToolCall(
                        id=_TOOL_CALL_ID,
                        type="function",
                        function=Function(name=action, arguments=args_json),
                    )
                ],
            )
        else:  # action == "final" / no tools / unexpected → a free final answer.
            msg = ChatCompletionMessage(role="assistant", content=await self._free(messages))

        return ModelResponse(
            output=Converter.message_to_output_items(msg),
            usage=Usage(),
            response_id=None,
        )

    async def stream_response(  # noqa: PLR0913 — matches the SDK Model interface
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: Any,
        tools: list[Any],
        output_schema: Any,
        handoffs: Any,
        tracing: Any,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[TResponseStreamEvent]:
        """Stream a turn: DECISION + ARGS stay structured/non-stream (the unreliable
        long-arg path), but the FINAL text answer streams.

        - ``final`` / no tools → delegate to the inner ``LitellmModel``'s native
          streaming so the answer streams token-by-token; tools are stripped so the
          inner model can only produce text (it won't try a native tool call).
        - a chosen tool → emit the function call via ``ChatCmplStreamHandler`` (a
          one-chunk fake stream) so the function_call + ResponseCompletedEvent are
          shaped exactly as the SDK Runner expects and the tool gets executed."""
        messages = self._prep(input, system_instructions)
        fn_tools = {t.name: t for t in tools if isinstance(t, FunctionTool)}

        action = await self._decide(messages, fn_tools) if fn_tools else _FINAL

        if action not in fn_tools:
            # FINAL: stream the answer natively from the inner model. Pass tools=[]
            # so it can only emit text (never a native tool_call we'd have to parse).
            async for ev in self._inner.stream_response(  # type: ignore[attr-defined]
                system_instructions,
                input,
                model_settings,
                [],
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            ):
                yield ev
            return

        # TOOL: structured ARGS, then replay the call through the shared stream
        # handler so the Runner extracts + executes it just like a native tool_call.
        args_json = await self._tool_args_json(messages, fn_tools[action], action)
        initial = Response(
            id=FAKE_RESPONSES_ID,
            created_at=time.time(),
            model=self._model,
            object="response",
            output=[],
            tool_choice="auto",
            top_p=None,
            temperature=None,
            tools=[],
            parallel_tool_calls=False,
        )

        async def _fake_stream() -> AsyncIterator[ChatCompletionChunk]:
            yield ChatCompletionChunk(
                id=_TOOL_CALL_ID,
                created=int(time.time()),
                model=self._model,
                object="chat.completion.chunk",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChoiceDelta(
                            role="assistant",
                            tool_calls=[
                                ChoiceDeltaToolCall(
                                    index=0,
                                    id=_TOOL_CALL_ID,
                                    type="function",
                                    function=ChoiceDeltaToolCallFunction(
                                        name=action, arguments=args_json
                                    ),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
            )
            yield ChatCompletionChunk(
                id=_TOOL_CALL_ID,
                created=int(time.time()),
                model=self._model,
                object="chat.completion.chunk",
                choices=[ChunkChoice(index=0, delta=ChoiceDelta(), finish_reason="tool_calls")],
            )

        # handle_stream only does `async for chunk in stream`, so a plain async
        # generator is a fine substitute for the declared AsyncStream.
        async for ev in ChatCmplStreamHandler.handle_stream(
            initial,
            _fake_stream(),  # ty: ignore[invalid-argument-type]
            model=self._model,
        ):
            yield ev
