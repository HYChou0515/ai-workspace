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
``format`` (verified both forward the param + return valid JSON). Non-streaming is
deliberate (structured output is one whole object; the feedback_always_stream rule's
accepted exception). Every sub-call still flows through litellm → the LlmCallLogger,
so each step stays observable.
"""

from __future__ import annotations

import json
from typing import Any

import litellm
from agents import FunctionTool
from agents.items import ModelResponse
from agents.models.chatcmpl_converter import Converter
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)

_FINAL = "final"


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
    ) -> None:
        self._inner = inner
        self._model = model
        self._base_url = base_url
        self._api_key = api_key

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

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
        )
        return resp.choices[0].message.content or ""

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
        messages = Converter.items_to_messages(input)
        if system_instructions:
            messages = [{"role": "system", "content": system_instructions}, *messages]

        fn_tools = {t.name: t for t in tools if isinstance(t, FunctionTool)}
        if fn_tools:
            msg = await self._decide_and_act(messages, fn_tools)
        else:  # no tools → just answer
            msg = ChatCompletionMessage(role="assistant", content=await self._free(messages))

        return ModelResponse(
            output=Converter.message_to_output_items(msg),
            usage=Usage(),
            response_id=None,
        )

    async def _decide_and_act(
        self, messages: list[Any], fn_tools: dict[str, FunctionTool]
    ) -> ChatCompletionMessage:
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
        action = decision.get("action") if isinstance(decision, dict) else None

        if action in fn_tools:
            tool = fn_tools[action]
            args = await self._structured(
                [
                    *messages,
                    {"role": "user", "content": f"Produce the arguments for `{action}` as JSON."},
                ],
                dict(tool.params_json_schema),
            )
            return ChatCompletionMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ChatCompletionMessageFunctionToolCall(
                        id="call_dta_1",
                        type="function",
                        function=Function(name=action, arguments=json.dumps(args)),
                    )
                ],
            )
        # action == "final" (or an unexpected value) → a free final answer.
        return ChatCompletionMessage(role="assistant", content=await self._free(messages))

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        # Decide-then-act is non-streaming (structured output). The runner routes
        # these turns through Runner.run (get_response); stream_response is never
        # called. Implemented to satisfy the abstract base.
        raise NotImplementedError("DecideThenActModel runs via get_response (non-streaming)")
