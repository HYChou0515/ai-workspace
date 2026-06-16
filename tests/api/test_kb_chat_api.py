from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

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
from workspace_app.resources import AgentConfig, make_spec
from workspace_app.resources.kb import EMBED_DIM, RetrievedPassage
from workspace_app.sandbox.mock import MockSandbox


def _test_kb_cfg() -> AgentConfig:
    """Minimal AgentConfig for the KB sub-agent in answer_question tests —
    just kb_search as allowed_tools. The scripted runners ignore model /
    system_prompt, so neither matters here."""
    return AgentConfig(name="kb", model="x", allowed_tools=["kb_search"])


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
            assert ctx.run_subagent is not None
            answer, _ = await ctx.run_subagent(
                "kb_chat", prompt, ctx.on_exec_output, ctx.investigation_id
            )
            yield MessageDelta(text=f"KB says: {answer}")
            yield RunDone()


def _client(runner: object) -> TestClient:
    spec = make_spec()
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
    """Issue #32: /kb/agent is now an ARRAY of {name, model, suggestions}
    — the FE picker iterates it. The first entry is the visible
    default."""
    client = _client(_KbRunner())
    body = client.get("/kb/agent").json()
    assert isinstance(body, list) and body
    first = body[0]
    # Issue #32 follow-up: bundled kb_chat now ships 3 entries (local
    # + Claude + GPT) with explicit picker labels so the FE shows real
    # choices on first run. Earlier the bundle had a single entry that
    # fell back to the default_name "KB Agent".
    assert first["name"] == "KB · Qwen3 (local)"
    assert isinstance(first["suggestions"], list) and first["suggestions"]
    assert isinstance(first["model"], str) and first["model"]
    # Composer model picker (handoff redesign): each entry's blurb rides
    # along so the popover can render a note under the name.
    assert isinstance(first["description"], str) and first["description"]


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


def test_run_error_is_streamed_and_persisted_as_an_error_message():
    """#37 — a failed turn used to vanish (only the user msg persisted),
    making it undebuggable. Now the failure is kept as a `role="error"`
    message so a reloaded thread shows it."""
    client = _client(_BoomRunner())
    cid = client.post("/kb/chats", json={"collection_ids": ["c"]}).json()["resource_id"]

    r = client.post(f"/kb/chats/{cid}/messages", json={"content": "boom?"})
    assert r.status_code == 200
    assert "error" in r.text  # the failure surfaces as a terminal SSE event

    msgs = client.get(f"/kb/chats/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "error"]
    assert msgs[-1]["error_kind"] == "error"


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
    spec = make_spec(default_user="u")
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))

    answer = await answer_question(
        _KbRunner(), retriever, ["c"], "why voids?", agent_config=_test_kb_cfg()
    )

    assert "Zone three drifted [1]." in answer  # visible content only (no <think>)
    assert "Sources: [1] reflow.md" in answer  # cited source appended


class _PlainRunner:
    """A KB turn that cites nothing — answers without searching."""

    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield MessageDelta(text="I don't see that in the knowledge base.")
        yield RunDone()


async def test_answer_question_surfaces_kb_sub_agent_tool_errors_instead_of_synthesized_refusal(
    caplog,
):
    """When the KB sub-agent's kb_search call(s) all return SDK-wrapped
    error strings, the LLM downstream tends to synthesize a polite
    "I can't access the KB" recovery sentence. That sentence is what
    `answer_question` used to return — masking the real failure from
    the operator (no error in server logs) and from the RCA agent
    (which just sees "I can't access").

    The fix: detect tool-error-shaped ToolEnd outputs, log them, and
    surface them as the returned answer instead of the LLM's polite
    refusal. Operator sees the trace in logs; the RCA agent's
    `ask_knowledge_base` tool message shows the real cause.
    """

    class _BrokenRetrieverRunner:
        """Models the real failure: kb_search erupts (e.g. Ollama down,
        LiteLLM HTTP error), the SDK wraps the exception as a tool
        result string starting "An error occurred", the LLM then
        synthesizes a polite refusal. The runner emits all of this."""

        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            yield ToolStart(call_id="c1", name="kb_search", args={"query": "voids"})
            yield ToolEnd(
                call_id="c1",
                output=(
                    "An error occurred while running the tool. Please try again. "
                    "Error: HTTPConnectionPool(host='localhost', port=11434): Max "
                    "retries exceeded (Connection refused)"
                ),
            )
            yield MessageDelta(text="I cannot access the knowledge base at this time.")
            yield RunDone()

    import logging

    spec = make_spec(default_user="u")
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))

    with caplog.at_level(logging.ERROR):
        answer = await answer_question(
            _BrokenRetrieverRunner(),
            retriever,
            ["c"],
            "why voids?",
            agent_config=_test_kb_cfg(),
        )

    # The LLM's polite "I cannot access" recovery is NOT what we return.
    assert "cannot access" not in answer.lower()
    # The real error IS in the response so the operator (via the RCA
    # tool message that wraps this) sees the root cause.
    assert "Connection refused" in answer
    assert "kb_search" in answer  # tool name is named
    # Logger also got it so server stderr shows the trace.
    assert any("kb_search" in r.message for r in caplog.records)


