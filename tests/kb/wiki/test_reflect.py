"""Issue #479: WikiReflector — the daily/manual reflection pass that consolidates
a prose wiki. survey (deterministic, 0 LLM) → plan (one collect) → apply (program
control-flow + a bounded collect per action). This module covers the survey step:
a per-page digest read straight off the store, skipping the meta/reserved pages
we don't reflect on."""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.reflect import (
    ConceptAction,
    PageDigest,
    ReflectPlan,
    WikiReflector,
    _extract_links,
    _extract_sources,
    _find_orphans,
    _render_digest,
    _render_journal,
)
from workspace_app.kb.wiki.store import (
    CLARIFICATIONS_DIR,
    CORRECTIONS_DIR,
    REFLECTIONS_DIR,
    WikiFileStore,
    reflection_page_path,
)
from workspace_app.resources import Collection, make_spec


class _ScriptedLlm(ILlm):
    """FIFO of queued responses; records prompts so a test can assert the survey
    step made zero LLM calls."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield (self._responses.pop(0) if self._responses else "x", False)


class _RoutedLlm(ILlm):
    """Response chosen by a substring of the prompt, so a multi-stage reflect run
    needn't depend on call order."""

    def __init__(self, routes: dict[str, str], default: str = "x") -> None:
        self._routes = routes
        self._default = default

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        for key, val in self._routes.items():
            if key in prompt:
                yield (val, False)
                return
        yield (self._default, False)


def _mk_prose(name: str = "c"):
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name=name, use_wiki=True))
        .resource_id
    )
    return spec, cid


async def test_survey_extracts_digests_and_skips_meta_and_reserved_pages():
    spec, cid = _mk_prose()
    store = WikiFileStore(spec)
    # meta scaffolding + reserved ground truth — never surveyed
    await store.write(cid, "/WIKI.md", b"# conventions\n\nhow to write.\n")
    await store.write(cid, "/log.md", b"## [ingest] a\n")
    await store.write(cid, CORRECTIONS_DIR + "entities-foo.md", b"# correction\n\nfix.\n")
    await store.write(cid, REFLECTIONS_DIR + "2026-07-06.md", b"# reflection\n\nlog.\n")
    # knowledge pages — surveyed
    await store.write(
        cid,
        "/entities/reflow-zone-3.md",
        b"# Reflow Zone 3\n\nThe third reflow zone runs at 245 C; see [[voiding]].\n\n"
        b"Sources: reflow-spec.pdf \xc2\xb7 qual-report.md\n",
    )
    await store.write(cid, "/concepts/voiding.md", b"# Voiding\n\nA solder defect.\n")

    reflector = WikiReflector(spec, _ScriptedLlm())
    digests = await reflector.survey(cid)

    assert [d.path for d in digests] == ["/concepts/voiding.md", "/entities/reflow-zone-3.md"]
    z3 = digests[1]
    assert z3.title == "Reflow Zone 3"
    assert "third reflow zone" in z3.summary
    assert z3.links == ["voiding"]
    assert z3.sources == ["reflow-spec.pdf", "qual-report.md"]
    assert z3.size > 0
    # survey is deterministic — it must not touch the LLM
    assert reflector._llm.prompts == []  # ty: ignore[unresolved-attribute]


async def test_survey_title_falls_back_to_the_path_stem_when_no_heading():
    spec, cid = _mk_prose()
    store = WikiFileStore(spec)
    await store.write(cid, "/entities/orphan.md", b"no heading here, just prose.\n")
    digests = await (WikiReflector(spec, _ScriptedLlm())).survey(cid)
    assert digests[0].title == "orphan"


def test_extract_links_dedupes_and_ignores_anchors():
    text = "see [[voiding]] and [[reflow-zone-3#top]] and [[voiding]] again"
    assert _extract_links(text) == ["voiding", "reflow-zone-3"]


def test_extract_sources_splits_on_middot_and_comma():
    assert _extract_sources("Sources: a.pdf · b.md, c.txt") == ["a.pdf", "b.md", "c.txt"]
    assert _extract_sources("no sources line here") == []


# ── P3: plan ────────────────────────────────────────────────────────────

