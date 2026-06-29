"""CodeWikiBuilder (issue #281) — build a code collection's wiki by reading its
source, hierarchically: bottom-up summaries, top-down synthesis.

The tension a flat approach can't resolve: feeding a whole repo to one LLM pass
overflows context (it misses subsystems on a big project); summarising one file
at a time has no big-picture view. The fix is to feed each level only the
*summaries* of the level below, never raw code — so every level's context stays
bounded regardless of repo size, while coverage is enforced by iterating the
complete file/directory list (not by the LLM's diligence):

  - **L0 — file cards.** Every SourceDoc → ``/files/<path>.md`` = a deterministic
    tree-sitter ``outline`` (never hallucinates / drops a symbol) + a one-line
    LLM summary. The skeleton is the faithful backbone; the prose rides on top.
  - **L1 — directory pages** (P2): each directory rolled up from its child cards.
  - **L2 — architecture / index / topics** (P3): synthesised from all directory
    summaries.

Each page is a single ``ILlm.collect`` over fixed material (not an agent loop),
so the build is a predictable pipeline and the program — not the model — writes
the files (sidestepping #50's "narrate instead of write_file" failure mode).

Incremental: a file card records its source's content hash; an unchanged file is
skipped on re-sync (no LLM call), so a routine re-pull re-summarises only what
moved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...resources import SourceDoc
from ..doc_id import encode_doc_id
from .code_outline import outline
from .sources import SpecstarWikiSources
from .store import WikiFileStore

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..llm import ILlm

# A file card opens with a hidden marker carrying the source's content hash, so
# an incremental build can skip a file whose bytes haven't changed.
_SRC_MARKER = "<!-- src: {file_id} -->"

_CARD_PROMPT = (
    "You are documenting a source file for a code wiki. In ONE sentence, say what "
    "this file is responsible for — its role in the codebase. No preamble, no list, "
    "just the sentence.\n\n"
    "File: {path}\n\n"
    "Outline (top-level symbols):\n{outline}\n\n"
    "Source:\n{source}\n"
)


class CodeWikiBuilder:
    """Builds (and incrementally refreshes) one code collection's wiki."""

    def __init__(
        self, spec: SpecStar, llm: ILlm, *, wiki_store: WikiFileStore | None = None
    ) -> None:
        self._spec = spec
        self._llm = llm
        self._store = wiki_store or WikiFileStore(spec)
        self._doc_rm = spec.get_resource_manager(SourceDoc)

    async def build(self, collection_id: str) -> None:
        """Bring the collection's code wiki up to date with its SourceDocs."""
        await self._file_cards(collection_id)

    async def _file_cards(self, collection_id: str) -> None:
        sources = SpecstarWikiSources(self._spec, collection_id)
        for path in sources.list():
            ref = sources.ref(path)
            if ref is None:  # pragma: no cover — listed-then-deleted race
                continue
            doc = self._doc_rm.get(encode_doc_id(collection_id, path))
            assert isinstance(doc.data, SourceDoc)
            file_id = doc.data.content.file_id
            assert isinstance(file_id, str)  # a stored SourceDoc's blob always has a content hash
            page = f"/files/{path}.md"
            if await self._is_current(collection_id, page, file_id):
                continue  # bytes unchanged since last build — skip the LLM call
            card = self._render_card(path, ref.text, file_id)
            await self._store.write(collection_id, page, card.encode("utf-8"))

    async def _is_current(self, collection_id: str, page: str, file_id: str) -> bool:
        """True when ``page`` already exists and its source-hash marker matches
        ``file_id`` — i.e. the source's bytes haven't changed since it was built."""
        prev = await self._store.read_with_etag(collection_id, page)
        if prev is None:
            return False
        marker = _SRC_MARKER.replace("{file_id}", file_id)
        return prev[0].decode("utf-8", errors="replace").startswith(marker)

    def _render_card(self, path: str, text: str, file_id: str) -> str:
        skeleton = outline(path, text)
        summary = self._llm.collect(
            _CARD_PROMPT.replace("{path}", path)
            .replace("{outline}", skeleton or "(none)")
            .replace("{source}", text)
        ).strip()
        marker = _SRC_MARKER.replace("{file_id}", file_id)
        body = f"{marker}\n# {path}\n\n{summary}\n"
        if skeleton:
            body += f"\n```\n{skeleton}\n```\n"
        return body
