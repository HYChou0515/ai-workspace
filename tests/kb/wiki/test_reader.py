"""answer_from_wiki (#50 P4) — the wiki reader navigates the wiki and cites
back to the underlying source documents.

A scripted runner stands in for the reader LLM: it reads a wiki page, reads the
cited source (which registers a citable passage), then answers citing [1]. The
test proves the wiring resolves [1] back to the real SourceDoc.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.tools import read_file_impl, read_source_impl
from workspace_app.api.events import MessageDelta, RunError
from workspace_app.kb.wiki.reader import answer_from_wiki, default_wiki_reader_config
from workspace_app.kb.wiki.sources import IWikiSources, WikiSourceRef
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, make_spec


class _FakeSources(IWikiSources):
    def __init__(self, cid, docs):  # docs: path -> (doc_id, text)
        self._cid = cid
        self._docs = docs

    def list(self):
        return sorted(self._docs)

    def read(self, path):
        d = self._docs.get(path)
        return d[1] if d else None

    def ref(self, path):
        d = self._docs.get(path)
        return (
            WikiSourceRef(document_id=d[0], collection_id=self._cid, path=path, text=d[1])
            if d
            else None
        )

    def ref_by_id(self, doc_id):
        for path, (did, _text) in self._docs.items():
            if did == doc_id:
                return self.ref(path)
        return None


class _ReadingRunner:
    """Reads the index page + the cited source, then answers citing [1]."""

    async def run(self, question, ctx):
        wrapped = RunContextWrapper(ctx)
        await read_file_impl(wrapped, "/index.md")  # navigate
        await read_source_impl(wrapped, "reflow-spec.md")  # registers [1]
        yield MessageDelta(text="Zone 3 setpoint is 245C [1].")


class _FailingRunner:
    async def run(self, question, ctx):
        yield RunError(message="model unavailable")


async def _seed(spec, cid):
    store = WikiFileStore(spec)
    await store.write(cid, "/index.md", b"# Reflow\n\nSee [[reflow-zone-3]].\n")
    return store


async def test_reader_answers_and_cites_back_to_the_source_doc():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    store = await _seed(spec, cid)
    sources = _FakeSources(cid, {"reflow-spec.md": ("doc-123", "Zone 3 setpoint 245C.")})

    seen = []
    answer, cites = await answer_from_wiki(
        _ReadingRunner(),
        wiki_store=store,
        wiki_sources=sources,
        collection_id=cid,
        question="What is the zone 3 setpoint?",
        agent_config=default_wiki_reader_config(),
        on_event=seen.append,  # relay every reader event to the caller
    )

    assert seen  # the navigation was relayed
    assert "245C" in answer
    assert len(cites) == 1
    c = cites[0]
    assert c.marker == 1
    assert c.document_id == "doc-123"  # cites the SourceDoc, not the wiki page
    assert c.filename == "reflow-spec.md"
    assert "245C" in c.snippet


async def test_reader_run_error_surfaces_and_yields_no_citations():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    store = await _seed(spec, cid)

    answer, cites = await answer_from_wiki(
        _FailingRunner(),
        wiki_store=store,
        wiki_sources=_FakeSources(cid, {}),
        collection_id=cid,
        question="anything",
        agent_config=default_wiki_reader_config(),
    )
    assert "model unavailable" in answer
    assert cites == []


def test_reader_config_is_read_only():
    cfg = default_wiki_reader_config()
    allowed = set(cfg.allowed_tools or [])
    assert {"search_wiki", "read_source", "read_file", "ls", "list_sources"} <= allowed
    assert not ({"write_file", "edit_file", "delete_file"} & allowed)