_DIGEST = [
    PageDigest("/entities/zone-3.md", "Zone 3", "runs at 245 C", [], ["voiding"], 300),
    PageDigest("/concepts/voiding.md", "Voiding", "a solder defect", [], [], 200),
    PageDigest("/concepts/voids.md", "Voids", "solder voids (dup)", [], [], 180),
    PageDigest("/entities/big.md", "Big", "a huge grab-bag page", [], [], 9000),
]

_GOOD_PLAN = """```json
{"concepts":[{"title":"Voiding","sources":["/entities/zone-3.md","/nope.md"]}],
 "merges":[{"keep":"/concepts/voiding.md","duplicates":["/concepts/voids.md"]}],
 "splits":[{"page":"/entities/big.md","subtopics":["Setup","Runtime"]}],
 "contradictions":["245 C vs 250 C"],
 "notes":"lifted voiding; merged voids; split big"}
```"""


def test_plan_parses_json_and_keeps_valid_actions():
    r = WikiReflector(make_spec(default_user="u"), _ScriptedLlm([_GOOD_PLAN]))
    plan = r.plan(_DIGEST, collection_name="c")
    assert plan.concepts[0].title == "Voiding"
    # an unknown source page is dropped, the real one kept
    assert plan.concepts[0].sources == ["/entities/zone-3.md"]
    assert plan.merges[0].keep == "/concepts/voiding.md"
    assert plan.merges[0].duplicates == ["/concepts/voids.md"]
    assert plan.splits[0].page == "/entities/big.md"
    assert plan.contradictions == ["245 C vs 250 C"]


def test_plan_returns_empty_on_garbage_output():
    # a model that narrates instead of emitting JSON → a no-op plan (idempotent)
    r = WikiReflector(make_spec(default_user="u"), _ScriptedLlm(["I would reorganise the wiki..."]))
    plan = r.plan(_DIGEST, collection_name="c")
    assert plan == ReflectPlan()


def test_plan_returns_empty_on_malformed_json():
    # a brace is found but the payload doesn't match the schema → no-op (safe)
    r = WikiReflector(make_spec(default_user="u"), _ScriptedLlm(['{"concepts": 5}']))
    assert r.plan(_DIGEST, collection_name="c") == ReflectPlan()


def test_plan_filters_degenerate_actions():
    # empty title / self-merge / empty subtopics / blank contradiction all drop out
    j = (
        '{"concepts":[{"title":"","sources":[]}],'
        '"merges":[{"keep":"/concepts/voiding.md","duplicates":["/concepts/voiding.md"]}],'
        '"splits":[{"page":"/entities/big.md","subtopics":[""]}],'
        '"contradictions":["   "]}'
    )
    r = WikiReflector(make_spec(default_user="u"), _ScriptedLlm([j]), split_min_chars=1)
    assert r.plan(_DIGEST, collection_name="c") == ReflectPlan()


def test_plan_drops_splits_below_the_size_threshold():
    # hysteresis guard: only clearly-overgrown pages may be split, so a just-split
    # small page can't be re-split next day. big.md (9000) stays; a small page drops.
    j = '{"splits":[{"page":"/entities/big.md","subtopics":["A"]},'
    j += '{"page":"/concepts/voiding.md","subtopics":["B"]}]}'
    r = WikiReflector(make_spec(default_user="u"), _ScriptedLlm([j]), split_min_chars=4000)
    plan = r.plan(_DIGEST, collection_name="c")
    assert [s.page for s in plan.splits] == ["/entities/big.md"]


def test_plan_drops_actions_referencing_unknown_pages():
    j = '{"merges":[{"keep":"/nope.md","duplicates":["/concepts/voids.md"]},'
    j += '{"keep":"/concepts/voiding.md","duplicates":["/gone.md"]}],'
    j += '"splits":[{"page":"/missing.md","subtopics":["A"]}]}'
    r = WikiReflector(make_spec(default_user="u"), _ScriptedLlm([j]), split_min_chars=1)
    plan = r.plan(_DIGEST, collection_name="c")
    assert plan.merges == []  # first keep unknown; second's only duplicate unknown → empty
    assert plan.splits == []


