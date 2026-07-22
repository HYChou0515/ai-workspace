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
from workspace_app.api.runner import AgentRunner
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
    runner: AgentRunner,
    holder: dict[str, str],
    *,
    superusers: frozenset[str] = frozenset(),
) -> SubagentBridge:
    cfg = AgentConfig(name="kb", model="x", allowed_tools=["kb_search"])
    return SubagentBridge(
        spec=spec,
        runner=runner,
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


async def test_bridge_filters_the_unspecified_global_path_too():
    """The unspecified fan-out — now the GLOBAL set (grill D2/D5), no longer "all" —
    is permission-filtered the same way: the speaker never reaches a global
    collection they can't read."""
    spec = make_spec()
    holder = {"id": "alice"}
    rm = spec.get_resource_manager(Collection)
    with rm.using("bob"):
        # a PRIVATE global — visible to nobody but its owner
        rm.create(
            Collection(name="secret", is_global=True, permission=Permission(visibility="private"))
        )
    public_global = _global_collection(spec, by="bob", name="open")
    runner = _CapturingRunner()
    await _bridge(spec, runner, holder).run("kb_chat", "q")  # unspecified → global set
    assert runner.seen_ids == [public_global]  # the private global is filtered out


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


class _DisclosingRunner:
    """A sub-agent that records its scopes, then simulates kb_search disclosing
    every read_meta-only (discoverable) collection into the turn accumulator."""

    def __init__(self) -> None:
        self.seen_ids: list[str] | None = None
        self.seen_discoverable: list[str] | None = None

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.seen_ids = list(ctx.collection_ids)
        self.seen_discoverable = list(ctx.discoverable_collection_ids)
        ctx.withheld_collection_ids.extend(ctx.discoverable_collection_ids)
        yield MessageDelta(text="answer")
        yield RunDone()


async def test_bridge_passes_discoverable_scope_and_bubbles_withheld_into_the_sink():
    # permission-disclosure: a read_meta-only collection is NOT searched but IS
    # handed to the sub-agent as discoverable; a disclosed match bubbles up.
    spec = make_spec()
    holder = {"id": "alice"}
    readable = _new_collection(
        spec, by="bob", permission=Permission(visibility="restricted", read_content=["user:alice"])
    )
    disc = _new_collection(
        spec,
        by="bob",
        name="Sales-2026",
        permission=Permission(visibility="restricted", read_meta=["user:alice"]),
    )
    runner = _DisclosingRunner()
    sink: list[str] = []
    answer, _ = await _bridge(spec, runner, holder).run(
        "kb_chat", "q", collection_ids=[readable, disc], withheld_sink=sink
    )
    assert runner.seen_ids == [readable]  # searched scope: readable only
    assert runner.seen_discoverable == [disc]  # read_meta-only handed over as discoverable
    assert sink == [disc]  # the disclosed withheld source bubbled to the parent
    assert answer == "answer"


async def test_bridge_probes_discoverable_collections_beyond_the_picked_scope():
    """#605 P2: the disclosure universe is every discoverable collection, not the
    picked scope. A restricted collection alice never selected (and holds no
    grant on) still reaches the sub-agent as discoverable — so "there IS an
    answer you can't read" can finally fire for a collection she didn't (or
    couldn't) think to pick."""
    spec = make_spec()
    holder = {"id": "alice"}
    readable = _new_collection(spec, by="bob")  # public → the picked, searched scope
    unpicked = _new_collection(
        spec, by="bob", name="Fab-Yield", permission=Permission(visibility="restricted")
    )
    runner = _DisclosingRunner()
    sink: list[str] = []
    await _bridge(spec, runner, holder).run(
        "kb_chat", "q", collection_ids=[readable], withheld_sink=sink
    )
    assert runner.seen_ids == [readable]  # searched scope: exactly what was picked
    assert runner.seen_discoverable == [unpicked]  # never selected, still probed
    assert sink == [unpicked]  # and it can bubble up as withheld


async def test_bridge_still_runs_to_disclose_when_nothing_is_readable():
    # readable empty but discoverable present → do NOT short-circuit; run so the
    # motivating case (the only answer is behind read_content) still discloses.
    spec = make_spec()
    holder = {"id": "alice"}
    disc = _new_collection(
        spec,
        by="bob",
        name="Secret",
        permission=Permission(visibility="restricted", read_meta=["user:alice"]),
    )
    runner = _DisclosingRunner()
    sink: list[str] = []
    answer, _ = await _bridge(spec, runner, holder).run(
        "kb_chat", "q", collection_ids=[disc], withheld_sink=sink
    )
    assert runner.seen_ids == []  # nothing readable to search
    assert sink == [disc]  # but the withheld source is still disclosed
    assert answer == "answer"


def _global_collection(spec: SpecStar, *, by: str, name: str = "g") -> str:
    """A public collection flagged is_global — always in the AI's baseline scope."""
    rm = spec.get_resource_manager(Collection)
    with rm.using(by):
        return rm.create(Collection(name=name, is_global=True)).resource_id


async def test_kb_chat_scope_unions_the_global_collections():
    # global-collection concept: a specified collection is searched TOGETHER WITH
    # the always-in-scope global set (grill D2 mode 2: specified ∪ global).
    spec = make_spec()
    holder = {"id": "alice"}
    specified = _new_collection(spec, by="alice", name="my-docs")
    g = _global_collection(spec, by="bob", name="Sales-KB")
    runner = _CapturingRunner()
    await _bridge(spec, runner, holder).run("kb_chat", "q", collection_ids=[specified])
    assert runner.seen_ids == [specified, g]  # specified first, then global


async def test_unspecified_kb_chat_scope_is_the_global_set_not_all():
    # grill D2 mode 1 / D5: unspecified ⇒ GLOBAL only — NOT every collection.
    spec = make_spec()
    holder = {"id": "alice"}
    g = _global_collection(spec, by="bob", name="HR")
    _new_collection(spec, by="bob", name="unrelated")  # public, but NOT global
    runner = _CapturingRunner()
    await _bridge(spec, runner, holder).run("kb_chat", "q")  # collection_ids omitted
    assert runner.seen_ids == [g]  # only the global one, not the unrelated public one


async def test_infer_modules_does_not_union_global():
    # infer_modules is a focused classifier over its single configured collection —
    # global is deliberately NOT added (only the KB-answer path unions global).
    spec = make_spec()
    holder = {"id": "alice"}
    specified = _new_collection(spec, by="alice", name="one")
    _global_collection(spec, by="bob", name="Sales-KB")
    cfg = AgentConfig(name="infer", model="x", allowed_tools=["kb_search"])
    runner = _CapturingRunner()
    bridge = SubagentBridge(
        spec=spec,
        runner=runner,
        retriever=Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM)),
        catalog=AgentConfigCatalog(),
        purpose_fallbacks={"infer_modules": cfg},
        get_user_id=lambda: holder["id"],
        max_searches=None,
    )
    await bridge.run("infer_modules", "q", collection_ids=[specified])
    assert runner.seen_ids == [specified]  # no global union


async def test_kb_chat_scope_drops_an_excluded_global():
    # grill D2 mode 3: global \ excluded — the effective scope removes an
    # explicitly-excluded global from the baseline.
    spec = make_spec()
    holder = {"id": "alice"}
    g1 = _global_collection(spec, by="bob", name="g1")
    g2 = _global_collection(spec, by="bob", name="g2")
    runner = _CapturingRunner()
    await _bridge(spec, runner, holder).run("kb_chat", "q", excluded_collection_ids=[g1])
    assert runner.seen_ids == [g2]  # g1 excluded from the global baseline
