"""ChatExportParser — `.chat.json` insights as chunks of the chat doc.

User's architectural call (2026-06-06): extracted insights are stored
as CHUNKS under the uploaded `.chat.json` SourceDoc — the existing
DocChunk→SourceDoc Ref is what links distilled knowledge back to the
original conversation (citations open the chat; the chunk count IS
the extraction outcome). No separate insight SourceDocs on this path
(promote keeps its own — there's no uploaded doc to hang chunks on).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from workspace_app.kb.chat_export import build_chat_export
from workspace_app.kb.llm import ILlm
from workspace_app.kb.parsers import MaterialisedParserInput
from workspace_app.kb.parsers.chat_export_parser import ChatExportParser


class _FakeLlm(ILlm):
    def __init__(self, response: str) -> None:
        self._response = response

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._response, False)


_TWO_INSIGHTS = (
    '{"insights": ['
    '  {"kind": "terminology", "title": "cutpoint",'
    '   "markdown": "# cutpoint\\n\\nscan stage 前最後可疑製程的 step_number。"},'
    '  {"kind": "assumption", "title": "Scan covers all modules",'
    '   "markdown": "# Assumption\\n\\nNever verified; confirm with the scan recipe."}'
    "]}"
)


def _export() -> bytes:
    return build_chat_export(
        title="MX-7 voids",
        messages=[
            {"role": "user", "content": "AOI flagged voids", "tool_name": ""},
            {"role": "assistant", "content": "Checking zone temps.", "tool_name": ""},
        ],
    )


def _input(data: bytes, filename: str = "inv-1.chat.json") -> MaterialisedParserInput:
    return MaterialisedParserInput(data, filename=filename)


def test_matches_only_chat_exports():
    p = ChatExportParser(_FakeLlm(_TWO_INSIGHTS))
    assert p.matches(filename="inv-1.chat.json", mime="application/json", source=_input(b"{}"))
    assert not p.matches(filename="plain.json", mime="application/json", source=_input(b"{}"))
    assert not p.matches(filename="t.csv", mime="text/csv", source=_input(b"a,b"))


def test_parse_emits_one_markdown_document_per_insight():
    """Each insight becomes a markdown Document routed to the markdown
    splitter (`.md` filename metadata — NOT the json branch, which would
    json.loads the markdown and produce zero nodes). kind/title ride
    along as metadata."""
    p = ChatExportParser(_FakeLlm(_TWO_INSIGHTS))
    progress: list[str] = []
    docs = list(
        p.parse(
            _input(_export()),
            filename="inv-1.chat.json",
            mime="application/json",
            on_progress=progress.append,
        )
    )
    assert len(docs) == 2
    assert "cutpoint" in docs[0].text and "step_number" in docs[0].text
    assert docs[0].metadata["filename"] == "inv-1/insight-0.md"
    assert docs[0].metadata["mime"] == "text/markdown"
    assert docs[0].metadata["kind"] == "terminology"
    assert docs[1].metadata["kind"] == "assumption"
    # The LLM round is the slow part — progress surfaces it.
    assert any("insight" in m.lower() for m in progress)


def test_parse_without_llm_raises_actionably():
    """No KB LLM wired → error (→ status=error + status_detail), not a
    silent ready-with-nothing."""
    p = ChatExportParser(None)
    # It still CLAIMS the file (so JsonParser doesn't shred it) …
    assert p.matches(filename="i.chat.json", mime="application/json", source=_input(b"{}"))
    # … but parsing tells the operator what to fix.
    with pytest.raises(RuntimeError, match="LLM"):
        p.parse(_input(_export(), "i.chat.json"), filename="i.chat.json", mime="application/json")


def test_parse_rejects_malformed_exports():
    p = ChatExportParser(_FakeLlm(_TWO_INSIGHTS))
    with pytest.raises(ValueError, match="messages"):
        p.parse(
            _input(b'{"title": "t"}', "i.chat.json"),
            filename="i.chat.json",
            mime="application/json",
        )


def test_inconclusive_chat_yields_zero_documents():
    """LLM finds nothing → zero Documents → the doc lands ready with 0
    chunks; the chunk count itself is the visible outcome."""
    p = ChatExportParser(_FakeLlm('{"insights": []}'))
    docs = list(p.parse(_input(_export()), filename="inv-1.chat.json", mime="application/json"))
    assert docs == []
