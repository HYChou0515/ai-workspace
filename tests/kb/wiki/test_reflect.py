"""Issue #479: WikiReflector — the daily/manual reflection pass that consolidates
a prose wiki. survey (deterministic, 0 LLM) → plan (one collect) → apply (program
control-flow + a bounded collect per action). This module covers the survey step:
a per-page digest read straight off the store, skipping the meta/reserved pages
we don't reflect on."""

from __future__ import annotations

from collections.abc import Iterator

from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.reflect import (
    WikiReflector,
    _extract_links,
    _extract_sources,
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
