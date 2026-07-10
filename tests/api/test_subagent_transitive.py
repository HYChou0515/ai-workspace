"""#305 — the KB sub-agent bridge filters its collection scope to what the
current speaker can `read_content`, so `ask_knowledge_base` can't launder access
to a private / since-tightened collection on the human's behalf.
"""

from collections.abc import AsyncIterator

from specstar import SpecStar

from workspace_app.agent.ask_kb import AskKbSpec
from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext, WikiSearchBudget
from workspace_app.api.events import AgentEvent, MessageDelta, RunDone
from workspace_app.api.subagent_bridge import SubagentBridge
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.retriever import Retriever
from workspace_app.perm import Permission
from workspace_app.resources import AgentConfig, make_spec
from workspace_app.resources.kb import EMBED_DIM, Collection


class _CapturingRunner:
    """Records the collection scope the sub-agent was handed (and that it ran at
    all), then answers deterministically."""

    def __init__(self) -> None:
        self.seen_ids: list[str] | None = None
        self.seen_tools: list[str] | None = None
        self.seen_wiki_budget: int | None = None
        self.ran = False

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.ran = True
        self.seen_ids = list(ctx.collection_ids)
        assert ctx.agent_config is not None
        self.seen_tools = ctx.agent_config.allowed_tools
        self.seen_wiki_budget = ctx.wiki_search_budget.max_calls
        yield MessageDelta(text="answer")
        yield RunDone()


def _new_collection(
    spec: SpecStar, *, by: str, permission: Permission | None = None, name: str = "c"
) -> str:
    rm = spec.get_resource_manager(Collection)
    with rm.using(by):
        return rm.create(Collection(name=name, permission=permission)).resource_id


def _bridge(
    spec: SpecStar,
    runner: _CapturingRunner,
    holder: dict[str, str],
    *,
    superusers: frozenset[str] = frozenset(),
) -> SubagentBridge:
    cfg = AgentConfig(name="kb", model="x", allowed_tools=["kb_search"])
    return SubagentBridge(
        spec=spec,
        runner=runner,
        kb_runner=runner,
        retriever=Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM)),
        catalog=AgentConfigCatalog(),
        purpose_fallbacks={"kb_chat": cfg},
        get_user_id=lambda: holder["id"],
        max_searches=None,
        superusers=superusers,
    )


async def test_bridge_forwards_ask_kb_spec_and_wiki_budget_to_the_sub_agent():
    # #506: the bridge is the shared delegation point — a spec-configured
    # ask_knowledge_base rides its AskKbSpec (tool set) + wiki budget through to the
    # sub-agent's context, exactly as make_ask_knowledge_base passes them.
    spec = make_spec()
    holder = {"id": "alice"}
    cid = _new_collection(spec, by="alice")
    runner = _CapturingRunner()

    await _bridge(spec, runner, holder).run(
        "kb_chat",
        "q",
        collection_ids=[cid],
        ask_kb_spec=AskKbSpec(wiki_search_max=0, glossary=True),
        wiki_budget=WikiSearchBudget(max_calls=4),
    )

    assert runner.seen_tools == ["kb_search", "lookup_glossary"]  # spec's authoritative set
    assert runner.seen_wiki_budget == 4  # the wiki cap rode through


async def test_bridge_searches_only_collections_the_speaker_can_read():
    spec = make_spec()
    holder = {"id": "alice"}
    public = _new_collection(spec, by="bob")
    private = _new_collection(spec, by="bob", permission=Permission(visibility="private"))
    granted = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_content=["user:alice"])
    )
    runner = _CapturingRunner()
    answer, _ = await _bridge(spec, runner, holder).run(
        "kb_chat", "why voids?", collection_ids=[public, private, granted]
    )
    assert runner.seen_ids == [public, granted]  # the private collection is filtered out
    assert answer == "answer"


async def test_bridge_filters_the_search_them_all_path_too():
    """The `collection_ids is None` fan-out (search every collection) is filtered
    the same way — the speaker never reaches a collection they can't read."""
    spec = make_spec()
    holder = {"id": "alice"}
    _new_collection(spec, by="bob", permission=Permission(visibility="private"), name="secret")
    public = _new_collection(spec, by="bob", name="open")
    runner = _CapturingRunner()
    await _bridge(spec, runner, holder).run("kb_chat", "q")  # collection_ids omitted → all
    assert runner.seen_ids == [public]


async def test_bridge_returns_no_sources_when_speaker_can_read_none():
    spec = make_spec()
    holder = {"id": "carol"}
    private = _new_collection(spec, by="bob", permission=Permission(visibility="private"))
    runner = _CapturingRunner()
    answer, cites = await _bridge(spec, runner, holder).run(
        "kb_chat", "q", collection_ids=[private]
    )
    assert not runner.ran  # the sub-agent is never launched over an empty scope
    assert "No accessible knowledge sources" in answer
    assert cites == []


async def test_bridge_lets_a_superuser_speaker_read_all():
    spec = make_spec(superusers=frozenset({"root"}))
    holder = {"id": "root"}
    private = _new_collection(spec, by="bob", permission=Permission(visibility="private"))
    runner = _CapturingRunner()
    await _bridge(spec, runner, holder, superusers=frozenset({"root"})).run(
        "kb_chat", "q", collection_ids=[private]
    )
    assert runner.seen_ids == [private]
