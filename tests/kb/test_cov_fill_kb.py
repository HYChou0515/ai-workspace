"""Characterization tests filling coverage gaps in kb modules
(preview error paths, prompt loader, wiki store directory ops, and the
wiki maintainer/reader/orchestrator event-loop branches).

These exercise specific previously-uncovered lines/branches; behaviour is
asserted, not just executed, so they double as regression locks.
"""

from __future__ import annotations

from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import MessageDelta
from workspace_app.kb.preview import preview_markdown
from workspace_app.kb.prompts import load_kb_system_prompt
from workspace_app.kb.wiki.maintainer import default_wiki_maintainer_config, run_wiki_maintainer
from workspace_app.kb.wiki.orchestrator import WikiAwareRunner
from workspace_app.kb.wiki.reader import answer_from_wiki, default_wiki_reader_config
from workspace_app.kb.wiki.sources import IWikiSources
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import AgentConfig, Collection, make_spec

# ── kb.preview error / empty paths ───────────────────────────────────


def test_empty_csv_with_no_header_row_yields_empty_preview():
    """A CSV/TSV with no rows at all → `next(reader)` raises StopIteration →
    the preview is "" (preview.py 94-95)."""
    assert preview_markdown(path="empty.csv", content_type="text/csv", raw=b"") == ""
    assert preview_markdown(path="empty.tsv", content_type="text/plain", raw=b"") == ""


def test_corrupt_xlsx_falls_back_to_empty_preview():
    """A blob that is NOT a real xlsx makes pandas/openpyxl raise → the viewer
    falls back to the download notice (preview.py 106-108)."""
    md = preview_markdown(path="bad.xlsx", content_type="application/zip", raw=b"not a real xlsx")
    assert md == ""


def test_corrupt_docx_falls_back_to_empty_preview():
    """A blob that is NOT a real docx makes docx2txt raise → "" (preview.py
    125-127)."""
    md = preview_markdown(path="bad.docx", content_type="application/zip", raw=b"not a real docx")
    assert md == ""


# ── kb.prompts loader ────────────────────────────────────────────────


def test_load_kb_system_prompt_reads_the_bundled_markdown():
    """The loader reads system.md from the package resources (prompts/__init__
    line 10)."""
    prompt = load_kb_system_prompt()
    assert isinstance(prompt, str)
    assert prompt.strip()  # non-empty bundled prompt


# ── WikiFileStore directory operations ───────────────────────────────


def _spec_with_collection() -> tuple[SpecStar, str]:
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    return spec, cid


async def test_mkdir_is_a_noop():
    """Directories are implicit; mkdir does nothing and returns None (store.py
    line 193)."""
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    assert await store.mkdir(cid, "/entities") is None
    # No page was created → the directory is still implicit / absent.
    assert await store.ls(cid) == []


async def test_rmdir_removes_every_page_under_the_prefix():
    """rmdir deletes all pages under the directory prefix (store.py 196-199)."""
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    await store.write(cid, "/entities/a.md", b"a")
    await store.write(cid, "/entities/b.md", b"b")
    await store.write(cid, "/index.md", b"keep me")

    await store.rmdir(cid, "/entities")

    assert sorted(await store.ls(cid)) == ["/index.md"]


async def test_is_dir_true_only_when_a_page_lives_under_it():
    """is_dir is derived from page paths (store.py 202-203)."""
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    await store.write(cid, "/entities/a.md", b"a")
    assert await store.is_dir(cid, "/entities") is True
    assert await store.is_dir(cid, "/concepts") is False


async def test_listdir_returns_implicit_directory_ancestors():
    """listdir derives every directory from the page paths (store.py 208-214)."""
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    await store.write(cid, "/entities/sub/a.md", b"a")
    await store.write(cid, "/index.md", b"i")

    dirs = await store.listdir(cid)
    assert "/entities" in dirs
    assert "/entities/sub" in dirs
    # Prefix-scoped listing too.
    assert await store.listdir(cid, "/entities/sub") == ["/entities/sub"]


# ── wiki maintainer: on_event is None while events are yielded ────────


class _EventEmittingRunner:
    """Yields one event so the maintainer's relay loop runs its body."""

    async def run(self, prompt, ctx):
        yield MessageDelta(text="folding…")


