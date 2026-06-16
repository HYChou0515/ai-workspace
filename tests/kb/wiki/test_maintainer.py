"""run_wiki_maintainer (#50 P2) — wires a sandbox-free wiki context so the
maintainer agent edits the collection's wiki via the file tools.

The agent itself is the runner (real LLM in prod); here a scripted runner
stands in for it and exercises the tools, proving the wiring end-to-end:
the context routes file ops to the WikiFileStore, seeds WIKI.md, and
exposes read_new_source / read_source.
"""

from __future__ import annotations

from agents import RunContextWrapper

from workspace_app.agent.tools import (
    read_new_source_impl,
    read_source_impl,
    write_file_impl,
)
from workspace_app.kb.wiki.maintainer import default_wiki_maintainer_config, run_wiki_maintainer
from workspace_app.kb.wiki.sources import IWikiSources
from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, make_spec


class _FakeSources(IWikiSources):
    def __init__(self, docs):
        self._docs = docs

    def list(self):
        return sorted(self._docs)

    def read(self, path):
        return self._docs.get(path)

    def ref(self, path):
        return None  # maintainer doesn't cite

    def ref_by_id(self, doc_id):
        return None  # maintainer fake reads new_source directly, never by id


class _ToolDrivingRunner:
    """Stands in for the LLM: reads the new source + writes a wiki page,
    exactly as the real maintainer agent would via its tools."""

    async def run(self, prompt, ctx):
        wrapped = RunContextWrapper(ctx)
        new = await read_new_source_impl(wrapped)
        src = await read_source_impl(wrapped, "reflow-spec.md")
        await write_file_impl(
            wrapped,
            "/entities/reflow.md",
            f"# Reflow\n\n{new}\nfrom spec: {src}\n\nSources: reflow-spec.md\n",
        )
        if False:
            yield  # pragma: no cover — make this an async generator


async def test_maintainer_run_seeds_schema_and_writes_through_to_the_wiki_store():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    store = WikiFileStore(spec)
    sources = _FakeSources({"reflow-spec.md": "Zone 3 setpoint 245C."})

    await run_wiki_maintainer(
        _ToolDrivingRunner(),
        wiki_store=store,
        wiki_sources=sources,
        collection_id=cid,
        new_source="The reflow oven spec, revision C.",
        agent_config=default_wiki_maintainer_config(),
    )

    # WIKI.md (the schema) was seeded into the workspace root.
    assert b"knowledge wiki" in await store.read(cid, "/WIKI.md")
    # The agent's page landed in the wiki store, durably.
    page = (await store.read(cid, "/entities/reflow.md")).decode()
    assert "revision C" in page and "245C" in page and "Sources:" in page


def test_default_maintainer_config_grants_the_wiki_toolset():
    cfg = default_wiki_maintainer_config()
    assert cfg.allowed_tools is not None
    assert {"search_wiki", "read_new_source", "read_source", "write_file", "edit_file"} <= set(
        cfg.allowed_tools
    )
    assert "knowledge wiki" in cfg.system_prompt.lower()


async def test_maintainer_does_not_reseed_an_existing_wiki_md():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    store = WikiFileStore(spec)
    await store.write(cid, "/WIKI.md", b"my custom conventions")

    class _Noop:
        async def run(self, prompt, ctx):
            if False:
                yield

    await run_wiki_maintainer(
        _Noop(),
        wiki_store=store,
        wiki_sources=_FakeSources({}),
        collection_id=cid,
        new_source="x",
        agent_config=default_wiki_maintainer_config(),
    )
    # The operator/agent-customised WIKI.md is preserved, not overwritten.
    assert await store.read(cid, "/WIKI.md") == b"my custom conventions"


async def test_maintainer_passes_the_configured_turn_budget_to_the_context():
    """The maintenance pass writes several pages, so it must run with a generous
    step budget (settings.kb.wiki.maintainer_max_turns) — not a chat reply's
    ~10 turns, which exhausts mid-read and writes nothing."""
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    store = WikiFileStore(spec)
    captured: dict[str, int | None] = {}

    class _Capture:
        async def run(self, prompt, ctx):
            captured["max_turns"] = ctx.max_turns
            if False:
                yield

    await run_wiki_maintainer(
        _Capture(),
        wiki_store=store,
        wiki_sources=_FakeSources({}),
        collection_id=cid,
        new_source="x",
        agent_config=default_wiki_maintainer_config(),
        max_turns=99,
    )
    assert captured["max_turns"] == 99
