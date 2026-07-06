"""Issue #479: WikiReflector — the daily/manual reflection pass that consolidates
a prose wiki. survey (deterministic, 0 LLM) → plan (one collect) → apply (program
control-flow + a bounded collect per action). This module covers the survey step:
a per-page digest read straight off the store, skipping the meta/reserved pages
we don't reflect on."""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.reflect import (
    PageDigest,
    ReflectPlan,
    WikiReflector,
    _extract_links,
    _extract_sources,
    _render_digest,
)
from workspace_app.kb.wiki.store import (
    CORRECTIONS_DIR,
    REFLECTIONS_DIR,
    WikiFileStore,
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
    assert "/entities/zone-3.md" in out
    assert "Zone 3" in out
    assert "voiding" in out  # link
    assert "245 C" in out  # summary
    assert "spec.pdf" in out  # source
