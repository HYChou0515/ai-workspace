"""Issue #281: CodeWikiBuilder turns a code collection's SourceDocs into a
hierarchical wiki — L0 per-file cards (deterministic outline + an LLM one-liner),
L1 directory pages rolled up from those cards, L2 architecture/index/topics
synthesised from the directory summaries. Every LLM call is a single ``collect``
(a fixed-material → one-page step, not an agent loop), so the build is a
predictable pipeline: the program writes the pages.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

from specstar.types import Binary

from workspace_app.kb.doc_id import encode_doc_id
from workspace_app.kb.llm import ILlm
from workspace_app.kb.wiki.code_wiki import CodeWikiBuilder
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, SourceDoc, make_spec


class _ScriptedLlm(ILlm):
    """One queued response per ``stream`` call (FIFO); records prompts so a test
    can assert call count + that incremental runs skip the LLM."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        yield (self._responses.pop(0) if self._responses else "a one-line summary.", False)


def _add_code(spec, cid: str, path: str, src: str) -> None:
    spec.get_resource_manager(SourceDoc).create(
        SourceDoc(collection_id=cid, path=path, content=Binary(data=src.encode()), text=src),
        resource_id=encode_doc_id(cid, path),
    )


def _mk(name: str = "c"):
    spec = make_spec(default_user="u")
    cid = (
        spec.get_resource_manager(Collection).create(Collection(name=name, git_url="x")).resource_id
    )
    return spec, cid


def test_build_writes_a_file_card_per_source():
    spec, cid = _mk()
    _add_code(spec, cid, "pkg/mod.py", "def foo():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _ScriptedLlm(["mod.py defines foo."])

    asyncio.run(CodeWikiBuilder(spec, llm, wiki_store=store).build(cid))

    card = asyncio.run(store.read(cid, "/files/pkg/mod.py.md")).decode()
    assert "def foo" in card  # the deterministic tree-sitter outline
    assert "mod.py defines foo." in card  # the LLM one-liner


def test_incremental_skips_unchanged_files_and_resummarises_changed_ones():
    spec, cid = _mk()
    _add_code(spec, cid, "a.py", "def a():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _ScriptedLlm(["A summary", "A summary v2"])
    builder = CodeWikiBuilder(spec, llm, wiki_store=store)

    asyncio.run(builder.build(cid))
    assert len(llm.prompts) == 1  # one card written

    # nothing changed → re-build makes NO LLM call (skips the unchanged file)
    asyncio.run(builder.build(cid))
    assert len(llm.prompts) == 1

    # the file's bytes change → its card is re-summarised
    spec.get_resource_manager(SourceDoc).update(
        encode_doc_id(cid, "a.py"),
        SourceDoc(
            collection_id=cid,
            path="a.py",
            content=Binary(data=b"def a():\n    return 2\n"),
            text="def a():\n    return 2\n",
        ),
    )
    asyncio.run(builder.build(cid))
    assert len(llm.prompts) == 2
    assert "A summary v2" in asyncio.run(store.read(cid, "/files/a.py.md")).decode()


def test_non_code_file_gets_a_card_without_an_outline_fence():
    # The repo's prose (README, docs) is good wiki material too — it gets a
    # card with the LLM one-liner but no tree-sitter skeleton.
    spec, cid = _mk()
    _add_code(spec, cid, "README.md", "# My Project\n\nDoes things.\n")
    store = WikiFileStore(spec)
    asyncio.run(
        CodeWikiBuilder(spec, _ScriptedLlm(["the project readme."]), wiki_store=store).build(cid)
    )

    card = asyncio.run(store.read(cid, "/files/README.md.md")).decode()
    assert "the project readme." in card
    assert "```" not in card  # no code fence — there's no outline for prose
