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

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ...resources import Collection, SourceDoc
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

_DIR_PROMPT = (
    "You are documenting a directory of a codebase for a code wiki. Using ONLY the "
    "one-line summaries of its files and sub-packages below, write a short paragraph "
    "(2-4 sentences) explaining what this directory is for and how its pieces fit "
    "together. No preamble, no bullet list — just the paragraph.\n\n"
    "Directory: {dir}\n\n"
    "{material}\n"
)

_ARCH_PROMPT = (
    "You are writing the architecture overview for a code wiki, given the one-line "
    "summary of each top-level package below. Explain the system's shape: its main "
    "layers/components, how they relate, and the overall flow. A few short paragraphs, "
    "markdown, no preamble.\n\n"
    "Top-level packages:\n{material}\n"
)

_TOPICS_PROMPT = (
    "Given the top-level packages of a codebase below, propose a short list of the "
    "most useful cross-cutting topics a reader would want explained (e.g. a subsystem, "
    "a key flow, a concern that spans packages). Reply with ONE topic title per line, "
    "at most six, no numbering, no other text. If nothing stands out, reply with "
    "nothing.\n\n"
    "Top-level packages:\n{material}\n"
)

_TOPIC_PAGE_PROMPT = (
    "Write the wiki page for the topic below, using the codebase's package summaries "
    "as context. A few short paragraphs of markdown explaining the topic and where it "
    "lives in the code. No preamble.\n\n"
    "Topic: {title}\n\n"
    "Package summaries:\n{material}\n"
)


@dataclass(frozen=True)
class _Tree:
    """The directory structure derived from the source paths. ``child_files`` /
    ``child_dirs`` map a directory (``""`` = repo root) to its DIRECT children;
    ``all_dirs`` is every non-root directory."""

    child_files: dict[str, list[str]] = field(default_factory=dict)
    child_dirs: dict[str, set[str]] = field(default_factory=dict)
    all_dirs: set[str] = field(default_factory=set)


def _build_tree(paths: list[str]) -> _Tree:
    child_files: dict[str, list[str]] = defaultdict(list)
    child_dirs: dict[str, set[str]] = defaultdict(set)
    all_dirs: set[str] = set()
    for p in paths:
        d = posixpath.dirname(p)
        child_files[d].append(p)
        while d:  # register every ancestor directory + the parent→child edge
            parent = posixpath.dirname(d)
            child_dirs[parent].add(d)
            all_dirs.add(d)
            d = parent
    return _Tree(child_files, child_dirs, all_dirs)


