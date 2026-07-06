"""WikiReflector (issue #479) — the periodic *reflection* pass that consolidates a
prose wiki, on top of (not instead of) the per-ingest fold maintainer (#50).

The fold maintainer keeps the wiki fresh one source at a time — fast and local,
but over many ingests the wiki fragments: the same idea explained on several
pages, general concepts buried inside entity pages, pages that outgrow their
topic, links left dangling by earlier edits. Reflection steps back and reads the
*whole* wiki to reorganise it and lift general concepts into their own pages —
the same "compile knowledge into interlinked pages" spirit as the LLM wiki, run
as a housekeeping pass. Its direct precedent is the topic-hub ``→consolidate``.

Same philosophy as the code wiki (#281): the **program** drives control flow and
writes the files, the model only does bounded per-unit synthesis — so a big wiki
never overflows one context and #50's "narrate instead of write" failure can't
happen. Three steps:

  - **survey** (this module, deterministic, 0 LLM): read every knowledge page and
    reduce it to a one-line digest (title + gist + ``Sources:`` + ``[[links]]`` +
    size). Enumerating the page list is what *forces* coverage — nothing is missed
    because the model forgot to look. Meta scaffolding (``/WIKI.md`` / ``/log.md``)
    and the reserved ground-truth / journal folders are skipped.
  - **plan** (P3): one ``collect`` over the digest → a structured reorg plan.
  - **apply** (P4): iterate the plan, one bounded ``collect`` per action, program
    writes — through the guarded store, so ground truth is never clobbered.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .code_wiki import _first_paragraph_after_h1
from .store import MaintainerWikiStore, WikiFileStore, _is_reserved, _norm_path

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..llm import ILlm

# Scaffolding pages that carry conventions / an ingest log, not knowledge — the
# reflection has nothing to consolidate in them, so the survey skips them (the
# reserved ground-truth + /reflections journal folders are skipped via _is_reserved).
_META_PAGES = ("/WIKI.md", "/log.md")

# A `[[wikilink]]` reference, minus any `#anchor` / `|alias`. Used to build the
# page link graph (for the digest + orphan detection).
_LINK_RE = re.compile(r"\[\[([^\[\]|#]+)")

# The page-footer provenance line (wiki_schema.md): `Sources: a.pdf · b.md`.
_SOURCES_RE = re.compile(r"(?im)^\s*Sources:\s*(.+?)\s*$")


def _extract_links(text: str) -> list[str]:
    """The distinct ``[[wikilink]]`` page-stems a page references, in first-seen
    order (anchors/aliases stripped)."""
    out: list[str] = []
    for m in _LINK_RE.finditer(text):
        stem = m.group(1).strip()
        if stem and stem not in out:
            out.append(stem)
    return out


def _extract_sources(text: str) -> list[str]:
    """The source labels on a page's ``Sources:`` provenance line (split on the
    ``·`` middot or a comma), or ``[]`` when the page has none."""
    m = _SOURCES_RE.search(text)
    if not m:
        return []
    return [s.strip() for s in re.split(r"[·,]", m.group(1)) if s.strip()]


def _page_title(text: str) -> str:
    """A page's ``# `` heading text, or ``""`` when it has none."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _survey_skip(path: str) -> bool:
    """Whether the reflection should skip ``path``: the reserved ground-truth /
    journal folders (#377/#397/#479) and the meta scaffolding pages — none of them
    are knowledge to consolidate."""
    return _is_reserved(path) or _norm_path(path) in _META_PAGES


@dataclass(frozen=True)
class PageDigest:
    """One knowledge page reduced to what the planner needs to reason about it
    without reading the whole page — the unit the plan step consumes."""

    path: str
    title: str
    summary: str  # the first prose paragraph under the `# ` heading
    sources: list[str]
    links: list[str]  # `[[wikilink]]` stems this page points at
    size: int  # content length in chars — the split/merge size signal


class WikiReflector:
    """Consolidates one prose collection's wiki: survey → plan → apply."""

    def __init__(
        self, spec: SpecStar, llm: ILlm, *, wiki_store: WikiFileStore | None = None
    ) -> None:
        self._spec = spec
        self._llm = llm
        # The raw store writes the /reflections journal (bypassing the guard); the
        # guarded view is what the page reorg goes through, so a reflection can
        # never clobber the /clarifications / /corrections ground truth (Q7).
        self._raw = wiki_store or WikiFileStore(spec)
        self._store = MaintainerWikiStore(self._raw)

    async def survey(self, collection_id: str) -> list[PageDigest]:
        """Read every knowledge page into a one-line digest (deterministic, no
        LLM). Pages are returned sorted by path so the digest — and thus the whole
        reflection — is reproducible."""
        digests: list[PageDigest] = []
        for path in sorted(await self._raw.ls(collection_id)):
            if _survey_skip(path):
                continue
            text = (await self._raw.read(collection_id, path)).decode("utf-8", errors="replace")
            digests.append(
                PageDigest(
                    path=path,
                    title=_page_title(text) or posixpath.basename(path).removesuffix(".md"),
                    summary=_first_paragraph_after_h1(text),
                    sources=_extract_sources(text),
                    links=_extract_links(text),
                    size=len(text),
                )
            )
        return digests
