"""InsightExtractor — the P2 LLM-driven transform that takes a RCA chat
conversation document and produces N structured-insight nodes (root cause,
procedure, lesson learned…). See docs/plan-llamaindex-ingest.md §3.

Tests use a `_FakeLlm` (scripted response) — never a live LLM.
"""

from __future__ import annotations

from collections.abc import Iterator

from llama_index.core.schema import Document, TextNode

from workspace_app.kb.insight_extractor import InsightExtractor
from workspace_app.kb.llm import ILlm


class _FakeLlm(ILlm):
    """ILlm test double — `collect()` returns the canned response verbatim
    regardless of prompt. Mirrors the pattern in test_rerank/test_multiquery."""

    def __init__(self, response: str) -> None:
        self._response = response

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield (self._response, False)


def test_extracts_structured_insights_from_llm_json():
    """The LLM returns JSON listing insights; the extractor parses it and
    emits one TextNode per insight, each carrying kind/title metadata and
    markdown text suitable for ingest as a regular SourceDoc."""
    llm = _FakeLlm(
        '{"insights": ['
        '  {"kind": "root_cause", "title": "Reflow zone-3 drift",'
        '   "markdown": "# Root cause: Reflow zone-3 drift\\n\\nThermocouple miscalibration."},'
        '  {"kind": "procedure", "title": "Recalibrate thermocouple",'
        '   "markdown": "# Procedure: Recalibrate zone-3\\n\\nSteps 1, 2, 3."}'
        "]}"
    )
    extractor = InsightExtractor(llm=llm)
    in_doc = Document(
        text="User: zone 3 looks off.\nAssistant: I think the thermocouple is drifting.",
        metadata={"source_investigation": "inv-abc", "source_title": "MX-7 voids"},
    )
    out = extractor([in_doc])

    assert len(out) == 2
    # Each output is a TextNode whose text IS the insight markdown.
    titles = [n.metadata["title"] for n in out]
    assert titles == ["Reflow zone-3 drift", "Recalibrate thermocouple"]
    assert all(n.metadata["kind"] in {"root_cause", "procedure"} for n in out)
    # Source investigation metadata propagated.
    assert all(n.metadata["source_investigation"] == "inv-abc" for n in out)
    # Insight sequence preserved for stable path encoding later.
    assert [n.metadata["insight_seq"] for n in out] == [0, 1]
    # Markdown body landed in node.text (what gets embedded).
    assert isinstance(out[0], TextNode)
    assert "Thermocouple miscalibration" in out[0].text


def test_malformed_llm_response_yields_no_insights():
    """The LLM may go off-script (no JSON, partial output, hallucination).
    The extractor must return [] rather than crashing the pipeline."""
    for bad in [
        "I'm not sure what you mean.",  # no JSON at all
        "{not valid json",  # bracket but invalid
        '{"insights": "not a list"}',  # wrong shape
        '{"insights": [{"kind": "made_up", "title": "x", "markdown": "y"}]}',  # bad kind
        '{"insights": [{"title": "missing kind", "markdown": "y"}]}',  # missing key
    ]:
        extractor = InsightExtractor(llm=_FakeLlm(bad))
        out = extractor([Document(text="anything", metadata={})])
        assert out == []


def test_strips_json_from_fenced_code_block():
    """Many small models wrap JSON in ```json fences despite the prompt. The
    extractor should tolerate that — pull the first balanced `{...}` block."""
    llm = _FakeLlm(
        "Sure, here's what I found:\n\n"
        "```json\n"
        '{"insights": [{"kind": "lesson_learned",'
        ' "title": "Always check zone temps", '
        '"markdown": "# Lesson\\n\\nCheck SPC."}]}\n'
        "```\n"
        "Let me know if you need more."
    )
    out = InsightExtractor(llm=llm)([Document(text="x", metadata={})])
    assert len(out) == 1
    assert out[0].metadata["kind"] == "lesson_learned"