class CodeWikiBuilder:
    """Builds (and incrementally refreshes) one code collection's wiki."""

    def __init__(
        self, spec: SpecStar, llm: ILlm, *, wiki_store: WikiFileStore | None = None
    ) -> None:
        self._spec = spec
        self._llm = llm
        self._store = wiki_store or WikiFileStore(spec)
        self._doc_rm = spec.get_resource_manager(SourceDoc)
        self._coll_rm = spec.get_resource_manager(Collection)

    async def build(self, collection_id: str) -> None:
        """Bring the collection's code wiki up to date with its SourceDocs."""
        sources = SpecstarWikiSources(self._spec, collection_id)
        paths = sources.list()
        changed = await self._file_cards(collection_id, sources, paths)
        # The directory roll-up + architecture read ALL the lower-level pages, so
        # they're only worth redoing when at least one card moved; a re-pull that
        # changed nothing is a no-op.
        if changed:
            tree = _build_tree(paths)
            await self._dir_pages(collection_id, tree)
            await self._arch_pages(collection_id, tree)

    # ── L0: per-file cards ───────────────────────────────────────────
    async def _file_cards(
        self, collection_id: str, sources: SpecstarWikiSources, paths: list[str]
    ) -> bool:
        """Write a card for every changed source; return whether any was (re)built."""
        changed = False
        for path in paths:
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
            changed = True
        return changed

    # ── L1: directory roll-up pages ──────────────────────────────────
    async def _dir_pages(self, collection_id: str, tree: _Tree) -> None:
        """Walk the directory tree bottom-up: each directory gets a page rolled
        up from its direct child file cards + child sub-directory pages. Deepest
        first, so a parent reads summaries its children just wrote."""
        for d in sorted(tree.all_dirs, key=lambda x: x.count("/"), reverse=True):
            page = await self._render_dir(
                collection_id,
                d,
                sorted(tree.child_files.get(d, [])),
                sorted(tree.child_dirs.get(d, [])),
            )
            await self._store.write(collection_id, f"/dirs/{d}.md", page.encode("utf-8"))

    async def _render_dir(
        self, collection_id: str, directory: str, files: list[str], subdirs: list[str]
    ) -> str:
        file_lines = [
            f"- [{posixpath.basename(p)}](/files/{p}.md) — "
            f"{await self._summary_of(collection_id, f'/files/{p}.md')}"
            for p in files
        ]
        sub_lines = [
            f"- [{posixpath.basename(sd)}/](/dirs/{sd}.md) — "
            f"{await self._summary_of(collection_id, f'/dirs/{sd}.md')}"
            for sd in subdirs
        ]
        material = "Files:\n" + ("\n".join(file_lines) or "(none)")
        if sub_lines:
            material += "\n\nSub-packages:\n" + "\n".join(sub_lines)
        synthesis = _unfence(
            self._llm.collect(
                _DIR_PROMPT.replace("{dir}", directory).replace("{material}", material)
            )
        )
        body = f"# {directory}\n\n{synthesis}\n"
        if file_lines:
            body += "\n## Files\n" + "\n".join(file_lines) + "\n"
        if sub_lines:
            body += "\n## Sub-packages\n" + "\n".join(sub_lines) + "\n"
        return body

    async def _summary_of(self, collection_id: str, page: str) -> str:
        """The one-line gist of an already-written card / directory page (the
        paragraph just under its ``# `` heading)."""
        prev = await self._store.read_with_etag(collection_id, page)
        if prev is None:  # pragma: no cover — every child page is written first
            return ""
        return _first_paragraph_after_h1(prev[0].decode("utf-8", errors="replace"))

    async def _is_current(self, collection_id: str, page: str, file_id: str) -> bool:
        """True when ``page`` already exists and its source-hash marker matches
        ``file_id`` — i.e. the source's bytes haven't changed since it was built."""
        prev = await self._store.read_with_etag(collection_id, page)
        if prev is None:
            return False
        marker = _SRC_MARKER.replace("{file_id}", file_id)
        return prev[0].decode("utf-8", errors="replace").startswith(marker)

    # ── L2: architecture / index / topics ────────────────────────────
    async def _arch_pages(self, collection_id: str, tree: _Tree) -> None:
        """Synthesise the top-down pages from the top-level summaries. Each
        top-level directory page already recursively rolls up its whole subtree,
        so the top-level summaries are a bounded, full-repo picture — no matter
        how big the repo is."""
        top_dirs = sorted(tree.child_dirs.get("", set()))
        top_files = sorted(tree.child_files.get("", []))
        dir_summaries = [
            (d, await self._summary_of(collection_id, f"/dirs/{d}.md")) for d in top_dirs
        ]
        material = "\n".join(f"- {d}/: {s}" for d, s in dir_summaries)
        for p in top_files:
            sep = "\n" if material else ""
            material += f"{sep}- {p}: " + await self._summary_of(collection_id, f"/files/{p}.md")
        material = material or "(empty repository)"

        arch = _unfence(self._llm.collect(_ARCH_PROMPT.replace("{material}", material)))
        await self._store.write(
            collection_id, "/architecture.md", f"# Architecture\n\n{arch}\n".encode()
        )

        topics = self._plan_topics(material)
        for title in topics:
            body = _unfence(
                self._llm.collect(
                    _TOPIC_PAGE_PROMPT.replace("{title}", title).replace("{material}", material)
                )
            )
            await self._store.write(
                collection_id, f"/topics/{_slugify(title)}.md", f"# {title}\n\n{body}\n".encode()
            )

        coll = self._coll_rm.get(collection_id).data
        assert isinstance(coll, Collection)
        index = _render_index(coll.name, dir_summaries, top_files, topics)
        await self._store.write(collection_id, "/index.md", index.encode("utf-8"))

    def _plan_topics(self, material: str) -> list[str]:
        """Ask the model for a few cross-cutting topic titles; tolerant of
        bullets / numbering / blank lines, de-duplicated by slug, capped."""
        raw = self._llm.collect(_TOPICS_PROMPT.replace("{material}", material))
        titles: list[str] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            title = re.sub(r"^\s*(?:[-*]|\d+[.)])?\s*", "", line).strip()
            slug = _slugify(title)
            if slug and slug not in seen:
                seen.add(slug)
                titles.append(title)
        return titles[:_MAX_TOPICS]

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


# At most this many cross-cutting topic pages — keep the wiki focused, bound cost.
_MAX_TOPICS = 6


def _unfence(text: str) -> str:
    """Strip a single wrapping ```` ```lang … ``` ```` fence some models put
    around their whole answer — otherwise a page's prose renders as one big code
    block in the markdown view. Leaves fences that are part of the content alone."""
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text.strip()


def _slugify(title: str) -> str:
    """A filename-safe slug for a topic title (``"Data Flow"`` → ``"data-flow"``)."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _render_index(
    name: str,
    dir_summaries: list[tuple[str, str]],
    top_files: list[str],
    topics: list[str],
) -> str:
    """Assemble the wiki home page deterministically: title + links to the
    architecture overview, each top-level package, top-level files, and topics."""
    lines = [f"# {name}", "", "[Architecture overview](/architecture.md)", ""]
    if dir_summaries:
        lines.append("## Packages")
        lines += [f"- [{d}/](/dirs/{d}.md) — {summary}" for d, summary in dir_summaries]
        lines.append("")
    if top_files:
        lines.append("## Files")
        lines += [f"- [{p}](/files/{p}.md)" for p in top_files]
        lines.append("")
    if topics:
        lines.append("## Topics")
        lines += [f"- [{t}](/topics/{_slugify(t)}.md)" for t in topics]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _first_paragraph_after_h1(text: str) -> str:
    """The first prose paragraph beneath a page's ``# `` heading — the file
    card's one-liner or a directory page's roll-up — stopping at the first code
    fence / ``## `` section / blank line after content."""
    out: list[str] = []
    seen_h1 = False
    for line in text.splitlines():
        if line.startswith("# "):
            seen_h1 = True
            continue
        if not seen_h1:
            continue
        if line.startswith("```") or line.startswith("## "):
            break
        if line.strip():
            out.append(line.strip())
        elif out:  # blank line after we've collected content ends the paragraph
            break
    return " ".join(out)
