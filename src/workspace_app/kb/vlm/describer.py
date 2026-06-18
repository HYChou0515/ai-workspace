"""VlmDescriber — the one component every vision-backed parser shares
(issue #39: standalone images, PDF visual pages, slides).

Owns the layered SOTA prompt (verbatim OCR → interpretation → tables;
see ``kb/prompts/vlm_describe.md``) and turns one image into one
Markdown text block.

Two-stage (issue #115): small VLMs read images well but often ignore
the Markdown-format instruction and emit free text, which the chunker
then splits as raw token windows (truncating tables / severing
sections). So when a text ``ILlm`` formatter is wired, stage 2 re-emits
the VLM's content as clean Markdown — a *pure* reformat (it must keep
every word), with a length fuse that reverts to the raw VLM text if the
formatter dropped content. No formatter wired → return the raw text
(still routed through the Markdown splitter downstream).
"""

from __future__ import annotations

from pathlib import Path

from ..llm import ILlm
from .protocol import IVlm, OnChunk

_PROMPTS = Path(__file__).parent.parent / "prompts"
_PROMPT_TEMPLATE = (_PROMPTS / "vlm_describe.md").read_text(encoding="utf-8")
_FORMAT_PROMPT = (_PROMPTS / "vlm_format.md").read_text(encoding="utf-8")

# Discard the formatter's output when it is shorter than this fraction of the
# raw VLM text — a sign it summarized instead of reformatting (issue #115).
_MIN_KEEP_RATIO = 0.5


class VlmDescriber:
    def __init__(
        self,
        vlm: IVlm,
        *,
        formatter: ILlm | None = None,
        prompt_template: str | None = None,
        format_prompt: str | None = None,
    ) -> None:
        self._vlm = vlm
        self._formatter = formatter
        self._prompt_template = prompt_template or _PROMPT_TEMPLATE
        self._format_prompt = format_prompt or _FORMAT_PROMPT

    def describe(
        self,
        image: bytes,
        mime: str,
        *,
        context: str = "",
        on_chunk: OnChunk | None = None,
    ) -> str:
        """One image → one Markdown description. ``context`` anchors the
        VLM (e.g. ``"page 3 of slides.pdf"``) and is folded into the
        prompt; ``on_chunk`` surfaces the live stream of both stages."""
        context_line = f"Context: this image is {context}.\n\n" if context else ""
        prompt = self._prompt_template.format(context_line=context_line)
        raw = self._vlm.collect(prompt, images=[(image, mime)], on_chunk=on_chunk).strip()
        if self._formatter is None or not raw:
            return raw
        return self._format(raw, on_chunk=on_chunk)

    def _format(self, raw: str, *, on_chunk: OnChunk | None) -> str:
        """Stage 2: re-emit ``raw`` as clean Markdown via the text LLM. The
        raw text is appended after the instruction (not interpolated) so its
        own braces / placeholders can't break the prompt. A formatter that
        drops content (output < half the raw length) is discarded."""
        assert self._formatter is not None
        formatted = self._formatter.collect(f"{self._format_prompt}\n\n{raw}", on_chunk).strip()
        if len(formatted) < len(raw) * _MIN_KEEP_RATIO:
            return raw
        return formatted