def test_caps_at_max_insights_to_avoid_kb_flood():
    """If the LLM generates 20 insights (over-eager), only the first
    `max_insights` reach the pipeline — protects the KB from flooding."""
    many = ", ".join(
        f'{{"kind": "procedure", "title": "step {i}", "markdown": "# {i}\\n\\nbody"}}'
        for i in range(20)
    )
    llm = _FakeLlm('{"insights": [' + many + "]}")
    out = InsightExtractor(llm=llm, max_insights=3)([Document(text="x", metadata={})])
    assert len(out) == 3


def test_conversation_to_extraction_doc_serialises_and_truncates():
    """Helper that turns a Conversation's messages into the single Document
    the extractor wants. Tool outputs are truncated so a chatty pipeline
    can't blow the LLM context."""
    from workspace_app.kb.insight_extractor import conversation_to_extraction_doc

    huge = "x" * 9999
    doc = conversation_to_extraction_doc(
        investigation_id="inv-1",
        title="Reflow drift",
        messages=[
            {"role": "user", "content": "lots flagged"},
            {"role": "assistant", "content": "investigating"},
            {"role": "tool", "tool_name": "exec", "content": huge},
            {"role": "user", "content": ""},  # empty → skipped
        ],
        max_chars_per_message=100,
    )
    assert "User: lots flagged" in doc.text
    assert "Assistant: investigating" in doc.text
    assert "[exec]:" in doc.text
    # Truncated to ~100 chars + "[truncated]" marker.
    assert "[truncated]" in doc.text
    assert len(doc.text) < 1000
    assert doc.metadata["source_investigation"] == "inv-1"
    assert doc.metadata["source_title"] == "Reflow drift"


def test_distillation_kinds_cover_terminology_context_and_assumptions():
    """以終為始 (user, 2026-06-06): the point of ingesting a conversation
    is distilling the USEFUL information — which includes domain
    terminology, the user's situational context, and the implicit
    assumptions the discussion leaned on. Those kinds must survive
    extraction (not be dropped as "made_up" kinds)."""
    llm = _FakeLlm(
        '{"insights": ['
        '  {"kind": "terminology", "title": "黑話: cutpoint",'
        '   "markdown": "# cutpoint\\n\\nscan stage 前最後可疑製程的 step_number。"},'
        '  {"kind": "context", "title": "User runs a logic fab on 28nm",'
        '   "markdown": "# Context\\n\\nDefects observed on M6 Cu CMP, lot 25-W14."},'
        '  {"kind": "assumption", "title": "Assumed scan covers all modules",'
        '   "markdown": "# Assumption\\n\\nNever verified the scan stage covers Passiv."}'
        "]}"
    )
    ex = InsightExtractor(llm=llm)
    out = ex(
        [
            Document(
                text="User: foo\nAssistant: bar",
                metadata={"source_investigation": "inv-1", "source_title": "t"},
            )
        ]
    )
    assert [n.metadata["kind"] for n in out] == ["terminology", "context", "assumption"]


def test_prompt_sandwiches_the_transcript_between_instructions():
    """Regression (live qwen3:14b, 2026-06-06): with the conversation at
    the END of the prompt, the model CONTINUED the dialogue (answered
    the assistant's open question) instead of extracting insights —
    classic role-confusion. The template must mark the transcript as
    data and close with the extraction instruction (instruction
    sandwich), so the last thing the model reads is its job, not an
    open question to answer."""
    from workspace_app.kb.insight_extractor import _DEFAULT_PROMPT

    pos = _DEFAULT_PROMPT.find("{conversation}")
    assert pos != -1
    after = _DEFAULT_PROMPT[pos:]
    # The transcript is fenced as data…
    assert "<transcript>" in _DEFAULT_PROMPT and "</transcript>" in after
    # …and a substantive instruction follows it (not a trailing newline).
    tail = after.split("</transcript>", 1)[1]
    assert "Do NOT continue" in tail and "JSON" in tail
