"""Issue #281 follow-up P2 (A2): the wiki READER must work over a CODE wiki,
not just a prose one. The reader is generic, but #281 ships a new page structure
(``/files/<path>.md`` skeleton cards, ``/dirs`` roll-ups, ``/architecture.md``)
that was never exercised end-to-end. These tests build a real code wiki via the
CodeWikiBuilder, then drive the reader over it to prove:

  - ``search_wiki`` finds code-wiki pages (direct grep over the store — no vector
    index needed), including the tree-sitter symbol skeleton, so a question can
    locate the right page; and
  - ``read_source`` cites back to the original ``.py`` SourceDoc (document-level
    citation), so an answer about the code links to the file it came from.

A scripted runner stands in for the reader LLM (deterministic); the live model
behaviour is covered by the #281 live check, not here.
"""

from __future__ import annotations

from collections.abc import Iterator

from agents import RunContextWrapper
from specstar.types import Binary

from workspace_app.agent.tools import read_source_impl, search_wiki_impl
from workspace_app.api.events import MessageDelta
from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.coordinator import WikiMaintenanceCoordinator
from workspace_app.kb.wiki.reader import answer_from_wiki, default_wiki_reader_config
from workspace_app.kb.wiki.sources import SpecstarWikiSources
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, SourceDoc, make_spec

_QUEUE_PY = "class TaskQueue:\n    def push(self, task):\n        self._q.append(task)\n"


class _Llm(ILlm):
    """A stand-in summariser — page CONTENT quality is the live check's job; here
    we only need the builder to emit real pages so the reader can grep + cite."""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield ("a one-line summary.", False)


class _NoopRunner:
    async def run(self, prompt, ctx):  # the code build path never uses the runner
        if False:  # pragma: no cover
            yield


async def _build_code_wiki(spec, cid: str) -> str:
    doc_id = encode_doc_id(cid, "app/queue.py")
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(
            collection_id=cid,
            path="app/queue.py",
            content=Binary(data=_QUEUE_PY.encode()),
            text=_QUEUE_PY,
            status="ready",
        ),
        resource_id=doc_id,
    )
    coord = WikiMaintenanceCoordinator(spec, _NoopRunner(), code_wiki_llm=_Llm())
    await coord.on_doc_indexed(doc_id)
    await coord.aclose()  # the consumer runs the hierarchical build
    return doc_id


async def test_reader_searches_and_cites_a_built_code_wiki():
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection)
        .create(Collection(name="repo", git_url="https://git.example/r.git", use_wiki=True))
        .resource_id
    )
    doc_id = await _build_code_wiki(spec, cid)

    captured: dict[str, str] = {}

    class _CodeReadingRunner:
        """Greps the wiki for the class, reads its source file (registers [1]),
        then answers citing [1] — the realistic reader flow over a code wiki."""

        async def run(self, question, ctx):
            wrapped = RunContextWrapper(ctx)
            captured["hits"] = await search_wiki_impl(wrapped, "TaskQueue")
            await read_source_impl(wrapped, "app/queue.py")
            yield MessageDelta(text="TaskQueue is defined in app/queue.py [1].")

    answer, cites = await answer_from_wiki(
        _CodeReadingRunner(),  # ty: ignore[invalid-argument-type]
        wiki_store=WikiFileStore(spec),
        wiki_sources=SpecstarWikiSources(spec, cid),
        collection_id=cid,
        question="Where is TaskQueue defined?",
        agent_config=default_wiki_reader_config(),
    )

    # search_wiki located the code-wiki page AND the tree-sitter symbol skeleton —
    # i.e. code symbols are greppable in the built wiki, no vector index needed.
    assert "TaskQueue" in captured["hits"]
    assert "queue.py" in captured["hits"]
    # …and the answer cites the original .py SourceDoc (document-level), not the
    # wiki page — so the FE reference card links to the source file.
    assert "app/queue.py" in answer
    assert len(cites) == 1
    assert cites[0].document_id == doc_id
    assert cites[0].filename == "queue.py"
