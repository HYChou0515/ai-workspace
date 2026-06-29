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
from workspace_app.kb.wiki.code_wiki import CodeWikiBuilder, _first_paragraph_after_h1
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


class _RoutedLlm(ILlm):
    """Returns a response chosen by a substring of the prompt (so a multi-stage
    build needn't depend on exact call order); ``default`` otherwise."""

    def __init__(self, routes: dict[str, str], default: str = "x") -> None:
        self._routes = routes
        self._default = default
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        for key, val in self._routes.items():
            if key in prompt:
                yield (val, False)
                return
        yield (self._default, False)


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


def _card_calls(llm: _RoutedLlm, path: str) -> list[str]:
    """The L0 card-summary calls issued for a specific source file."""
    return [p for p in llm.prompts if "documenting a source file" in p and f"File: {path}" in p]


def test_incremental_skips_unchanged_files_and_resummarises_changed_ones():
    spec, cid = _mk()
    _add_code(spec, cid, "a.py", "def a():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _RoutedLlm({"documenting a source file": "the card summary."})
    builder = CodeWikiBuilder(spec, llm, wiki_store=store)

    asyncio.run(builder.build(cid))
    assert len(_card_calls(llm, "a.py")) == 1  # card built once

    asyncio.run(builder.build(cid))  # nothing changed
    assert len(_card_calls(llm, "a.py")) == 1  # NOT re-summarised — skipped

    # the file's bytes change → its card IS re-summarised
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
    assert len(_card_calls(llm, "a.py")) == 2


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


def test_build_writes_a_directory_page_rolled_up_from_child_cards():
    spec, cid = _mk()
    _add_code(spec, cid, "pkg/a.py", "def a():\n    pass\n")
    _add_code(spec, cid, "pkg/b.py", "def b():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _ScriptedLlm(
        [
            "a does A.",  # L0 card for pkg/a.py (sorted first)
            "b does B.",  # L0 card for pkg/b.py
            "pkg bundles A and B.",  # L1 page for the pkg directory
        ]
    )

    asyncio.run(CodeWikiBuilder(spec, llm, wiki_store=store).build(cid))

    dirpage = asyncio.run(store.read(cid, "/dirs/pkg.md")).decode()
    assert "pkg bundles A and B." in dirpage  # the LLM roll-up
    assert "a.py" in dirpage and "b.py" in dirpage  # deterministically lists its files
    assert "a does A." in dirpage  # carries the child one-liners


def test_nested_directories_roll_up_into_parents():
    spec, cid = _mk()
    _add_code(spec, cid, "app/api/routes.py", "def route():\n    pass\n")
    _add_code(spec, cid, "app/main.py", "def main():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _ScriptedLlm(
        [
            "routes handles HTTP.",  # L0 app/api/routes.py (sorted first)
            "main is the entrypoint.",  # L0 app/main.py
            "the api sub-package.",  # L1 app/api (deepest first)
            "the app top package.",  # L1 app (parent — rolls up api + main.py)
        ]
    )

    asyncio.run(CodeWikiBuilder(spec, llm, wiki_store=store).build(cid))

    app_page = asyncio.run(store.read(cid, "/dirs/app.md")).decode()
    assert "the app top package." in app_page  # the parent's own roll-up
    assert "the api sub-package." in app_page  # carries the child directory's summary
    assert "api/" in app_page  # links the sub-package
    assert "main.py" in app_page  # lists its own files


def test_unchanged_rebuild_skips_all_upper_levels():
    # When no file changed, the (more expensive than needed) directory roll-up
    # AND the architecture/topics synthesis are skipped entirely — a routine
    # re-pull that moved nothing costs no LLM at any level.
    spec, cid = _mk()
    _add_code(spec, cid, "pkg/a.py", "def a():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _RoutedLlm({})
    builder = CodeWikiBuilder(spec, llm, wiki_store=store)

    asyncio.run(builder.build(cid))
    n1 = len(llm.prompts)
    assert n1 > 0  # the first build did real work (L0 + L1 + L2)

    asyncio.run(builder.build(cid))  # nothing changed
    assert len(llm.prompts) == n1  # no new calls at any level


def test_directory_with_only_subpackages_lists_no_files():
    spec, cid = _mk()
    _add_code(spec, cid, "app/api/x.py", "def x():\n    pass\n")
    store = WikiFileStore(spec)
    # L0 x.py; L1 deepest-first: app/api then app (which has no direct files)
    llm = _ScriptedLlm(["x summary", "api summary", "app summary"])

    asyncio.run(CodeWikiBuilder(spec, llm, wiki_store=store).build(cid))

    app_page = asyncio.run(store.read(cid, "/dirs/app.md")).decode()
    assert "## Files" not in app_page  # app has no direct files of its own
    assert "## Sub-packages" in app_page  # only its api sub-package


def test_build_writes_architecture_and_index():
    spec, cid = _mk("My Service")
    _add_code(spec, cid, "app/main.py", "def main():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _RoutedLlm(
        {
            "architecture overview": "A layered service.",
            "cross-cutting topics": "",  # propose no topics
        }
    )

    asyncio.run(CodeWikiBuilder(spec, llm, wiki_store=store).build(cid))

    arch = asyncio.run(store.read(cid, "/architecture.md")).decode()
    assert "A layered service." in arch
    index = asyncio.run(store.read(cid, "/index.md")).decode()
    assert "My Service" in index  # titled by the collection name
    assert "architecture.md" in index  # links the overview
    assert "app" in index  # links the top-level package


def test_build_writes_topic_pages_and_links_them_from_index():
    spec, cid = _mk()
    _add_code(spec, cid, "auth/login.py", "def login():\n    pass\n")
    store = WikiFileStore(spec)
    llm = _RoutedLlm(
        {
            "architecture overview": "arch",
            # blank line + a duplicate exercise the tolerant, de-duplicating parse
            "cross-cutting topics": "Authentication\n\nData Flow\n- Authentication",
            "wiki page for the topic": "the topic body.",
        }
    )

    asyncio.run(CodeWikiBuilder(spec, llm, wiki_store=store).build(cid))

    auth_topic = asyncio.run(store.read(cid, "/topics/authentication.md")).decode()
    assert "the topic body." in auth_topic
    assert "# Authentication" in auth_topic
    asyncio.run(store.read(cid, "/topics/data-flow.md"))  # slugified, exists
    index = asyncio.run(store.read(cid, "/index.md")).decode()
    assert "Authentication" in index and "topics/authentication.md" in index
    assert index.count("](/topics/") == 2  # de-duplicated to two distinct topics


def test_first_paragraph_after_h1_edge_cases():
    # stops at a ## section / code fence that immediately follows content
    assert _first_paragraph_after_h1("# h\nthe gist\n## Files\n- x") == "the gist"
    assert _first_paragraph_after_h1("# h\nthe gist\n```\ncode") == "the gist"
    # empty content under the heading → the next section never leaks in
    assert _first_paragraph_after_h1("# h\n\n## Files") == ""
    # no fence / section / trailing blank → returns the collected paragraph
    assert _first_paragraph_after_h1("# h\njust this") == "just this"
    # no heading at all → nothing
    assert _first_paragraph_after_h1("no heading here\ntext") == ""
