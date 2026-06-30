"""IVlm + VlmDescriber — issue #39 P9/P10 core.

IVlm mirrors ILlm's streaming-only discipline (feedback: every LLM
call must stream — `collect` drains `stream`, never a separate
non-streaming call) but carries images alongside the prompt.

VlmDescriber owns the layered SOTA prompt (verbatim OCR + structural
description + tables-as-markdown) and is the single component every
vision-backed parser shares (standalone images, PDF visual pages,
slides).
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Iterator, Sequence

import pytest

from workspace_app.kb.llm import ILlm
from workspace_app.kb.vlm import IVlm, VlmDescriber


class FakeVlm(IVlm):
    """Scripted IVlm — records calls, replays canned chunks."""

    def __init__(self, chunks: list[tuple[str, bool]] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._chunks = chunks or [("a diagram of ", False), ("an etch chamber", False)]

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.calls.append({"prompt": prompt, "images": list(images)})
        yield from self._chunks


class FakeLlm(ILlm):
    """Scripted text ILlm — records prompts, replays canned chunks."""

    def __init__(self, chunks: list[tuple[str, bool]]) -> None:
        self.calls: list[str] = []
        self._chunks = chunks

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.calls.append(prompt)
        yield from self._chunks


def test_ivlm_is_abc_and_requires_stream():
    assert ABC in IVlm.__mro__

    class Missing(IVlm):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Missing()  # type: ignore[abstract]


def test_collect_joins_non_reasoning_and_forwards_all_chunks():
    """collect = drain stream: reasoning chunks reach on_chunk (live
    thinking) but are excluded from the returned text."""
    vlm = FakeVlm([("thinking…", True), ("OCR: hello", False)])
    seen: list[tuple[str, bool]] = []
    out = vlm.collect(
        "p", images=[(b"png", "image/png")], on_chunk=lambda t, r: seen.append((t, r))
    )
    assert out == "OCR: hello"
    assert seen == [("thinking…", True), ("OCR: hello", False)]


def test_describer_sends_layered_prompt_with_the_image():
    """The describer's prompt covers the three SOTA layers — verbatim
    transcription, structural description, tables as markdown — and the
    image rides along to the VLM."""
    vlm = FakeVlm()
    d = VlmDescriber(vlm)
    text = d.describe(b"\x89PNG...", "image/png")
    assert text == "a diagram of an etch chamber"
    (call,) = vlm.calls
    prompt = str(call["prompt"]).lower()
    assert "verbatim" in prompt
    assert "table" in prompt and "markdown" in prompt
    assert call["images"] == [(b"\x89PNG...", "image/png")]


def test_describer_context_line_lands_in_the_prompt():
    """Callers add context (e.g. 'page 3 of slides.pdf') so the VLM can
    anchor its description; it must appear in the prompt."""
    vlm = FakeVlm()
    VlmDescriber(vlm).describe(b"img", "image/jpeg", context="page 3 of slides.pdf")
    assert "page 3 of slides.pdf" in str(vlm.calls[0]["prompt"])


def test_describer_appends_collection_guidance_after_the_base_prompt():
    """#328: a collection's `parser_guidance` is appended AFTER the base
    describe template, so the operator can steer extraction per collection
    (e.g. "a fishbone diagram → emit JSON") without replacing the SOTA layers."""
    vlm = FakeVlm()
    VlmDescriber(vlm).describe(
        b"img", "image/png", guidance="If you see a fishbone diagram, emit JSON."
    )
    prompt = str(vlm.calls[0]["prompt"])
    assert "If you see a fishbone diagram, emit JSON." in prompt
    # the base layered template is still there — guidance augments, never replaces.
    assert "verbatim" in prompt.lower()
    # appended at the end (after the base prompt body).
    assert prompt.rstrip().endswith("If you see a fishbone diagram, emit JSON.")


def test_describer_empty_guidance_leaves_the_prompt_unchanged():
    """Blank guidance (the default / a collection that set none) ⇒ the prompt is
    byte-for-byte the base template — no trailing separator, no behaviour change."""
    v1 = FakeVlm()
    VlmDescriber(v1).describe(b"img", "image/png")
    plain = str(v1.calls[0]["prompt"])
    v2 = FakeVlm()
    VlmDescriber(v2).describe(b"img", "image/png", guidance="")
    assert str(v2.calls[0]["prompt"]) == plain


def test_describer_runs_formatter_stage_when_wired():
    """Issue #115: a small VLM often emits free text, not Markdown. With a
    text-LLM formatter wired, describe() pipes the VLM's text through it and
    returns the formatter's clean Markdown — and the raw VLM text is what the
    formatter is asked to restructure."""
    vlm = FakeVlm([("Visual: a bar chart of weekly yield. Text: W1 92%, W2 95%.", False)])
    formatter = FakeLlm(
        [
            (
                "## Visual description\n\nA bar chart.\n\n## Tables\n\n| W1 | 92% |\n| W2 | 95% |",
                False,
            )
        ]
    )
    out = VlmDescriber(vlm, formatter=formatter).describe(b"png", "image/png")
    assert out.startswith("## Visual description")
    assert "| W1 | 92% |" in out
    # The formatter restructured the VLM's raw text (it appeared in its prompt).
    assert "W1 92%" in formatter.calls[0]


def test_describer_without_formatter_returns_raw_vlm_text():
    """No formatter wired (kb.vlm_format_llm unset) → describe() returns the
    raw VLM output unchanged. It still gets routed through the Markdown path
    downstream, so a concise slide becomes one clean chunk — never worse than
    today, no extra LLM call."""
    vlm = FakeVlm([("## already markdown\n\nbody", False)])
    out = VlmDescriber(vlm, formatter=None).describe(b"png", "image/png")
    assert out == "## already markdown\n\nbody"


def test_describer_keeps_raw_when_formatter_drops_content():
    """Safety fuse: the formatter is a *pure* reformatter. If it returns far
    less text than the VLM produced (it summarized/dropped content), discard it
    and keep the raw VLM text — we never trade structure for truncation, which
    is the very bug #115 is about."""
    raw = "Verbatim transcription: " + (
        "wafer lot A23 etch step CD 42nm overlay 3nm defect cluster NW quadrant. " * 8
    )
    vlm = FakeVlm([(raw, False)])
    formatter = FakeLlm([("## Summary\n\nA wafer report.", False)])  # far too short
    out = VlmDescriber(vlm, formatter=formatter).describe(b"png", "image/png")
    assert out == raw.strip()


