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
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import msgspec

from .code_wiki import _first_paragraph_after_h1, _slugify, _unfence
from .store import (
    MaintainerWikiStore,
    WikiFileStore,
    _is_reserved,
    _norm_path,
    reflection_page_path,
)

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..llm import ILlm

# Notified with the current stage name (surveying | planning | applying) as the
# reflection progresses, so a caller can surface live status.
OnPhase = Callable[[str], None]

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


# ── apply (program control-flow + a bounded collect per action) ──────────

_CONCEPT_PROMPT = (
    "Write a concise knowledge-wiki page for the general concept below, synthesised "
    "from the source pages' content. Cover the concept itself — not the individual "
    "entities it appears on. A few short paragraphs of factual, skimmable markdown. "
    "Start with a `# {title}` heading. No preamble.\n\n"
    "Concept: {title}\n\nSource pages:\n{material}\n"
)

_MERGE_PROMPT = (
    "These wiki pages redundantly cover the SAME thing. Merge them into ONE coherent "
    "page: keep every distinct fact, drop only the duplication, reconcile the order. "
    "Start with a `# ` heading. Factual markdown, no preamble.\n\nPages:\n{material}\n"
)

_SPLIT_PROMPT = (
    "The source page below has grown too broad. Write the focused wiki subpage for "
    "just ONE subtopic of it: '{subtopic}'. Include only what's relevant to that "
    "subtopic. Start with a `# {subtopic}` heading. Markdown, no preamble.\n\n"
    "Source page ({page}):\n{content}\n"
)

# The reflection's own wiki-internal term index (#479 Q4): a concept·term index it
# owns and rebuilds each pass, so the general concepts it lifts are discoverable
# without fighting the fold maintainer over /index.md.
_GLOSSARY_PAGE = "/glossary.md"
# Entry-point pages that legitimately have no inbound [[links]] — never flagged as
# orphans.
_ROOT_STEMS = frozenset({"index", "glossary"})


def _stem(path: str) -> str:
    """A page's ``[[wikilink]]`` stem — its basename without the ``.md``."""
    return posixpath.basename(path).removesuffix(".md")


def _ensure_heading(body: str, title: str) -> str:
    """A page body guaranteed to open with a ``# `` heading (the model usually
    includes one; add ``# {title}`` when it doesn't) and end with one newline."""
    b = body.strip()
    if not b.startswith("# "):
        b = f"# {title}\n\n{b}"
    return b + "\n"


def _find_orphans(digest: list[PageDigest]) -> list[str]:
    """Pages nothing links to (deterministic, from the survey link graph) — flagged
    in the journal for a human, never deleted (Q5 #7). Entry-point roots are
    excluded (they legitimately have no inbound links)."""
    inbound: set[str] = set()
    for d in digest:
        inbound.update(d.links)
    return sorted(
        d.path for d in digest if _stem(d.path) not in inbound and _stem(d.path) not in _ROOT_STEMS
    )


