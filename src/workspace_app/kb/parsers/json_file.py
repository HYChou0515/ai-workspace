"""JsonParser — bundled IParser for `.json` / `.jsonl` uploads (issue #39 P7).

The parser is deliberately thin: it validates + decodes and emits raw
JSON text Documents; the structure-aware splitting (one node per
top-level array element, leaf lines carrying their ancestor key path)
happens downstream in `DispatchSplitter`'s JSON branch via LlamaIndex's
`JSONNodeParser`. Keeping the split at the splitter layer follows the
chunking-hyperparams principle: parsers produce whole-file Documents,
granularity belongs to the splitter.

`.jsonl` is the exception that proves the rule — each line IS an
independent record by format definition, so the parser emits one
Document per line (a record must never straddle a chunk boundary).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from .protocol import IParser, IParserInput

if TYPE_CHECKING:
    from llama_index.core.schema import Document

_JSON_MIMES = {"application/json"}
_JSON_EXTENSIONS = (".json", ".jsonl")


class JsonParser(IParser):
    """`.json` → one whole-file Document; `.jsonl` → one Document per
    line. Malformed JSON raises ``ValueError`` so the Ingestor flips the
    doc to ``status="error"`` with the message in ``status_detail``."""

    def matches(self, *, filename: str, mime: str, source: IParserInput) -> bool:
        from ..chat_export import is_chat_export

        # `.chat.json` is owned by ChatExportParser (insight extraction);
        # shredding a conversation into generic key-path lines would just
        # pollute retrieval alongside the distilled chunks.
        if is_chat_export(filename):
            return False
        # Extension check matters: libmagic frequently sniffs JSON as
        # text/plain, so mime alone would miss most uploads.
        return mime in _JSON_MIMES or filename.lower().endswith(_JSON_EXTENSIONS)

    @staticmethod
    def _jsonl_records(text: str) -> list[tuple[int, str]]:
        """Non-empty lines as ``(1-based line no, line)`` — the JSONL units
        (#227). Blank lines are skipped but don't shift line numbers, so
        citation labels stay accurate."""
        return [
            (lineno, line) for lineno, line in enumerate(text.splitlines(), start=1) if line.strip()
        ]

    def count_units(self, source: IParserInput, *, filename: str, mime: str) -> int:
        """Fan-out unit count (#227). JSONL → non-empty line count. A
        top-level JSON **array** → its element count (each element is a
        record, like a CSV row). Any other root — or unparseable bytes —
        stays a single unit so the whole-file path (and its error
        surfacing) is unchanged."""
        text = source.as_bytes().decode("utf-8", errors="replace")
        if filename.lower().endswith(".jsonl"):
            return len(self._jsonl_records(text))
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return 1  # malformed → not fanned out; parse() raises the real error
        return len(obj) if isinstance(obj, list) else 1

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

        text = source.as_bytes().decode("utf-8", errors="replace")
        meta = {"filename": filename, "mime": mime}
        if filename.lower().endswith(".jsonl"):
            records = self._jsonl_records(text)
            if unit_range is not None:
                records = records[unit_range[0] : unit_range[1]]
            docs: list[Document] = []
            for lineno, line in records:
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON on line {lineno}: {exc}") from exc
                docs.append(Document(text=line, metadata={**meta, "jsonl_line": lineno}))
            return docs
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        # A fanned-out array process job emits only its element slice (still
        # valid JSON → DispatchSplitter's JSON branch flattens it). The
        # whole-file path keeps the verbatim text (granularity = splitter).
        if unit_range is not None and isinstance(obj, list):
            return [Document(text=json.dumps(obj[unit_range[0] : unit_range[1]]), metadata=meta)]
        return [Document(text=text, metadata=meta)]
