"""ChatExportParser — `.chat.json` conversations distilled into chunks
(issue #39 chat-history support).

The user's architectural call: extracted insights live as CHUNKS under
the uploaded chat doc, so the existing ``DocChunk → SourceDoc`` Ref is
what links distilled knowledge back to the original conversation —
citations open the chat, the chunk count is the visible extraction
outcome, and no parallel doc-management path is needed. (The promote
button still writes separate insight docs: a promoted conversation has
no uploaded SourceDoc to hang chunks on.)

Each insight emits one markdown ``Document`` whose ``filename``
metadata ends in ``.md`` — that routes the DispatchSplitter to the
markdown branch. Leaving the ``.chat.json`` name on them would route
them to the JSON branch, which would ``json.loads`` the markdown and
produce zero nodes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..chat_export import CHAT_EXPORT_SUFFIX, is_chat_export, parse_chat_export
from ..llm import ILlm
from .protocol import IParser, IParserInput

if TYPE_CHECKING:
    from llama_index.core.schema import Document


class ChatExportParser(IParser):
    """Claims ``*.chat.json`` even without an LLM (so JsonParser never
    shreds a chat export into key-path lines); parsing without one
    raises an actionable error instead."""

    def __init__(self, llm: ILlm | None = None) -> None:
        self._llm = llm

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        return is_chat_export(filename)

    def parse(
        self,
        source: IParserInput,
        *,
        filename: str,
        mime: str,
        on_progress: Callable[[str], None] | None = None,
        on_preview: Callable[[bytes, str], None] | None = None,
    ) -> list[Document]:
        from llama_index.core.schema import Document

        from ..insight_extractor import InsightExtractor, conversation_to_extraction_doc

        if self._llm is None:
            raise RuntimeError(
                "a KB LLM is required to extract insights from chat exports — "
                "configure kb.retrieval_llm and reindex this document"
            )
        title, messages = parse_chat_export(source.as_bytes())
        if on_progress is not None:
            on_progress("ChatExportParser: extracting insights via LLM…")
        # "dir/inv-1.chat.json" → "inv-1" — a stable label for metadata.
        stem = filename.rsplit("/", 1)[-1][: -len(CHAT_EXPORT_SUFFIX)]
        conv_doc = conversation_to_extraction_doc(
            investigation_id=stem, title=title, messages=messages
        )
        nodes = InsightExtractor(llm=self._llm)([conv_doc])
        return [
            Document(
                text=n.get_content(),
                metadata={
                    "filename": f"{stem}/insight-{i}.md",
                    "mime": "text/markdown",
                    "kind": n.metadata.get("kind", ""),
                    "title": n.metadata.get("title", ""),
                },
            )
            for i, n in enumerate(nodes)
        ]