def _render_journal(today: str, plan: ReflectPlan, applied: list[str], orphans: list[str]) -> str:
    """The ``/reflections/<date>.md`` page — a human-readable record of what this
    pass reorganised, plus the contradictions + orphans it surfaced but left for a
    person to act on."""
    lines = [f"# Reflection {today}", ""]
    if plan.notes.strip():
        lines += [plan.notes.strip(), ""]
    lines.append("## Actions")
    lines += [f"- {a}" for a in applied] if applied else ["- (nothing to consolidate)"]
    if plan.contradictions:
        lines += ["", "## Contradictions (for a human)"]
        lines += [f"- {c}" for c in plan.contradictions]
    if orphans:
        lines += ["", "## Orphan pages (unlinked — flagged, not deleted)"]
        lines += [f"- {o}" for o in orphans]
    return "\n".join(lines).rstrip() + "\n"


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

    # ── apply ────────────────────────────────────────────────────────────
    async def reflect(
        self,
        collection_id: str,
        *,
        today: str,
        collection_name: str = "",
        on_phase: OnPhase | None = None,
    ) -> ReflectPlan:
        """Run one full reflection: survey → plan → apply. ``today`` dates the
        journal page; ``on_phase`` (optional) is notified as each stage begins, so
        the coordinator can surface live progress. Returns the executed plan."""

        def phase(name: str) -> None:
            if on_phase is not None:
                on_phase(name)

        phase("surveying")
        digest = await self.survey(collection_id)
        phase("planning")
        plan = self.plan(digest, collection_name=collection_name)
        phase("applying")
        await self.apply(collection_id, plan, digest, today=today)
        return plan

    async def apply(
        self, collection_id: str, plan: ReflectPlan, digest: list[PageDigest], *, today: str
    ) -> None:
        """Execute the plan: one bounded ``collect`` per concept/merge/split, the
        program writing each page (through the guarded store, write-suppressed), then
        rebuild the term index and record the pass in the journal. Deterministic
        control flow — coverage of every action is guaranteed by iterating the plan,
        not by the model's diligence."""
        applied: list[str] = []
        for c in plan.concepts:
            applied.append(await self._apply_concept(collection_id, c, digest))
        for m in plan.merges:
            applied.append(await self._apply_merge(collection_id, m))
        for s in plan.splits:
            applied.append(await self._apply_split(collection_id, s))
        await self._rebuild_glossary(collection_id)
        journal = _render_journal(today, plan, applied, _find_orphans(digest))
        # The journal lives under the reserved /reflections/ folder, so it's written
        # through the RAW store (the guarded view would drop it) — the reflect pass
        # owns that folder; the fold maintainer can't.
        await self._raw.write(collection_id, reflection_page_path(today), journal.encode("utf-8"))

    async def _read(self, cid: str, path: str) -> str:
        return (await self._raw.read(cid, path)).decode("utf-8", errors="replace")

    async def _write_page(self, cid: str, path: str, text: str) -> bool:
        """Write a reorganised page through the guarded store, suppressing a no-op:
        if the new bytes equal the current page, skip the write entirely (no
        revision, no churn) — the deterministic half of the idempotency net (Q7a)."""
        data = text.encode("utf-8")
        prev = await self._raw.read_with_etag(cid, path)
        if prev is not None and prev[0] == data:
            return False
        await self._store.write(cid, path, data)
        return True

    async def _apply_concept(self, cid: str, c: ConceptAction, digest: list[PageDigest]) -> str:
        """Lift a general concept into its own /concepts page, synthesised from the
        source pages it was scattered across; carry their provenance forward."""
        by_path = {d.path: d for d in digest}
        material_parts: list[str] = []
        sources: list[str] = []
        for p in c.sources:
            material_parts.append(f"## {p}\n{await self._read(cid, p)}")
            sources.extend(by_path[p].sources)
        body = _unfence(
            self._llm.collect(
                _CONCEPT_PROMPT.replace("{title}", c.title).replace(
                    "{material}", "\n\n".join(material_parts)
                )
            )
        )
        text = _ensure_heading(body, c.title)
        if sources:
            text = text.rstrip() + f"\n\nSources: {' · '.join(dict.fromkeys(sources))}\n"
        page = f"/concepts/{_slugify(c.title)}.md"
        await self._write_page(cid, page, text)
        return f"concept: {c.title} → {page}"

    async def _apply_merge(self, cid: str, m: MergeAction) -> str:
        """Fold the duplicate pages into ``keep`` (content-preserving), delete them,
        and repoint every inbound ``[[link]]`` from a duplicate's stem to keep's."""
        material = f"## {m.keep}\n{await self._read(cid, m.keep)}"
        for d in m.duplicates:
            material += f"\n\n## {d}\n{await self._read(cid, d)}"
        body = _unfence(self._llm.collect(_MERGE_PROMPT.replace("{material}", material)))
        await self._write_page(cid, m.keep, _ensure_heading(body, _stem(m.keep)))
        for d in m.duplicates:
            await self._store.delete(cid, d)
        await self._repoint_links(cid, [_stem(d) for d in m.duplicates], _stem(m.keep))
        return f"merge: {', '.join(m.duplicates)} → {m.keep}"

    async def _apply_split(self, cid: str, s: SplitAction) -> str:
        """Split an overgrown page into focused subtopic pages, then rewrite the
        original into a short hub that links them (so inbound links still resolve)."""
        content = await self._read(cid, s.page)
        directory = posixpath.dirname(s.page)
        stems: list[str] = []
        for sub in s.subtopics:
            body = _unfence(
                self._llm.collect(
                    _SPLIT_PROMPT.replace("{subtopic}", sub)
                    .replace("{page}", s.page)
                    .replace("{content}", content)
                )
            )
            slug = _slugify(sub)
            await self._write_page(cid, f"{directory}/{slug}.md", _ensure_heading(body, sub))
            stems.append(slug)
        hub = (
            f"# {_stem(s.page)}\n\nSplit into:\n" + "\n".join(f"- [[{st}]]" for st in stems) + "\n"
        )
        await self._write_page(cid, s.page, hub)
        return f"split: {s.page} → {', '.join(stems)}"

    async def _repoint_links(self, cid: str, old_stems: list[str], new_stem: str) -> None:
        """Rewrite ``[[old]]`` → ``[[new]]`` across every page (preserving any
        ``#anchor`` / ``|alias``) after a merge, so no link dangles. Skips the
        reserved ground-truth / journal folders."""
        patterns = [
            (re.compile(r"\[\[" + re.escape(old) + r"(?=[\]|#])"), old) for old in old_stems
        ]
        for path in await self._raw.ls(cid):
            if _is_reserved(path):
                continue
            text = await self._read(cid, path)
            new = text
            for pat, _old in patterns:
                new = pat.sub("[[" + new_stem, new)
            if new != text:
                await self._write_page(cid, path, new)

    async def _rebuild_glossary(self, cid: str) -> None:
        """Rebuild the wiki-internal concept·term index (#479 Q4) from the current
        /concepts pages — deterministic, 0 LLM. Skipped when there are no concept
        pages yet (nothing to index)."""
        concepts = sorted(p for p in await self._raw.ls(cid) if p.startswith("/concepts/"))
        if not concepts:
            return
        lines = ["# Concepts & Terms", "", "Auto-maintained index of the wiki's concepts.", ""]
        for p in concepts:
            lines.append(
                f"- [[{_stem(p)}]] — {_first_paragraph_after_h1(await self._read(cid, p))}"
            )
        await self._write_page(cid, _GLOSSARY_PAGE, "\n".join(lines).rstrip() + "\n")