def test_kb_chat_message_body_agent_name_picks_the_matching_kb_chat_entry():
    """Issue #32: per-message `agent_name` resolves to that kb_chats[]
    entry's AgentConfig (so the operator's FE picker actually drives
    which KB agent runs the turn). Unknown names → 422."""
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            captured["agent"] = ctx.agent_config.name if ctx.agent_config else None
            yield RunDone()

    # Build a catalog with TWO kb_chats entries so a picker is meaningful.
    from workspace_app.agent.config_catalog import AgentConfigCatalog
    from workspace_app.resources import AgentConfig

    kb_a = AgentConfig(name="KB · Fast", model="x", allowed_tools=["kb_search"])
    kb_b = AgentConfig(name="KB · Deep", model="y", allowed_tools=["kb_search"])
    catalog = AgentConfigCatalog(kb_chats=[kb_a, kb_b])

    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Capture(),
        kb_embedder=HashEmbedder(dim=EMBED_DIM),
        kb_chunker=FixedTokenChunker(max_tokens=3, overlap_tokens=1),
        agent_config_catalog=catalog,
    )
    client = TestClient(app)
    cid = client.post("/kb/chats", json={"collection_ids": []}).json()["resource_id"]

    # Default (no agent_name) → first kb_chats entry.
    r = client.post(f"/kb/chats/{cid}/messages", json={"content": "q"})
    assert r.status_code == 200, r.text
    _ = r.text  # drain the SSE stream so the runner finishes
    assert captured["agent"] == "KB · Fast"

    # Explicit name → that entry.
    captured.clear()
    client.post(
        f"/kb/chats/{cid}/messages",
        json={"content": "q", "agent_name": "KB · Deep"},
    )
    assert captured["agent"] == "KB · Deep"

    # Unknown name → 422 with the available list.
    r = client.post(
        f"/kb/chats/{cid}/messages",
        json={"content": "q", "agent_name": "Made up"},
    )
    assert r.status_code == 422
    assert "KB · Fast" in r.json()["detail"]


def test_kb_chat_message_body_quick_mode_sends_structured_enhancements():
    """FE's "quick" mode (Hybrid picker's leftmost preset) translates
    to `Enhancements(expand=0, hyde=0, rerank=False)` at the FE side
    and lands on `AgentToolContext.kb_enhancements`. The route NEVER
    accepts a bare `body.quick: bool` any more (B-flat Phase C: FE
    owns the level→enhancements translation)."""
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            captured["kb_enhancements"] = ctx.kb_enhancements
            yield RunDone()

    client = _client(_Capture())
    cid = client.post("/kb/chats", json={"collection_ids": []}).json()["resource_id"]
    client.post(
        f"/kb/chats/{cid}/messages",
        json={
            "content": "small q",
            "enhancements": {"expand": 0, "hyde": 0, "rerank": False},
        },
    )
    enh = captured["kb_enhancements"]
    assert enh is not None
    assert enh.expand == 0 and enh.hyde == 0 and enh.rerank is False

    # Omitting `enhancements` → no caller override; operator default
    # applies.
    captured.clear()
    client.post(f"/kb/chats/{cid}/messages", json={"content": "big q"})
    assert captured["kb_enhancements"] is None


def test_kb_chat_message_body_rejects_legacy_quick_field():
    """Phase C dropped the legacy `body.quick: bool` field. Sending
    it should NOT silently fall through to "all enhancements on" or
    "all off" — the pydantic model ignores unknown fields by default
    (configurable to error), but the new contract is `enhancements`
    only. This test pins the behaviour change so a regression doesn't
    silently re-introduce a bool toggle."""
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            captured["kb_enhancements"] = ctx.kb_enhancements
            yield RunDone()

    client = _client(_Capture())
    cid = client.post("/kb/chats", json={"collection_ids": []}).json()["resource_id"]
    # `quick: true` ride-along: the route IGNORES it (pydantic drops
    # unknown fields); `kb_enhancements` ends up None just like an
    # omitted body — operator default applies, no surprise "skip all".
    client.post(f"/kb/chats/{cid}/messages", json={"content": "q", "quick": True})
    assert captured["kb_enhancements"] is None