def test_render_digest_lists_each_page_with_signals():
    digest = [
        PageDigest("/entities/zone-3.md", "Zone 3", "runs at 245 C", ["spec.pdf"], ["voiding"], 300)
    ]
    out = _render_digest(digest)
    # relative, like list_files/search_wiki — the planner is told to reply with
    # "exact page paths from the digest", so this is the dialect it echoes back
    assert "entities/zone-3.md" in out
    assert "/entities/zone-3.md" not in out
    assert "Zone 3" in out
    assert "voiding" in out  # link
    assert "245 C" in out  # summary
    assert "spec.pdf" in out  # source


# ── P4: apply + reflect ──────────────────────────────────────────────────

_E2E_PLAN = (
    '{"concepts":[{"title":"Thermal Profile","sources":["/entities/zone-a.md"]}],'
    '"merges":[{"keep":"/concepts/voiding.md","duplicates":["/concepts/voids.md"]}],'
    '"splits":[{"page":"/entities/big.md","subtopics":["Setup"]}],'
    '"contradictions":["245 vs 250"],'
    '"notes":"reflection notes"}'
)

_E2E_ROUTES = {
    "reorganising a knowledge wiki": _E2E_PLAN,
    "synthesised from the source pages": "The thermal profile is a temperature curve.",
    "redundantly cover the SAME thing": "# Voiding\n\nMerged voiding facts.",
    "grown too broad": "# Setup\n\nSetup details.",
}


async def _seed_wiki(spec, cid) -> WikiFileStore:
    store = WikiFileStore(spec)
    await store.write(
        cid, "/entities/zone-a.md", b"# Zone A\n\nZone A. [[voiding]]\n\nSources: spec.pdf\n"
    )
    await store.write(cid, "/entities/zone-b.md", b"# Zone B\n\nZone B mentions [[voids]].\n")
    await store.write(cid, "/concepts/voiding.md", b"# Voiding\n\nVoiding is a defect.\n")
    await store.write(cid, "/concepts/voids.md", b"# Voids\n\nSolder voids (a duplicate).\n")
    await store.write(cid, "/entities/big.md", b"# Big\n\n" + b"x" * 300)
    # a reserved ground-truth page present during the run — repoint must skip it
    await store.write(cid, CLARIFICATIONS_DIR + "q.md", b"# q\n\nhuman answer\n")
    return store


async def test_reflect_end_to_end_consolidates_the_wiki():
    spec, cid = _mk_prose()
    store = await _seed_wiki(spec, cid)
    phases: list[str] = []
    r = WikiReflector(spec, _RoutedLlm(_E2E_ROUTES), split_min_chars=100)

    plan = await r.reflect(cid, today="2026-07-06", collection_name="c", on_phase=phases.append)

    assert phases == ["surveying", "planning", "applying"]
    # concept lifted into its own page, carrying the source page's provenance
    concept = (await store.read(cid, "/concepts/thermal-profile.md")).decode()
    assert concept.startswith("# Thermal Profile")  # heading synthesised (model omitted it)
    assert "Sources: spec.pdf" in concept
    # merge: keep rewritten, duplicate deleted, inbound [[voids]] repointed to [[voiding]]
    assert (
        await store.read(cid, "/concepts/voiding.md")
    ).decode() == "# Voiding\n\nMerged voiding facts.\n"
    assert not await store.exists(cid, "/concepts/voids.md")
    assert "[[voiding]]" in (await store.read(cid, "/entities/zone-b.md")).decode()
    # split: original becomes a hub linking the new subpage
    assert "[[setup]]" in (await store.read(cid, "/entities/big.md")).decode()
    assert await store.exists(cid, "/entities/setup.md")
    # term index rebuilt + journal written under the reserved folder
    assert "Concepts & Terms" in (await store.read(cid, "/glossary.md")).decode()
    journal = (await store.read(cid, reflection_page_path("2026-07-06"))).decode()
    assert "reflection notes" in journal and "245 vs 250" in journal and "Orphan pages" in journal
    # the reserved human page was never touched by the link repoint
    assert (await store.read(cid, CLARIFICATIONS_DIR + "q.md")).decode() == "# q\n\nhuman answer\n"
    assert plan.notes == "reflection notes"


