"""Wiki agent tools (#50 P2) — the maintainer/reader reuse the existing
file tools (read_file/write_file/edit_file/ls) over a WikiFileStore, plus:

  - search_wiki: FileStore-backed grep over the wiki pages (Karpathy uses
    grep; sandbox-free so no exec/sandbox), reusing the search primitives.
  - read_new_source: the source doc text that triggered this maintainer run.
  - list_sources / read_source: read-only access to the collection's raw
    sources (layer 1) so the maintainer can re-read / cross-reference.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, WikiSearchBudget
from workspace_app.agent.tools import (
    _coerce_source_path,
    list_sources_impl,
    read_new_source_impl,
    read_source_impl,
    search_wiki_impl,
    write_file_impl,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.wiki.sources import IWikiSources, WikiSourceRef


class _FakeSources(IWikiSources):
    def __init__(self, docs: dict[str, str]) -> None:
        self._docs = docs

    def list(self) -> list[str]:  # ty: ignore[invalid-type-form]
        return sorted(self._docs)

    def read(self, path: str) -> str | None:
        return self._docs.get(path)

    def ref(self, path: str) -> WikiSourceRef | None:
        text = self._docs.get(path)
        if text is None:
            return None
        return WikiSourceRef(document_id=f"doc::{path}", collection_id="c1", path=path, text=text)

    def ref_by_id(self, doc_id: str) -> WikiSourceRef | None:
        for path in self._docs:
            if f"doc::{path}" == doc_id:
                return self.ref(path)
        return None


def _ctx(**kw) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(
        AgentToolContext(investigation_id="wiki:c1", files=WorkspaceFiles(MemoryFileStore()), **kw)
    )


async def test_search_wiki_greps_the_pages():
    ctx = _ctx()
    await write_file_impl(ctx, "/index.md", "# Index\n- [[reflow]]\n")
    await write_file_impl(ctx, "/entities/reflow.md", "Reflow zone 3 runs hot.\nSee voiding.\n")
    await write_file_impl(ctx, "/concepts/voiding.md", "Voiding is bad.\n")

    out = await search_wiki_impl(ctx, "reflow")
    # grep-style path:line: hits, across pages, case-insensitive. Paths are
    # relative to the wiki root — the same form `list_files` prints, so the
    # agent never has to translate between two dialects of the same path.
    assert "entities/reflow.md:1:" in out
    assert "Reflow zone 3" in out
    # the index link line matches too
    assert "index.md" in out
    assert "/entities/reflow.md" not in out and "/index.md" not in out


async def test_search_wiki_greps_across_all_chat_collections():
    # #506 Task#1: the interactive kb_chat agent has NO single investigation_id — it
    # spans several collections. search_wiki must grep EACH collection's wiki store
    # (WikiFileStore is keyed per-collection) and merge, so the agent can consult the
    # wiki as a budgeted in-agent tool instead of the heavy whole-page reader routing.
    store = MemoryFileStore()
    await store.write("c1", "/entities/reflow.md", b"Reflow zone 3 runs hot.\n")
    await store.write("c2", "/concepts/voiding.md", b"Reflow can cause voiding.\n")
    ctx = RunContextWrapper(
        AgentToolContext(collection_ids=["c1", "c2"], files=WorkspaceFiles(store))
    )

    out = await search_wiki_impl(ctx, "reflow")

    # multi-collection hits stay disambiguated by collection, and the join keeps
    # exactly one separator now that the page path no longer carries a leading `/`.
    assert "c1/entities/reflow.md" in out  # a hit from c1's wiki
    assert "c2/concepts/voiding.md" in out  # AND from c2's — greps across collections


async def test_search_wiki_appends_budget_footer_when_capped():
    # #506: mirror kb_search — when a per-draft wiki-search cap is set, every
    # result tells the model how much of its budget remains, so it searches
    # frugally instead of re-grepping the wiki up to max_turns.
    ctx = _ctx(wiki_search_budget=WikiSearchBudget(max_calls=3))
    await write_file_impl(ctx, "/entities/reflow.md", "Reflow zone 3 runs hot.\n")

    out = await search_wiki_impl(ctx, "reflow")

    assert "entities/reflow.md" in out  # the hits are still returned
    assert "1 of 3 used" in out  # first search of the budget
    assert "2 left" in out


async def test_search_wiki_sentinel_when_budget_exhausted():
    # #506: the N+1th grep never runs — a sentinel steers the model to answer
    # from the wiki content it already has (mirror kb_search).
    ctx = _ctx(wiki_search_budget=WikiSearchBudget(max_calls=1))
    await write_file_impl(ctx, "/entities/reflow.md", "Reflow zone 3 runs hot.\n")
    await search_wiki_impl(ctx, "reflow")  # 1 of 1
    out = await search_wiki_impl(ctx, "zone")  # exhausted
    assert "budget" in out.lower() and "answer" in out.lower()
    assert "entities/reflow.md" not in out  # no hits — it didn't grep


async def test_search_wiki_cap_zero_is_disabled_not_exhausted():
    # #506: cap 0 = "no wiki search this reply" — reads as deliberately disabled.
    ctx = _ctx(wiki_search_budget=WikiSearchBudget(max_calls=0))
    await write_file_impl(ctx, "/entities/reflow.md", "Reflow zone 3.\n")
    out = await search_wiki_impl(ctx, "reflow")
    assert "no wiki searches" in out.lower()
    assert "exhausted" not in out.lower()


async def test_search_wiki_uncapped_has_no_footer():
    # #506: unlimited (None) — no budget bookkeeping, no footer (the maintainer/
    # reader path is unchanged).
    ctx = _ctx(wiki_search_budget=WikiSearchBudget(max_calls=None))
    await write_file_impl(ctx, "/entities/reflow.md", "Reflow zone 3.\n")
    out = await search_wiki_impl(ctx, "reflow")
    assert "budget" not in out.lower()


async def test_search_wiki_no_match():
    ctx = _ctx()
    await write_file_impl(ctx, "/index.md", "nothing here\n")
    out = await search_wiki_impl(ctx, "absent")
    assert "no wiki pages match" in out.lower()


async def test_read_new_source_returns_the_triggering_doc_text():
    ctx = _ctx(wiki_new_source="The reflow oven spec, revision C.")
    assert "revision C" in await read_new_source_impl(ctx)
    # absent → a clear notice, not a crash
    assert "no" in (await read_new_source_impl(_ctx())).lower()


def test_coerce_source_path_recovers_a_code_wiki_card_path():
    # #281 P7: read_source falls back through this when handed a wiki CARD path.
    assert _coerce_source_path("/files/app/queue.py.md") == "app/queue.py"
    assert _coerce_source_path("/files/app/queue.py") == "app/queue.py"  # /files/ but no .md
    assert _coerce_source_path("/files/README.md.md") == "README.md"  # strip exactly one .md
    assert _coerce_source_path("app/queue.py") == "app/queue.py"  # plain source path untouched


def test_coerce_source_path_accepts_the_relative_card_path_the_agent_is_shown():
    # The card path the agent actually sees now comes from `list_files` /
    # `search_wiki`, which print relative paths — so the fallback has to
    # recognise the slash-free form too, or it stops firing exactly when the
    # model copies what it was shown. (This is a FALLBACK, tried only after a
    # real source lookup missed, so a genuine source living at `files/…` still
    # resolves first and is never coerced.)
    assert _coerce_source_path("files/app/queue.py.md") == "app/queue.py"
    assert _coerce_source_path("files/README.md.md") == "README.md"


async def test_read_source_maintainer_mode_tolerates_a_card_path():
    # #281 P7: even on a maintainer run (plain text), the /files/<src>.md card
    # form resolves to the source — small models confuse the two.
    sources = _FakeSources({"app/queue.py": "class TaskQueue: ..."})
    ctx = _ctx(wiki_sources=sources)  # no wiki_cite_sources → maintainer path
    out = await read_source_impl(ctx, "/files/app/queue.py.md")
    assert "TaskQueue" in out


async def test_list_and_read_sources():
    sources = _FakeSources(
        {"reflow-spec.md": "Zone 3 setpoint 245C.", "qual.md": "lot 25-W14 passed."}
    )
    ctx = _ctx(wiki_sources=sources)
    listed = await list_sources_impl(ctx)
    assert "reflow-spec.md" in listed and "qual.md" in listed
    # Maintainer mode (no wiki_cite_sources): plain text, no numbering.
    out = await read_source_impl(ctx, "reflow-spec.md")
    assert "245C" in out and not out.startswith("[")
    assert "not found" in (await read_source_impl(ctx, "missing.md")).lower()


async def test_read_source_maintainer_prefixes_the_full_source_path():
    # #485: the maintainer must know WHERE the source lives — its full folder
    # path — mirroring the `Source path:` header the coordinator adds to
    # read_new_source. A bare body loses the placement location entirely.
    sources = _FakeSources({"manuals/reflow/guide.md": "Zone 3 setpoint 245C."})
    ctx = _ctx(wiki_sources=sources)  # maintainer mode

    out = await read_source_impl(ctx, "manuals/reflow/guide.md")
    assert out.startswith("Source path: manuals/reflow/guide.md")
    assert "245C" in out


async def test_maintainer_reads_are_capped_so_one_huge_source_cannot_blow_context():
    """#86 defense-in-depth: even with clean extracted text, a very large source
    must not flood the maintainer's context. read_source (maintainer) and
    read_new_source cap at exec_output_max_chars (the reader path already did)."""
    huge = "段落。\n" * 5000
    sources = _FakeSources({"big.md": huge})
    ctx = _ctx(wiki_sources=sources, wiki_new_source=huge, exec_output_max_chars=2_000)

    out = await read_source_impl(ctx, "big.md")
    assert len(out) < len(huge)
    assert "chars omitted" in out

    new = await read_new_source_impl(ctx)
    assert len(new) < len(huge)
    assert "chars omitted" in new


async def test_read_source_registers_a_citable_passage_in_reader_mode():
    sources = _FakeSources({"reflow-spec.md": "Zone 3 setpoint 245C."})
    ctx = _ctx(wiki_sources=sources, wiki_cite_sources=True)

    out = await read_source_impl(ctx, "reflow-spec.md")
    # Numbered like kb_search so the reader can cite [1].
    assert out.startswith("[1] reflow-spec.md:")
    assert len(ctx.context.kb_passages) == 1
    p = ctx.context.kb_passages[0]
    assert p.document_id == "doc::reflow-spec.md" and p.filename == "reflow-spec.md"

    # Re-reading the same source dedups to the same [n], no second passage.
    again = await read_source_impl(ctx, "reflow-spec.md")
    assert again.startswith("[1] ")
    assert len(ctx.context.kb_passages) == 1


async def test_read_source_reader_shows_the_full_path_not_just_basename():
    # #485: the placement location matters — two sources can share a basename in
    # different folders, so the reader must see the FULL path, not just `guide.md`.
    sources = _FakeSources({"manuals/reflow/guide.md": "Zone 3 setpoint 245C."})
    ctx = _ctx(wiki_sources=sources, wiki_cite_sources=True)

    out = await read_source_impl(ctx, "manuals/reflow/guide.md")
    assert out.startswith("[1] manuals/reflow/guide.md:")


async def test_reader_cites_the_source_it_read():
    """End-to-end through the real source seam: the reader cites back to the
    underlying SourceDoc — the [n] filename is the path, document_id its
    natural-key id."""
    from specstar.types import Binary

    from workspace_app.kb.doc_id import encode_doc_id
    from workspace_app.kb.wiki.sources import SpecstarWikiSources
    from workspace_app.resources import Collection, SourceDoc, make_spec

    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(
            collection_id=cid, path="report.md", content=Binary(data=b"BOB body"), text="BOB body"
        ),
        resource_id=encode_doc_id(cid, "report.md"),
    )

    ctx = _ctx(wiki_sources=SpecstarWikiSources(spec, cid), wiki_cite_sources=True)
    out = await read_source_impl(ctx, "report.md")
    assert out.startswith("[1] report.md:")
    assert "BOB body" in out
    (p,) = ctx.context.kb_passages
    assert p.filename == "report.md"
    assert p.document_id == encode_doc_id(cid, "report.md")