async def test_maintainer_runs_with_no_on_event_relay():
    """When on_event is None the maintainer still consumes the runner's events
    without relaying (maintainer.py branch 126->125)."""
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    await run_wiki_maintainer(
        _EventEmittingRunner(),
        wiki_store=store,
        wiki_sources=_FakeSources({}),
        collection_id=cid,
        new_source="x",
        agent_config=default_wiki_maintainer_config(),
        on_event=None,
    )
    # WIKI.md still got seeded (the pass ran to completion).
    assert b"knowledge wiki" in await store.read(cid, "/WIKI.md")


class _FakeSources(IWikiSources):
    def __init__(self, docs):
        self._docs = docs

    def list(self):
        return sorted(self._docs)

    def read(self, path):
        return self._docs.get(path)

    def ref(self, path):
        return None

    def ref_by_id(self, doc_id):
        return None


# ── wiki reader: a non-text, non-error event loops back ──────────────


class _ReasoningThenTextReader:
    """Yields a reasoning delta (neither appended nor an error) then the answer
    — exercises reader.py branch 100->95."""

    async def run(self, question, ctx):
        yield MessageDelta(text="thinking about zones", reasoning=True)
        yield MessageDelta(text="Zone 3 setpoint is 245C.")


async def test_reader_ignores_reasoning_deltas_in_the_answer_text():
    spec, cid = _spec_with_collection()
    store = WikiFileStore(spec)
    await store.write(cid, "/index.md", b"# Reflow\n")

    answer, cites = await answer_from_wiki(
        _ReasoningThenTextReader(),  # ty: ignore[invalid-argument-type]
        wiki_store=store,
        wiki_sources=_FakeSources({}),
        collection_id=cid,
        question="setpoint?",
        agent_config=default_wiki_reader_config(),
    )
    # The reasoning text is dropped; only the content delta is the answer.
    assert answer == "Zone 3 setpoint is 245C."
    assert "thinking about zones" not in answer
    assert cites == []


# ── orchestrator: _collect ignores reasoning deltas (151->148) ───────


class _ReasoningInCollect:
    """Each wiki sub-run yields a reasoning delta (ignored by _collect) then a
    text answer; the merge path runs because there are two wiki collections."""

    async def run(self, prompt, ctx):
        cfg = ctx.agent_config
        if cfg is not None and cfg.name == "Wiki Merge":
            yield MessageDelta(text="Merged answer.")
            return
        # wiki sub-run
        yield MessageDelta(text="pondering", reasoning=True)
        yield MessageDelta(text="A wiki draft.")


async def test_collect_drops_reasoning_deltas_from_drafts():
    spec = make_spec(default_user="u")
    rm = spec.get_resource_manager(Collection)
    c1 = rm.create(Collection(name="a", use_rag=False, use_wiki=True)).resource_id
    c2 = rm.create(Collection(name="b", use_rag=False, use_wiki=True)).resource_id
    runner = WikiAwareRunner(_ReasoningInCollect(), spec)
    ctx = _wiki_ctx([c1, c2])

    answer = await _drive(runner, "q", ctx)
    # Two wiki drafts collected (reasoning dropped) → merged.
    assert answer.startswith("Merged answer")


# ── orchestrator: _classify skips a non-Collection record (line 94) ──


class _NonCollectionData:
    """A record whose .data is not a Collection — _classify must skip it."""

    def __init__(self, data) -> None:
        self.data = data


class _FakeCollRm:
    """Stands in for the Collection resource manager: returns a record whose
    `.data` is NOT a Collection, forcing the isinstance guard (orchestrator.py
    line 94)."""

    def get(self, cid):
        return _NonCollectionData(object())


async def test_classify_skips_records_that_are_not_collections():
    spec = make_spec(default_user="u")
    runner = WikiAwareRunner(_NeverCalledBase(), spec)
    # Swap in a manager that yields a non-Collection record.
    runner._coll_rm = _FakeCollRm()  # ty: ignore[invalid-assignment]
    rag, wiki = runner._classify(["whatever"])
    assert rag == [] and wiki == []


class _NeverCalledBase:
    async def run(self, prompt, ctx):  # pragma: no cover — not driven here
        if False:
            yield


# ── shared helpers ───────────────────────────────────────────────────


def _wiki_ctx(collection_ids):
    return AgentToolContext(
        collection_ids=collection_ids,
        agent_config=AgentConfig(name="KB"),
        wiki_query=True,
    )


async def _drive(runner, prompt, ctx) -> str:
    parts: list[str] = []
    async for ev in runner.run(prompt, ctx):
        if isinstance(ev, MessageDelta) and not ev.reasoning:
            parts.append(ev.text)
    return "".join(parts)
