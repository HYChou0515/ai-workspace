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

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import (
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
    # grep-style path:line: hits, across pages, case-insensitive
    assert "/entities/reflow.md:1:" in out
    assert "Reflow zone 3" in out
    # the index link line matches too
    assert "/index.md" in out


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