async def test_write_page_suppresses_an_unchanged_write():
    spec, cid = _mk_prose()
    r = WikiReflector(spec, _ScriptedLlm())
    assert await r._write_page(cid, "/concepts/x.md", "# X\n\nbody\n") is True  # new
    assert await r._write_page(cid, "/concepts/x.md", "# X\n\nbody\n") is False  # unchanged → skip
    assert await r._write_page(cid, "/concepts/x.md", "# X\n\nEDITED\n") is True  # changed → write


async def test_apply_concept_without_sources_writes_no_footer():
    spec, cid = _mk_prose()
    store = WikiFileStore(spec)
    await store.write(cid, "/entities/plain.md", b"# Plain\n\nno provenance line here.\n")
    r = WikiReflector(spec, _RoutedLlm({"synthesised from the source pages": "# C\n\ndef."}))
    digest = await r.survey(cid)
    await r.apply(
        cid, ReflectPlan(concepts=[ConceptAction("C", ["/entities/plain.md"])]), digest, today="d"
    )
    assert "Sources:" not in (await store.read(cid, "/concepts/c.md")).decode()


async def test_apply_empty_plan_writes_only_the_journal():
    # an empty plan (well-organised wiki / garbage output) is a near no-op: no
    # glossary is written when there are no concept pages, just the journal.
    spec, cid = _mk_prose()
    store = WikiFileStore(spec)
    await store.write(cid, "/entities/lone.md", b"# Lone\n\nonly page.\n")
    r = WikiReflector(spec, _ScriptedLlm(["not json"]))
    await r.reflect(cid, today="2026-07-06")  # no on_phase → covers the None branch
    assert not await store.exists(cid, "/glossary.md")
    assert await store.exists(cid, reflection_page_path("2026-07-06"))


def test_find_orphans_flags_unlinked_non_root_pages():
    digest = [
        PageDigest("/index.md", "Home", "", [], ["voiding"], 10),  # root — excluded
        PageDigest("/concepts/voiding.md", "Voiding", "", [], [], 10),  # inbound — kept
        PageDigest("/entities/lonely.md", "Lonely", "", [], [], 10),  # no inbound → orphan
    ]
    assert _find_orphans(digest) == ["/entities/lonely.md"]


def test_render_journal_variants():
    full = _render_journal(
        "2026-07-06",
        ReflectPlan(contradictions=["a vs b"], notes="did things"),
        ["concept: X → /concepts/x.md"],
        ["/entities/orphan.md"],
    )
    assert "did things" in full
    assert "concept: X" in full
    assert "a vs b" in full
    assert "/entities/orphan.md" in full
    empty = _render_journal("2026-07-06", ReflectPlan(), [], [])
    assert "(nothing to consolidate)" in empty
    assert "Contradictions" not in empty and "Orphan" not in empty


def test_plan_validation_accepts_the_relative_paths_the_digest_showed():
    """The digest the planner reads lists pages relative (`entities/zone-a.md`),
    and the prompt tells it to reply with the exact paths from that digest — so
    validation has to recognise that form. Matching only the store's `/`-prefixed
    key would filter EVERY action out and turn reflection into a silent no-op."""
    plan_json = (
        '{"concepts":[{"title":"Thermal","sources":["entities/zone-a.md"]}],'
        '"merges":[{"keep":"concepts/voiding.md","duplicates":["concepts/voids.md"]}],'
        '"splits":[{"page":"entities/big.md","subtopics":["Setup"]}],'
        '"contradictions":[],"notes":""}'
    )
    digest = [
        PageDigest("/entities/zone-a.md", "Zone A", "", [], [], 100),
        PageDigest("/concepts/voiding.md", "Voiding", "", [], [], 100),
        PageDigest("/concepts/voids.md", "Voids", "", [], [], 100),
        PageDigest("/entities/big.md", "Big", "", [], [], 9000),
    ]
    r = WikiReflector(make_spec(default_user="u"), _ScriptedLlm([plan_json]), split_min_chars=1)
    plan = r.plan(digest, collection_name="c")

    assert [c.sources for c in plan.concepts] == [["/entities/zone-a.md"]]
    assert [(m.keep, m.duplicates) for m in plan.merges] == [
        ("/concepts/voiding.md", ["/concepts/voids.md"])
    ]
    assert [s.page for s in plan.splits] == ["/entities/big.md"]
