"""The composed Topic Hub App (P10, manual §9–§11): a Hub seeds its memory +
collection-set files, injects the live memory each turn (never persisting it), and
exposes the retriever-free glossary / collection / KB tool ceiling scoped to the
Hub's collections.json."""

from agents import RunContextWrapper
from fastapi.testclient import TestClient
from specstar import QB

from workspace_app.agent import AgentToolContext, lookup_glossary_impl
from workspace_app.api import RunDone, create_app
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.resources import Conversation, make_spec
from workspace_app.resources.kb import Collection, ContextCard
from workspace_app.sandbox.mock import MockSandbox


class _Capture:
    """Records the prompt + ctx the turn hands the runner (no tool runs)."""

    def __init__(self) -> None:
        self.prompt: str | None = None
        self.ctx: AgentToolContext | None = None

    async def run(self, prompt, ctx):
        self.prompt = prompt
        self.ctx = ctx
        yield RunDone()


def _app(runner):
    spec = make_spec(default_user="u")
    app = create_app(spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=runner)
    return app, spec


def _hub(client: TestClient) -> tuple[str, list[str]]:
    r = client.post("/a/topic-hub/items", json={"title": "Heat treat"})
    assert r.status_code == 200
    body = r.json()
    return body["resource_id"], body["seeded"]


def _conv(spec, iid: str) -> Conversation:
    rm = spec.get_resource_manager(Conversation)
    for r in rm.list_resources(QB.all()):
        data = r.data
        if isinstance(data, Conversation) and data.item_id == iid:
            return data
    raise AssertionError(f"no conversation for {iid}")


def test_hub_picker_offers_multiple_models_so_the_composer_choice_is_real():
    """#110: a one-entry picker is a dropdown with nothing to choose. Offer the
    same Qwen/Claude/GPT trio the RCA App does so the composer's model control is
    actually usable — the Hub's own system prompt still wins (AppCatalog.resolve),
    the chosen preset only swaps the model + credentials."""
    app, _spec = _app(_Capture())
    client = TestClient(app)
    m = client.get("/apps/topic-hub").json()
    presets = [p["preset"] for p in m["agent"]["picker"]]
    assert presets == ["qwen3-local", "claude-opus", "openai-mini"]
    assert len(presets) > 1  # the whole point: more than just the local default


def test_creating_a_hub_seeds_memory_and_collection_files():
    app, _spec = _app(_Capture())
    client = TestClient(app)
    _iid, seeded = _hub(client)
    assert "/MEMORY.md" in seeded
    assert "/memory/notes.md" in seeded  # the deeper-memory dir is seeded
    assert "/collections.json" in seeded


def test_seeded_memory_is_title_substituted():
    app, _spec = _app(_Capture())
    client = TestClient(app)
    iid, _ = _hub(client)
    mem = client.get(f"/a/topic-hub/items/{iid}/files/MEMORY.md").content.decode()
    assert "Heat treat" in mem  # $title substituted at seed time


def test_hub_turn_injects_live_memory_but_keeps_the_persisted_message_clean():
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid, _ = _hub(client)
    # Edit memory AFTER seeding — the block must reflect the LIVE content.
    client.put(f"/a/topic-hub/items/{iid}/files/MEMORY.md", content=b"# Mem\nMEMTOKEN-distinct")
    client.post(f"/a/topic-hub/items/{iid}/messages", json={"content": "hello hub"})
    # The agent saw the live memory prepended to its turn content ...
    assert cap.prompt is not None
    assert "MEMTOKEN-distinct" in cap.prompt
    assert "hello hub" in cap.prompt
    # ... but the PERSISTED user message stays clean (block never enters history).
    user_msg = next(m for m in _conv(spec, iid).messages if m.role == "user")
    assert user_msg.content == "hello hub"
    assert "MEMTOKEN" not in user_msg.content


def test_hub_turn_exposes_glossary_tools_and_scopes_to_collections_json():
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid, _ = _hub(client)
    cid = spec.get_resource_manager(Collection).create(Collection(name="Defects")).resource_id
    client.put(
        f"/a/topic-hub/items/{iid}/files/collections.json",
        content=f'[{{"id": "{cid}", "name": "Defects"}}]'.encode(),
    )
    client.post(f"/a/topic-hub/items/{iid}/messages", json={"content": "hi"})
    assert cap.ctx is not None
    assert cap.ctx.spec is not None  # the retriever-free tools can query specstar
    assert cap.ctx.collection_ids == [cid]  # scoped to the Hub's collection set
    assert cap.ctx.agent_config is not None
    allowed = set(cap.ctx.agent_config.allowed_tools or [])
    assert {"lookup_glossary", "resolve_collection", "ask_knowledge_base"} <= allowed


def test_hub_glossary_layer_answers_a_card_covered_term_without_rag():
    """Retrieval layering §11: a card-covered term is answered by the deterministic
    glossary (layer 2) — no retriever / RAG — using the real turn ctx's collection scope."""
    cap = _Capture()
    app, spec = _app(cap)
    client = TestClient(app)
    iid, _ = _hub(client)
    cid = spec.get_resource_manager(Collection).create(Collection(name="Defects")).resource_id
    spec.get_resource_manager(ContextCard).create(
        ContextCard(
            collection_id=cid,
            keys=["M4"],
            norm_keys=derive_norm_keys(["M4"]),
            title="M4",
            body="Metal layer 4.",
        )
    )
    client.put(
        f"/a/topic-hub/items/{iid}/files/collections.json",
        content=f'[{{"id": "{cid}", "name": "Defects"}}]'.encode(),
    )
    client.post(f"/a/topic-hub/items/{iid}/messages", json={"content": "what is M4?"})
    assert cap.ctx is not None
    out = lookup_glossary_impl(RunContextWrapper(cap.ctx), "M4")
    assert "Metal layer 4." in out  # answered from the card, no knowledge-base search