def test_describer_answer_uses_question_as_prompt_and_skips_formatter():
    """`answer` (the interactive read_image path, #112) sends the caller's
    question straight to the VLM as the prompt — not the layered describe
    template — and never runs the formatter (it's an answer, not OCR to
    restructure). The VLM's non-reasoning content is returned."""
    vlm = FakeVlm([("the error is ", False), ("OOMKilled", False)])
    formatter = FakeLlm([("## should not be used", False)])
    out = VlmDescriber(vlm, formatter=formatter).answer(
        b"png", "image/png", question="what error is in this screenshot?"
    )
    assert out == "the error is OOMKilled"
    (call,) = vlm.calls
    assert call["prompt"] == "what error is in this screenshot?"
    assert call["images"] == [(b"png", "image/png")]
    assert formatter.calls == []


def test_describer_answer_forwards_chunks_to_on_chunk():
    """`answer` streams live like every other VLM call — reasoning + content
    chunks reach on_chunk so the tool card shows progress."""
    vlm = FakeVlm([("thinking…", True), ("answer", False)])
    seen: list[tuple[str, bool]] = []
    VlmDescriber(vlm).answer(
        b"png", "image/png", question="q", on_chunk=lambda t, r: seen.append((t, r))
    )
    assert seen == [("thinking…", True), ("answer", False)]


def test_describer_skips_formatter_when_vlm_returns_nothing():
    """An empty VLM result (a featureless image the model couldn't read) is
    returned as-is — we never hand the formatter empty input to hallucinate
    from, even when one is wired."""
    vlm = FakeVlm([("", False)])
    formatter = FakeLlm([("## Hallucinated heading\n\nmade-up body", False)])
    out = VlmDescriber(vlm, formatter=formatter).describe(b"png", "image/png")
    assert out == ""
    assert formatter.calls == []