def test_kb_chat_message_body_structured_enhancements_threads_to_ctx():
    """Newer FE / API caller sends a structured `enhancements` payload
    (any subset of `expand` / `hyde` / `rerank`). The route packs that
    into `Enhancements` and hands it to the kb_search tool via context."""
    captured: dict[str, object] = {}

    class _Capture:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            captured["kb_enhancements"] = ctx.kb_enhancements
            yield RunDone()

    client = _client(_Capture())
    cid = client.post("/kb/chats", json={"collection_ids": []}).json()["resource_id"]
    client.post(
        f"/kb/chats/{cid}/messages",
        json={"content": "q", "enhancements": {"expand": 2, "rerank": False}},
    )
    enh = captured["kb_enhancements"]
    assert enh is not None
    assert enh.expand == 2 and enh.rerank is False
    assert enh.hyde is None  # unset → inherits operator default


async def test_answer_question_surfaces_run_error_when_runner_gives_up(caplog):
    """If the runner exhausted its retry budget and emits a terminal
    `RunError` (e.g. LiteLLM HTTP errors all the way down, model
    refused to start), don't return whatever partial MessageDelta
    text the LLM happened to produce before bailing — surface the
    RunError verbatim so the operator sees the real reason."""
    import logging

    from workspace_app.api.events import RunError

    class _GivingUpRunner:
        async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
            yield MessageDelta(text="I will help by ")  # partial generation before crash
            yield RunError(
                message=(
                    "giving up after 3 attempts: APIConnectionError: "
                    "litellm.APIConnectionError: APIConnectionError"
                )
            )

    spec = make_spec(default_user="u")
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))
    with caplog.at_level(logging.ERROR):
        answer = await answer_question(
            _GivingUpRunner(),
            retriever,
            ["c"],
            "anything?",
            agent_config=_test_kb_cfg(),
        )
    assert "APIConnectionError" in answer
    assert "giving up" in answer
    # Server log saw it too.
    assert any("RunError" in r.message or "APIConnectionError" in r.message for r in caplog.records)


async def test_answer_question_without_citations_has_no_footer():
    spec = make_spec(default_user="u")
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))

    answer = await answer_question(
        _PlainRunner(), retriever, ["c"], "off-topic?", agent_config=_test_kb_cfg()
    )

    assert answer == "I don't see that in the knowledge base."  # no Sources footer


def test_rca_agent_consults_kb_through_ask_knowledge_base():
    # RCA turn calls the bridge, which runs the KB agent over all collections.
    client = _client(_DualRunner())
    client.post("/kb/collections", json={"name": "kb"})  # a collection to search
    iid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]

    r = client.post(f"/a/rca/items/{iid}/messages", json={"content": "consult the kb"})

    assert r.status_code == 202
    # #43: the answer is persisted (+ broadcast on .../stream), not in the POST body.
    msgs = client.get(f"/a/rca/items/{iid}/export").json()["messages"]
    body = " ".join(m.get("content", "") or "" for m in msgs).lower()
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
    spec = make_spec(default_user="u")
    retriever = Retriever(spec, embedder=HashEmbedder(dim=EMBED_DIM))

    seen: list[AgentEvent] = []
    await answer_question(
        _KbRunner(),
        retriever,
        ["c"],
        "why voids?",
        agent_config=_test_kb_cfg(),
        on_event=seen.append,
    )

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
            assert ctx.run_subagent is not None
            ctx.on_exec_output = lambda b: self.relayed.append(b.decode())
            answer, _ = await ctx.run_subagent(
                "kb_chat", prompt, ctx.on_exec_output, ctx.investigation_id
            )
            yield MessageDelta(text=f"KB says: {answer}")
            yield RunDone()


def test_ask_knowledge_base_relays_kb_progress_to_the_run_sink():
    runner = _StreamingDualRunner()
    client = _client(runner)
    client.post("/kb/collections", json={"name": "kb"})
    iid = client.post("/a/rca/items", json={"title": "t"}).json()["resource_id"]

    r = client.post(f"/a/rca/items/{iid}/messages", json={"content": "consult the kb"})
    assert r.status_code == 202  # #43: POST awaits the turn (relay captured below)

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
