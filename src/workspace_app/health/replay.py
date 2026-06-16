"""ReplayService (#51 P4) — re-run a past LLM interaction's context
against the CURRENT model and hand back the raw output for human
comparison.

Q4 contract (docs/plan-sanity-checks.md): replay = `(context snapshot,
target LLM) → raw output`. The service NEVER executes a tool and NEVER
writes state — a tool-call in the response is reported as INTENT only.
To judge the model's reaction to a tool output, the history's persisted
output is folded into the context; the tool itself stays untouched.

Faithfulness over convenience: the context is rebuilt by the SAME code
paths the live surfaces use (`api.turns.history_items` for dialogue,
the runner's agent assembly for system prompt + tool schemas), so a
replay failure means "this model can't do what that turn needed" — not
"the replay harness prompts differently".
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any


class ReplayInvalidTarget(ValueError):
    """The requested message can't be replayed — out of range, or not a
    model-produced message (only assistant answers and tool calls have
    an LLM interaction behind them)."""


class ReplayUnsupported(Exception):
    """This document's processing has no LLM interaction to replay (or
    the component that would run it isn't configured)."""


@dataclass(frozen=True)
class ReplayToolCall:
    """A tool call the replayed model WANTED to make — never executed."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ReplayRequest:
    """What the replay actually sent the model — the tool-calling knobs an
    operator compares against the live turn's logged trace (#69). The
    replay leaves `parallel_tool_calls` / `tool_choice` unset, matching the
    post-#69 live runner; a difference here means a config drift."""

    model: str
    endpoint: str
    tools: list[str]
    parallel_tool_calls: str = "unset"
    tool_choice: str = "auto (unset)"


@dataclass(frozen=True)
class ReplayResult:
    """The model's raw response to the rebuilt context."""

    text: str
    reasoning: str = ""
    tool_calls: list[ReplayToolCall] = field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    note: str = ""
    request: ReplayRequest | None = None  # turn replays only


def _intent(slot: dict[str, str]) -> ReplayToolCall:
    """One accumulated tool-call intent. Unparseable args stay visible
    as `{"_raw": …}` — replay exists to SHOW a model emitting malformed
    JSON, not to hide it."""
    import json

    try:
        args = json.loads(slot["arguments"]) if slot["arguments"] else {}
        if not isinstance(args, dict):
            args = {"_raw": slot["arguments"]}
    except json.JSONDecodeError:
        args = {"_raw": slot["arguments"]}
    return ReplayToolCall(name=slot["name"], arguments=args)


