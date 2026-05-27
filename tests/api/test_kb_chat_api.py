from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from specstar import SpecStar

from workspace_app.agent.context import AgentToolContext
from workspace_app.api import create_app
from workspace_app.api.events import (
    AgentEvent,
    AgentMetrics,
    MessageDelta,
    RunDone,
    ToolEnd,
    ToolLog,
    ToolStart,
)
from workspace_app.api.kb_chat_routes import answer_question
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.chunker import FixedTokenChunker
from workspace_app.kb.embedder import HashEmbedder
from workspace_app.kb.retriever import Retriever
from workspace_app.resources import register_all
from workspace_app.resources.kb import EMBED_DIM, RetrievedPassage
from workspace_app.sandbox.mock import MockSandbox


def _reflow_passage() -> RetrievedPassage:
    return RetrievedPassage(
        collection_id="c",
        document_id="c/u/reflow.md",
        filename="reflow.md",
        start=0,
        end=16,
        source_chunk_ids=["c/u/reflow.md#0"],
        text="zone three drift",
    )


class _KbRunner:
    """Stands in for the KB agent: 'runs' kb_search (filling the per-turn
    passage registry on the context) then answers citing [1]."""

    def __init__(self) -> None:
        self.seen_collections: list[str] | None = None

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.seen_collections = list(ctx.collection_ids)
        ctx.kb_passages.append(_reflow_passage())
        yield MessageDelta(text="searching the kb", reasoning=True)  # <think> channel
        yield MessageDelta(text="Zone three drifted ")
        yield MessageDelta(text="[1].")
        yield RunDone()


class _BoomRunner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        raise RuntimeError("model exploded")
        yield  # pragma: no cover — unreachable, makes this an async generator


class _ToolRunner:
    """Emits a kb_search tool call, then an answer — so persistence of the
    tool message + the assistant-after-tool continuation are exercised."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        ctx.kb_passages.append(_reflow_passage())
        yield MessageDelta(text="Let me check. ")
        yield ToolStart(call_id="t1", name="kb_search", args={"query": "reflow"})
        yield ToolEnd(call_id="t1", output="[1] reflow.md: zone three drift")
        yield MessageDelta(text="Zone three drifted [1].")
        yield RunDone()


class _MetricsRunner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="Answer.")
        yield AgentMetrics(phase="final", prompt_tokens=42, completion_tokens=7, elapsed_ms=1234)
        yield RunDone()


def test_send_message_persists_final_token_metrics():
    client = _client(_MetricsRunner())
    cid = client.post("/kb/chats", json={"title": "t", "collection_ids": []}).json()["resource_id"]
    client.post(f"/kb/chats/{cid}/messages", json={"content": "hi"})

    answer = client.get(f"/kb/chats/{cid}").json()["messages"][-1]
    assert answer["role"] == "assistant"
    # the live token line survives a reload (persisted on the assistant message)
    assert answer["metrics"] == {"prompt_tokens": 42, "completion_tokens": 7, "elapsed_ms": 1234}


class _HistoryRecordingRunner:
    """Records the history the engine handed it, and answers deterministically."""

    def __init__(self) -> None:
        self.seen_history: list[list[dict[str, str]]] = []

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        self.seen_history.append(list(ctx.history))
        yield MessageDelta(text=f"answer to {prompt}")
        yield RunDone()


def test_agent_sees_prior_turns_as_history():
    runner = _HistoryRecordingRunner()
    client = _client(runner)
    cid = client.post("/kb/chats", json={"title": "t", "collection_ids": []}).json()["resource_id"]

    client.post(f"/kb/chats/{cid}/messages", json={"content": "q1"})
    client.post(f"/kb/chats/{cid}/messages", json={"content": "q2"})

    # turn 1 had no history; turn 2 replays turn 1's user + assistant dialogue
    assert runner.seen_history[0] == []
    assert runner.seen_history[1] == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "answer to q1"},
    ]


class _OrphanToolRunner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield ToolEnd(call_id="ghost", output="stray output")
        yield RunDone()


class _DualRunner:
    """One runner that plays both sides of the RCA → KB bridge: a KB turn
    (retriever set) answers from the registry; an RCA turn (no retriever) calls
    the ask_knowledge_base bridge and relays what the KB said."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if ctx.retriever is not None:  # KB turn
            ctx.kb_passages.append(_reflow_passage())
            yield MessageDelta(text="Zone three drifted [1].")
            yield RunDone()
        else:  # RCA turn — consult the KB as a tool
            assert ctx.ask_kb is not None
            answer = await ctx.ask_kb(prompt, ctx.on_exec_output, ctx.investigation_id)
            yield MessageDelta(text=f"KB says: {answer}")
            yield RunDone()


