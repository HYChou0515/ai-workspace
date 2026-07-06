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

import msgspec

from .code_wiki import _first_paragraph_after_h1, _unfence
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


# ── plan (one collect over the digest → a structured reorg plan) ─────────

# Only a page at/above this many chars is eligible to be split (Q7c). The guard
# is the anti-thrash lever: a just-split page falls below it and so can't be
# re-split next day; merges are redundancy- (not size-) driven, so they never
# recombine the distinct subtopics a split produced. A coordinator knob later.
_DEFAULT_SPLIT_MIN_CHARS = 4000


class ConceptAction(msgspec.Struct):
    """Lift/refresh a general concept into ``/concepts/<slug>.md``, synthesised
    from the ``sources`` pages it's currently scattered across."""

    title: str = ""
    sources: list[str] = msgspec.field(default_factory=list)


class MergeAction(msgspec.Struct):
    """Fold ``duplicates`` (redundant pages) into ``keep`` — content-preserving,
    then repoint inbound ``[[links]]`` and delete the folded pages."""

    keep: str = ""
    duplicates: list[str] = msgspec.field(default_factory=list)


class SplitAction(msgspec.Struct):
    """Split an overgrown ``page`` into the named ``subtopics`` (new sibling
    pages in the same directory)."""

    page: str = ""
    subtopics: list[str] = msgspec.field(default_factory=list)


class ReflectPlan(msgspec.Struct):
    """The reorganisation the planner proposes. Every field defaults empty, so an
    empty plan (``{}`` / unparseable output) decodes to a clean no-op — the
    idempotency floor when there's nothing to consolidate (Q7)."""

    concepts: list[ConceptAction] = msgspec.field(default_factory=list)
    merges: list[MergeAction] = msgspec.field(default_factory=list)
    splits: list[SplitAction] = msgspec.field(default_factory=list)
    contradictions: list[str] = msgspec.field(default_factory=list)  # journal only
    notes: str = ""  # one-line summary of what changed + why (journal)


_PLAN_PROMPT = (
    "You are reorganising a knowledge wiki — a set of interlinked markdown pages. "
    'Below is a one-line digest of every page: `path [size] "title": gist (links) '
    "(sources)`.\n\n"
    "Propose a CONSERVATIVE reorganisation as a JSON object with these keys (all "
    "optional — omit a key or use [] when nothing applies):\n"
    "- concepts: [{title, sources}] — a GENERAL concept currently scattered across "
    "the listed source pages that deserves its own /concepts page.\n"
    "- merges: [{keep, duplicates}] — pages that redundantly cover the SAME thing; "
    "keep the best page (a path), fold the duplicate paths into it.\n"
    "- splits: [{page, subtopics}] — ONE overgrown page (large size) to split into "
    "the named subtopics.\n"
    '- contradictions: ["..."] — conflicting facts you noticed (recorded for a '
    "human; do NOT resolve them yourself).\n"
    "- notes: a one-line summary of what you changed and why.\n\n"
    "Only propose an action when there is a CONCRETE defect. If the wiki is already "
    "well-organised, reply with {}. Use exact page paths from the digest. Reply with "
    "ONLY the JSON object, no prose.\n\n"
    "Wiki: {name}\n\nPages:\n{material}\n"
)


def _render_digest(digest: list[PageDigest]) -> str:
    """One line per page for the planner — path, size, title, gist, and (when
    present) its outgoing links + sources."""
    lines: list[str] = []
    for d in digest:
        parts = [f'- {d.path} [{d.size}] "{d.title}": {d.summary}']
        if d.links:
            parts.append(f"(links: {', '.join(d.links)})")
        if d.sources:
            parts.append(f"(sources: {', '.join(d.sources)})")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _parse_plan(raw: str) -> ReflectPlan:
    """Decode the planner's output into a ReflectPlan, tolerating prose around the
    JSON. Any failure (no JSON / malformed / wrong shape) → an empty plan, so a
    model that narrates instead of emitting JSON is a safe no-op, not a crash."""
    s = _unfence(raw).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end < start:
        return ReflectPlan()
    try:
        return msgspec.json.decode(s[start : end + 1].encode(), type=ReflectPlan)
    except (msgspec.ValidationError, msgspec.DecodeError):
        return ReflectPlan()


class WikiReflector:
    """Consolidates one prose collection's wiki: survey → plan → apply."""

    def __init__(
        self,
        spec: SpecStar,
        llm: ILlm,
        *,
        wiki_store: WikiFileStore | None = None,
        split_min_chars: int = _DEFAULT_SPLIT_MIN_CHARS,
    ) -> None:
        self._spec = spec
        self._llm = llm
        self._split_min = split_min_chars
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

    def plan(self, digest: list[PageDigest], *, collection_name: str = "") -> ReflectPlan:
        """One ``collect`` over the digest → a validated reorg plan. The LLM's
        judgment is then filtered against the real page set (unknown paths dropped,
        degenerate actions removed, splits size-gated) so ``apply`` only ever
        touches pages that exist and the plan can't thrash (Q6/Q7)."""
        raw = self._llm.collect(
            _PLAN_PROMPT.replace("{name}", collection_name).replace(
                "{material}", _render_digest(digest)
            )
        )
        return self._validate_plan(_parse_plan(raw), digest)

    def _validate_plan(self, plan: ReflectPlan, digest: list[PageDigest]) -> ReflectPlan:
        """Keep only actions that reference existing pages and aren't degenerate:
        concepts need a title (sources narrowed to known pages); a merge needs a
        known ``keep`` and ≥1 known duplicate that isn't ``keep``; a split needs a
        known, over-threshold page and ≥1 subtopic. Deterministic — the guard rail
        that makes the LLM's plan safe to execute."""
        sizes = {d.path: d.size for d in digest}
        known = set(sizes)
        concepts = [
            ConceptAction(title=c.title.strip(), sources=[s for s in c.sources if s in known])
            for c in plan.concepts
            if c.title.strip()
        ]
        merges: list[MergeAction] = []
        for m in plan.merges:
            dups = [d for d in m.duplicates if d in known and d != m.keep]
            if m.keep in known and dups:
                merges.append(MergeAction(keep=m.keep, duplicates=dups))
        splits: list[SplitAction] = []
        for s in plan.splits:
            subs = [t.strip() for t in s.subtopics if t.strip()]
            if s.page in known and sizes[s.page] >= self._split_min and subs:
                splits.append(SplitAction(page=s.page, subtopics=subs))
        return ReflectPlan(
            concepts=concepts,
            merges=merges,
            splits=splits,
            contradictions=[c.strip() for c in plan.contradictions if c.strip()],
            notes=plan.notes,
        )
