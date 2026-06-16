"""VlmDescriber — the one component every vision-backed parser shares
(issue #39: standalone images, PDF visual pages, slides).

Owns the layered SOTA prompt (verbatim OCR → structural description →
tables-as-markdown; see ``kb/prompts/vlm_describe.md``) and turns one
image into one Markdown text block via ``IVlm.collect``.
"""

from __future__ import annotations

from pathlib import Path

from .protocol import IVlm, OnChunk

_PROMPT_TEMPLATE = (Path(__file__).parent.parent / "prompts" / "vlm_describe.md").read_text(
    encoding="utf-8"
)


class VlmDescriber:
    def __init__(self, vlm: IVlm, *, prompt_template: str | None = None) -> None:
        self._vlm = vlm
        self._prompt_template = prompt_template or _PROMPT_TEMPLATE

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
        prompt; ``on_chunk`` surfaces the live stream."""
        context_line = f"Context: this image is {context}.\n\n" if context else ""
        prompt = self._prompt_template.format(context_line=context_line)
        return self._vlm.collect(prompt, images=[(image, mime)], on_chunk=on_chunk).strip()
