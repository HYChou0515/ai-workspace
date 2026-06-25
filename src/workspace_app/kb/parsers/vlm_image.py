"""VlmImageParser — standalone raster images via VLM (issue #39 P10).

One image → one VLM call (layered prompt: verbatim OCR + structure +
tables-as-markdown, owned by ``VlmDescriber``) → one Document whose
text is the returned Markdown. The original image bytes stay on the
SourceDoc for rendering; only the description is embedded.

No VLM wired → ``matches`` returns False, so image uploads store with
zero chunks (Q9b) instead of erroring; an operator who later
configures ``kb.vlm_llm`` just reindexes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..vlm import VlmDescriber
from .protocol import IParser, IParserInput

if TYPE_CHECKING:
    from llama_index.core.schema import Document

# Raster types qwen2.5-vl & friends accept. gif (animated) and svg
# (text format — better served by a future dedicated parser) are out.
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


class VlmImageParser(IParser):
    def __init__(self, describer: VlmDescriber | None) -> None:
        self._describer = describer

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        if self._describer is None:
            return False
        return mime in _IMAGE_MIMES or filename.lower().endswith(_IMAGE_EXTENSIONS)

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
        unit_range: tuple[int, int] | None = None,
    ) -> list[Document]:
        from llama_index.core.schema import Document

        assert self._describer is not None  # matches() gates on it
        if on_progress is not None:
            on_progress(f"VlmImageParser: describing {filename}")
        text = self._describer.describe(
            source.as_bytes(),
            mime if mime in _IMAGE_MIMES else "image/png",
            context=f"the uploaded image {filename}",
        )
        # content_format flags the text as Markdown so DispatchSplitter routes
        # it through the heading-aware Markdown path (issue #115) — the source
        # mime stays the original raster type for honest provenance.
        return [
            Document(
                text=text,
                metadata={"filename": filename, "mime": mime, "content_format": "markdown"},
            )
        ]
