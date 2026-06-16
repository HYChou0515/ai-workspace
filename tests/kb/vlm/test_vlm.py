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
