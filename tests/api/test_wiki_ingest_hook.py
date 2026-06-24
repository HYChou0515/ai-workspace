"""#50 P3 — the upload→wiki ingest hook, end to end through the app.

Uploading a document to a ``use_wiki`` collection should, after indexing,
drive the wiki maintainer over that collection's wiki. The maintainer runs in
the coordinator's background worker; the test drains it via the coordinator
exposed on ``app.state`` and then asserts the wiki pages exist. A collection
without ``use_wiki`` must not build a wiki.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agents import RunContextWrapper
from httpx import ASGITransport

from workspace_app.agent.context import AgentToolContext
from workspace_app.agent.tools import read_new_source_impl, write_file_impl
from workspace_app.api import create_app
from workspace_app.api.events import AgentEvent, RunDone
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import make_spec
from workspace_app.resources.kb import EMBED_DIM
from workspace_app.sandbox.mock import MockSandbox

from ._client import AsyncClient


class _WikiWritingRunner:
    """When handed a wiki-maintenance context (wiki_new_source set), writes a
    page from the new source — otherwise yields nothing. Stands in for the
    maintainer LLM."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if ctx.wiki_new_source is not None:
            wrapped = RunContextWrapper(ctx)
            new = await read_new_source_impl(wrapped)
            await write_file_impl(wrapped, "/entities/note.md", f"{new}\n\nSources: note.md\n")
        yield RunDone()


def _app(spec, runner):
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
    )


async def test_upload_to_wiki_collection_builds_the_wiki():
    spec = make_spec(default_user="u")
    app = _app(spec, _WikiWritingRunner())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/collections", json={"name": "c", "use_wiki": True})).json()[
            "resource_id"
        ]
        r = await c.post(
            f"/kb/collections/{cid}/documents",
            files={"file": ("note.md", b"Reflow zone 3 runs at 245C.", "text/markdown")},
        )
        assert r.status_code == 200

    # #82: indexing runs on the index queue — drain it (runs index → the
    # index→wiki hook enqueues the maintenance job), THEN drain the wiki.
    await app.state.index_coordinator.aclose()
    await app.state.wiki_coordinator.aclose()

    store = WikiFileStore(spec)
    assert await store.exists(cid, "/WIKI.md")
    body = (await store.read(cid, "/entities/note.md")).decode()
    assert "245C" in body


async def test_upload_to_non_wiki_collection_builds_no_wiki():
    spec = make_spec(default_user="u")
    app = _app(spec, _WikiWritingRunner())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        cid = (await c.post("/kb/collections", json={"name": "c", "use_wiki": False})).json()[
            "resource_id"
        ]
        r = await c.post(
            f"/kb/collections/{cid}/documents",
            files={"file": ("note.md", b"Reflow zone 3 runs at 245C.", "text/markdown")},
        )
        assert r.status_code == 200

    await app.state.index_coordinator.aclose()  # index runs; use_wiki off ⇒ no wiki job
    await app.state.wiki_coordinator.aclose()
    assert await WikiFileStore(spec).ls(cid) == []


def test_wiki_model_override_applies_to_the_wiki_agents():
    """The wiki agents' model/endpoint (resolved from `kb.wiki.llm` in
    __main__, passed to create_app as `wiki_model`) points them at a
    stronger tool-calling model without restating their prompts/tools."""
    spec = make_spec(default_user="u")
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_WikiWritingRunner(),
        get_user_id=lambda: "u",
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=8, overlap_tokens=2),
        wiki_model="openai/gpt-4o-mini",
        wiki_llm_base_url="https://api.example/v1",
    )
    cfg = app.state.wiki_coordinator._agent_config
    assert cfg.model == "openai/gpt-4o-mini"
    assert cfg.llm_base_url == "https://api.example/v1"
    # The maintainer prompt + tools are untouched by the model override.
    assert "knowledge wiki" in cfg.system_prompt.lower()
    assert "write_file" in (cfg.allowed_tools or [])
