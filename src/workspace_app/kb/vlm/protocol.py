"""IVlm — a **streaming** vision-language completion (issue #39).

Mirrors ``kb.llm.ILlm``'s discipline — streaming is the only
primitive; ``collect`` drains ``stream`` so callers still surface the
model's live thinking — but the call carries images alongside the
prompt. Kept as a separate interface (not an ILlm extension) so the
many text-only ILlm callers/fakes don't grow an images param they'd
never use.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Iterator, Sequence

# Live-progress sink for streamed chunks: (text, is_reasoning).
OnChunk = Callable[[str, bool], None]


class IVlm(abc.ABC):
    @abc.abstractmethod
    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        """Yield ``(text_chunk, is_reasoning)`` as the model produces
        them. ``images`` is a sequence of ``(raw_bytes, mime)`` sent
        with the prompt. Always streaming; the only primitive."""
        ...

    def collect(
        self,
        prompt: str,
        *,
        images: Sequence[tuple[bytes, str]],
        on_chunk: OnChunk | None = None,
    ) -> str:
        """Drain ``stream()``: forward every chunk to ``on_chunk`` (live
        thinking) and return the joined **non-reasoning** content."""
        out: list[str] = []
        for text, reasoning in self.stream(prompt, images=images):
            if on_chunk is not None:
                on_chunk(text, reasoning)
            if not reasoning:
                out.append(text)
        return "".join(out)