def _client(runner: object) -> TestClient:
    spec = SpecStar()
    spec.configure(default_user="default-user", default_now=lambda: datetime.now(UTC))
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=runner,  # ty: ignore[invalid-argument-type]
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
    )
    return TestClient(app)


def test_kb_agent_config_exposes_suggestions():
    client = _client(_KbRunner())
    body = client.get("/kb/agent").json()
    assert body["name"] == "KB Agent"
    assert isinstance(body["suggestions"], list) and body["suggestions"]


def test_create_list_get_delete_chat():
    client = _client(_KbRunner())
    cid = client.post("/kb/chats", json={"title": "Reflow", "collection_ids": ["c"]}).json()[
        "resource_id"
    ]

    listed = client.get("/kb/chats").json()
    match = next(c for c in listed if c["resource_id"] == cid)
    assert match["title"] == "Reflow"
    assert match["collection_ids"] == ["c"]

    got = client.get(f"/kb/chats/{cid}").json()
    assert got["messages"] == []

    assert client.delete(f"/kb/chats/{cid}").status_code == 204
    assert client.get(f"/kb/chats/{cid}").status_code == 404


def test_send_message_streams_and_persists_answer_with_citations():
    runner = _KbRunner()
    client = _client(runner)
    cid = client.post("/kb/chats", json={"title": "t", "collection_ids": ["c"]}).json()[
        "resource_id"
    ]

    r = client.post(f"/kb/chats/{cid}/messages", json={"content": "why voids?"})
    assert r.status_code == 200
    body = r.text
    assert "message_delta" in body and "done" in body  # streamed live
    assert runner.seen_collections == ["c"]  # the thread's collections drove retrieval

    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "why voids?"
    answer = msgs[1]
    assert answer["content"] == "Zone three drifted [1]."
    assert answer["reasoning"] == "searching the kb"  # <think> kept separate from content
    # [1] resolved against the registry the run populated
    assert len(answer["citations"]) == 1
    cite = answer["citations"][0]
    assert cite["marker"] == 1
    assert cite["document_id"] == "c/u/reflow.md"
    assert cite["filename"] == "reflow.md"


def test_run_error_is_streamed_and_nothing_persisted():
    client = _client(_BoomRunner())
    cid = client.post("/kb/chats", json={"collection_ids": ["c"]}).json()["resource_id"]

    r = client.post(f"/kb/chats/{cid}/messages", json={"content": "boom?"})
    assert r.status_code == 200
    assert "error" in r.text  # the failure surfaces as a terminal SSE event

    # only the user's message persisted — the failed turn produced no answer
    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]


def test_send_message_persists_tool_calls_then_the_answer():
    client = _client(_ToolRunner())
    cid = client.post("/kb/chats", json={"collection_ids": ["c"]}).json()["resource_id"]

    client.post(f"/kb/chats/{cid}/messages", json={"content": "why voids?"})

    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    tool = msgs[2]
    assert tool["tool_name"] == "kb_search"
    assert tool["tool_args"] == {"query": "reflow"}
    # the answer after the tool is its own message, and it's the one that's cited
    answer = msgs[3]
    assert answer["content"] == "Zone three drifted [1]."
    assert len(answer["citations"]) == 1
    assert tool["citations"] == []  # tool output isn't citation-parsed


def test_orphan_tool_end_persists_with_null_name_args():
    client = _client(_OrphanToolRunner())
    cid = client.post("/kb/chats", json={"collection_ids": []}).json()["resource_id"]

    client.post(f"/kb/chats/{cid}/messages", json={"content": "q"})

    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    tool = next(m for m in msgs if m["role"] == "tool")
    assert tool["content"] == "stray output"
    assert tool["tool_name"] is None and tool["tool_args"] is None


async def test_answer_question_returns_synthesized_answer_with_sources_footer():
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    register_all(spec)
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))

    answer = await answer_question(_KbRunner(), retriever, ["c"], "why voids?")

    assert "Zone three drifted [1]." in answer  # visible content only (no <think>)
    assert "Sources: [1] reflow.md" in answer  # cited source appended