class ReplayService:
    def __init__(
        self,
        *,
        completion: Callable[..., Iterator[Any]] | None = None,
        kb_llm: Any | None = None,  # kb.llm.ILlm — drives chat-export extraction
        describer: Any | None = None,  # kb.vlm.VlmDescriber — drives image description
        default_base_url: str | None = None,
        default_api_key: str | None = None,
    ) -> None:
        if completion is None:  # pragma: no cover — bound only in production wiring
            import litellm

            completion = litellm.completion
        self._completion = completion
        self._kb_llm = kb_llm
        self._describer = describer
        self._default_base_url = default_base_url
        self._default_api_key = default_api_key

    def replay_doc(self, *, path: str, mime: str, blob: bytes) -> ReplayResult:
        """Replay the LLM step of a document's ingestion: insight
        extraction for chat exports, the VLM description for images.
        Dry-run — nothing is re-indexed, no chunk is written."""
        from ..kb.chat_export import is_chat_export

        if is_chat_export(path):
            return self._replay_chat_export(path=path, blob=blob)
        return self._replay_image(path=path, mime=mime, blob=blob)

    def _replay_chat_export(self, *, path: str, blob: bytes) -> ReplayResult:
        # Same pipeline ChatExportParser runs at ingest, minus the
        # node-building tail: parse the export, serialise the transcript,
        # resolve the extraction prompt — then hand the model's RAW
        # response back instead of silently parsing it.
        from ..kb.chat_export import CHAT_EXPORT_SUFFIX, parse_chat_export
        from ..kb.insight_extractor import _parse_insights, extraction_prompt
        from ..kb.insight_extractor import conversation_to_extraction_doc as to_doc

        if self._kb_llm is None:
            raise ReplayUnsupported(
                "insight extraction isn't configured on this deployment "
                "(no knowledge-base model) — nothing to replay"
            )
        title, messages = parse_chat_export(blob)
        stem = path.rsplit("/", 1)[-1][: -len(CHAT_EXPORT_SUFFIX)]
        conv_doc = to_doc(investigation_id=stem, title=title, messages=messages)
        prompt = extraction_prompt(conv_doc.get_content())
        started = time.perf_counter()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for chunk, is_reasoning in self._kb_llm.stream(prompt):
            (reasoning_parts if is_reasoning else text_parts).append(chunk)
        text = "".join(text_parts)
        n = len(_parse_insights(text, max_n=99))
        note = (
            f"{n} insight(s) would be extracted from this response"
            if n
            else "no insights would be extracted from this response — "
            "indexing it would produce zero searchable chunks"
        )
        return ReplayResult(
            text=text,
            reasoning="".join(reasoning_parts),
            latency_ms=int((time.perf_counter() - started) * 1000),
            note=note,
        )

    def _replay_image(self, *, path: str, mime: str, blob: bytes) -> ReplayResult:
        from ..kb.parsers.vlm_image import _IMAGE_EXTENSIONS, _IMAGE_MIMES

        if not (mime in _IMAGE_MIMES or path.lower().endswith(_IMAGE_EXTENSIONS)):
            raise ReplayUnsupported("this document's processing has no AI step to replay")
        if self._describer is None:
            raise ReplayUnsupported(
                "image description isn't configured on this deployment "
                "(no vision model) — nothing to replay"
            )
        started = time.perf_counter()
        # Identical to VlmImageParser.parse: same prompt template, same
        # mime fallback, same context line.
        text = self._describer.describe(
            blob,
            mime if mime in _IMAGE_MIMES else "image/png",
            context=f"the uploaded image {path}",
        )
        return ReplayResult(
            text=text,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def replay_turn(
        self,
        *,
        messages: Sequence[Any],
        index: int,
        config: Any,
        packages: list[Any] | None = None,
        template_profile: str | None = None,
    ) -> ReplayResult:
        """Replay the model interaction that produced ``messages[index]``
        (an assistant answer or a tool call): context = everything before
        it, assembled exactly as the runner would."""
        if not 0 <= index < len(messages):
            raise ReplayInvalidTarget(f"message index {index} out of range")
        target = messages[index]
        if target.role not in ("assistant", "tool"):
            raise ReplayInvalidTarget(
                f"message {index} is a {target.role!r} message — only model-produced "
                "messages (assistant / tool) can be replayed"
            )
        # The runner's OWN agent assembly (`_agent_for`) — deliberately
        # the private function, not a re-implementation: system prompt
        # (incl. the tool-inventory section) and tool schemas must be
        # bit-identical to what the live turn saw, or a replay diff
        # would implicate the harness instead of the model. Lazy import:
        # `api` imports `health` (routes) — module-load import would
        # cycle. Same for ThinkSplitter (Qwen3 inline `<think>` tags).
        from agents import FunctionTool

        from ..api.litellm_runner import ThinkSplitter, _agent_for

        agent = _agent_for(config, packages, template_profile=template_profile)
        sent: list[dict[str, Any]] = []
        if agent.instructions:
            sent.append({"role": "system", "content": agent.instructions})
        sent.extend(self._history(messages[:index]))
        tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.params_json_schema,
                },
            }
            # Everything we build IS a FunctionTool — the isinstance is
            # a `ty` narrowing for the SDK's wider Tool union.
            for t in agent.tools
            if isinstance(t, FunctionTool)
        ]

        started = time.perf_counter()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        intents: dict[int, dict[str, str]] = {}
        splitter = ThinkSplitter()
        for chunk in self._completion(
            model=config.model,
            messages=sent,
            tools=tools or None,
            stream=True,
            api_base=config.llm_base_url or self._default_base_url,
            api_key=config.llm_api_key or self._default_api_key,
        ):
            delta = chunk.choices[0].delta
            # Some providers stream reasoning as its own delta field
            # instead of inline tags.
            thought = getattr(delta, "reasoning_content", None)
            if thought:
                reasoning_parts.append(thought)
            if delta.content:
                content, reasoning = splitter.feed(delta.content)
                text_parts.append(content)
                reasoning_parts.append(reasoning)
            for tc in delta.tool_calls or []:
                # Streamed fragments: the first carries the name, the
                # rest append to `arguments` — accumulate by stream index.
                idx = getattr(tc, "index", 0) or 0
                slot = intents.setdefault(idx, {"name": "", "arguments": ""})
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", None)
                if name:
                    slot["name"] = name
                args_fragment = getattr(fn, "arguments", None)
                if args_fragment:
                    slot["arguments"] += args_fragment
        tail_content, tail_reasoning = splitter.flush()
        text_parts.append(tail_content)
        reasoning_parts.append(tail_reasoning)
        from ..api.llm_trace import redact_endpoint

        request = ReplayRequest(
            model=config.model,
            endpoint=redact_endpoint(config.llm_base_url or self._default_base_url),
            tools=[t.name for t in agent.tools if isinstance(t, FunctionTool)],
        )
        return ReplayResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=[_intent(slot) for _, slot in sorted(intents.items())],
            model=config.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request=request,
        )

    def _history(self, messages: Sequence[Any]) -> list[dict[str, Any]]:
        """Map persisted messages → chat-completions messages, going
        through the SAME `history_items` the live runner uses (faithful
        tool expansion, same empty-content filtering), then converting
        its Responses-API items to the chat-completions shapes the
        probe's `completion` speaks."""
        # Lazy: `api` imports `health` (routes); importing back at module
        # load would be a cycle.
        from ..api.turns import history_items

        out: list[dict[str, Any]] = []
        for item in history_items(messages, max_messages=0):
            kind = item.get("type")
            if kind == "function_call":
                out.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": item["call_id"],
                                "type": "function",
                                "function": {
                                    "name": item["name"],
                                    "arguments": item["arguments"],
                                },
                            }
                        ],
                    }
                )
            elif kind == "function_call_output":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": item["call_id"],
                        "content": item["output"],
                    }
                )
            else:
                out.append({"role": item["role"], "content": item["content"]})
        return out
