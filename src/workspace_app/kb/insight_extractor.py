"""InsightExtractor — P2 LlamaIndex `TransformComponent` that takes one
"conversation document" (a serialised RCA chat) and emits N markdown-shaped
insight nodes (root cause, procedure, lesson learned, false hypothesis).

The extractor's `__call__` runs once per input node:
  1. Format the conversation text into the extraction prompt
  2. Stream the LLM response (via our `ILlm.collect`) and parse it as JSON
  3. For each insight, create a `TextNode` carrying the markdown body +
     metadata (kind, title, source_investigation, insight_seq)

Downstream `DispatchSplitter` runs on these markdown nodes (most insights
are short enough to stay as one chunk; long ones split via the markdown
parser); `EmbedderAdapter` embeds them. The Ingestor then writes each as a
SourceDoc + DocChunk into the "Investigations Knowledge" collection.

The prompt is loaded from `prompts/insight_extraction.md` so it's version-
controlled and human-editable, not buried in code.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from llama_index.core.schema import BaseNode, Document, TextNode, TransformComponent

from .llm import ILlm

logger = logging.getLogger(__name__)

_VALID_KINDS = {"root_cause", "procedure", "lesson_learned", "false_hypothesis"}

_DEFAULT_PROMPT = (Path(__file__).parent / "prompts" / "insight_extraction.md").read_text(
    encoding="utf-8"
)


class InsightExtractor(TransformComponent):
    """Extract structured insights from a conversation document via LLM.
    Each input node → 0..N output nodes, one per insight."""

    # `Any` because TransformComponent is a pydantic model and Protocol-typed
    # fields trigger runtime isinstance checks (see EmbedderAdapter).
    llm: Any
    max_insights: int
    prompt_template: str

    def __init__(
        self,
        *,
        llm: ILlm,
        max_insights: int = 5,
        prompt_template: str | None = None,
    ) -> None:
        super().__init__(
            llm=llm,
            max_insights=max_insights,
            prompt_template=prompt_template or _DEFAULT_PROMPT,
        )

    def __call__(self, nodes: Sequence[BaseNode], **_kw: Any) -> list[BaseNode]:  # type: ignore[override]
        out: list[BaseNode] = []
        for node in nodes:
            # `str.replace` (not `.format`) so JSON-schema examples in the
            # prompt template don't collide with positional braces.
            prompt = self.prompt_template.replace("{conversation}", node.get_content())
            raw = self.llm.collect(prompt)
            insights = _parse_insights(raw, max_n=self.max_insights)
            for seq, insight in enumerate(insights):
                out.append(
                    TextNode(
                        text=insight["markdown"],
                        metadata={
                            **node.metadata,
                            "kind": insight["kind"],
                            "title": insight["title"],
                            "insight_seq": seq,
                        },
                    )
                )
        return out


def _parse_insights(raw: str, *, max_n: int) -> list[dict[str, str]]:
    """Parse the LLM's JSON response. Tolerant of leading prose / fenced
    code blocks — extract the first `{...}` block, then validate each
    insight has `kind` (one of `_VALID_KINDS`), `title`, `markdown`. Returns
    at most `max_n`; drops malformed entries silently with a log.
    Returns `[]` for any unrecoverable parse error — never raises."""
    try:
        obj = json.loads(_extract_json_object(raw))
        items = obj.get("insights", [])
        if not isinstance(items, list):
            return []
    except (json.JSONDecodeError, ValueError, AttributeError):
        logger.warning("InsightExtractor: LLM response was not parseable JSON")
        return []
    valid: list[dict[str, str]] = []
    for item in items:
        if (
            isinstance(item, dict)
            and item.get("kind") in _VALID_KINDS
            and isinstance(item.get("title"), str)
            and isinstance(item.get("markdown"), str)
        ):
            valid.append(item)
    return valid[:max_n]


def _extract_json_object(raw: str) -> str:
    """Return the substring from the first `{` to its matching `}`. Tolerates
    LLM responses that wrap JSON in ```json fences or add a preamble."""
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    depth = 0
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    raise ValueError("unterminated JSON object")


def conversation_to_extraction_doc(
    *,
    investigation_id: str,
    title: str,
    messages: list[dict[str, str]],
    max_chars_per_message: int = 2000,
) -> Document:
    """Serialise a Conversation into a `Document` ready for the
    extractor's prompt. Each message becomes a `Role: content` line; tool
    outputs are truncated so a chatty pipeline doesn't blow up context."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > max_chars_per_message:
            content = content[:max_chars_per_message] + "\n…[truncated]"
        if role == "tool":
            name = m.get("tool_name") or "tool"
            parts.append(f"[{name}]: {content}")
        else:
            parts.append(f"{role.capitalize()}: {content}")
    return Document(
        text="\n\n".join(parts),
        metadata={"source_investigation": investigation_id, "source_title": title},
    )