class _PlainRunner:
    """A KB turn that cites nothing — answers without searching."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="I don't see that in the knowledge base.")
        yield RunDone()


async def test_answer_question_without_citations_has_no_footer():
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    register_all(spec)
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))

    answer = await answer_question(_PlainRunner(), retriever, ["c"], "off-topic?")

    assert answer == "I don't see that in the knowledge base."  # no Sources footer


def test_rca_agent_consults_kb_through_ask_knowledge_base():
    # RCA turn calls the bridge, which runs the KB agent over all collections.
    client = _client(_DualRunner())
    client.post("/kb/collections", json={"name": "kb"})  # a collection to search

    r = client.post("/investigations/ws-kb/messages", json={"content": "consult the kb"})

    assert r.status_code == 200
    body = r.text.lower()
    assert "zone three drifted" in body  # the KB agent's answer reached RCA
    assert "sources:" in body  # carried through with its citation footer


# ── #4: stream the KB sub-agent's intermediate state ──────────────────────────


def test_kb_progress_surfaces_searches_and_reasoning_only():
    from workspace_app.api.kb_chat_routes import kb_progress

    assert (
        kb_progress(ToolStart(call_id="a", name="kb_search", args={"query": "voids"}))
        == "🔎 kb_search: voids\n"
    )
    assert kb_progress(ToolStart(call_id="b", name="kb_search", args={})) == "🔎 kb_search\n"
    assert kb_progress(MessageDelta(text="weighing it", reasoning=True)) == "weighing it"
    # kb_search's live output (e.g. the retriever's enhancement-LLM thinking) is relayed too
    assert kb_progress(ToolLog(call_id="a", text="↻ rerank\n")) == "↻ rerank\n"
    assert kb_progress(MessageDelta(text="the answer")) is None  # content isn't progress
    assert kb_progress(ToolEnd(call_id="a", output="x")) is None
    assert kb_progress(RunDone()) is None


async def test_answer_question_forwards_every_event_to_on_event():
    spec = SpecStar()
    spec.configure(default_user="u", default_now=lambda: datetime.now(UTC))
    register_all(spec)
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))

    seen: list[AgentEvent] = []
    await answer_question(_KbRunner(), retriever, ["c"], "why voids?", on_event=seen.append)

    assert any(isinstance(e, MessageDelta) and e.reasoning for e in seen)  # reasoning seen
    assert any(isinstance(e, RunDone) for e in seen)  # ran to completion


class _StreamingDualRunner:
    """RCA turn sets its output sink (like the real runner does) and consults
    the KB; the KB turn emits a search + reasoning so we can assert the bridge
    relays that intermediate state into the RCA run's sink."""

    def __init__(self) -> None:
        self.relayed: list[str] = []

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        if ctx.retriever is not None:  # KB turn
            ctx.kb_passages.append(_reflow_passage())
            yield ToolStart(call_id="s1", name="kb_search", args={"query": "voids"})
            yield ToolEnd(call_id="s1", output="[1] reflow.md: zone three drift")
            yield MessageDelta(text="weighing the evidence", reasoning=True)
            yield MessageDelta(text="Zone three drifted [1].")
            yield RunDone()
        else:  # RCA turn
            assert ctx.ask_kb is not None
            ctx.on_exec_output = lambda b: self.relayed.append(b.decode())
            answer = await ctx.ask_kb(prompt, ctx.on_exec_output, ctx.investigation_id)
            yield MessageDelta(text=f"KB says: {answer}")
            yield RunDone()


def test_ask_knowledge_base_relays_kb_progress_to_the_run_sink():
    runner = _StreamingDualRunner()
    client = _client(runner)
    client.post("/kb/collections", json={"name": "kb"})

    r = client.post("/investigations/ws-kb/messages", json={"content": "consult the kb"})
    assert r.status_code == 200
    _ = r.text  # drain the stream so the turn (and the bridge) runs

    relayed = "".join(runner.relayed)
    assert "🔎 kb_search: voids" in relayed  # the KB agent's search surfaced live
    assert "weighing the evidence" in relayed  # its reasoning surfaced live


def test_kb_chat_streams_tool_and_reasoning_before_the_answer():
    # #4 Part B: the KB chat SSE carries the agent's intermediate events
    # (tool calls + reasoning) live, not just the final answer.
    client = _client(_ToolRunner())
    cid = client.post("/kb/chats", json={"collection_ids": ["c"]}).json()["resource_id"]

    r = client.post(f"/kb/chats/{cid}/messages", json={"content": "why voids?"})
    assert r.status_code == 200
    body = r.text
    assert "tool_start" in body  # kb_search call streamed live
    assert "tool_end" in body
    assert "message_delta" in body  # answer streamed live
    assert body.index("tool_start") < body.rindex("message_delta")  # tool before final answer
