"""ReplayService (#51 P4) — rebuild a past LLM interaction's context and
probe the CURRENT model with it. Pure LLM probe: the service never
executes a tool and never writes state; its whole output is the model's
raw response (text / reasoning / tool-call INTENT) for human comparison.

Faithfulness is the design constraint (plan §3): the context must be
assembled by the SAME code paths the live surfaces use — `history_items`
for turn history, the runner's agent assembly for system prompt + tool
schemas, the insight-extraction prompt for chat docs, the VlmDescriber
for images. These tests drive the service with a scripted completion
stream and pin what context reaches the model.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from workspace_app.health.replay import ReplayInvalidTarget, ReplayService, ReplayToolCall
from workspace_app.kb.llm import ILlm
from workspace_app.resources import AgentConfig
from workspace_app.resources.conversation import Message


def _chunk(content=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))]
    )


class _FakeCompletion:
    """Stands in for `litellm.completion` — records the call, plays a
    scripted chunk stream."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.kwargs: dict = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return iter(self._chunks)


def test_replay_turn_rebuilds_context_and_returns_model_text():
    """Tracer bullet: replaying an assistant answer feeds the model the
    same dialogue the original turn saw (system prompt first, prior
    messages mapped) and returns the streamed text."""
    completion = _FakeCompletion([_chunk("The fan "), _chunk("was off.")])
    service = ReplayService(completion=completion)
    config = AgentConfig(name="rca", system_prompt="You are an RCA agent.", allowed_tools=[])
    messages = [
        Message(role="user", content="Why did zone 3 overheat?"),
        Message(role="assistant", content="The fan controller was off."),
    ]

    result = service.replay_turn(messages=messages, index=1, config=config)

    assert result.text == "The fan was off."
    assert result.model == config.model
    assert result.tool_calls == []
    assert result.latency_ms >= 0
    # The probe streamed (always-stream rule) against the config's model.
    assert completion.kwargs["stream"] is True
    assert completion.kwargs["model"] == config.model
    # Context = system prompt + everything BEFORE the replayed message.
    sent = completion.kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "You are an RCA agent."}
    assert sent[1:] == [{"role": "user", "content": "Why did zone 3 overheat?"}]


def test_replay_turn_reports_the_request_it_sent():
    """#69 observability: the replay echoes WHAT it sent — model, redacted
    endpoint, tool names, and the tool knobs (left unset, like the live
    runner now does) — so an operator can put it side-by-side with the live
    turn's logged trace and spot a config difference."""
    completion = _FakeCompletion([_chunk("ok")])
    service = ReplayService(completion=completion)
    config = AgentConfig(
        name="kb",
        system_prompt="KB.",
        allowed_tools=["kb_search"],
        llm_base_url="http://proxy:4000/v1",
    )
    messages = [
        Message(role="user", content="q"),
        Message(role="assistant", content="a"),
    ]

    result = service.replay_turn(messages=messages, index=1, config=config)

    assert result.request is not None
    assert result.request.model == config.model
    assert result.request.endpoint == "proxy:4000"
    assert result.request.tools == ["kb_search"]
    assert result.request.parallel_tool_calls == "unset"
    assert result.request.tool_choice == "auto (unset)"


def test_replay_turn_expands_tool_history_like_the_runner_does():
    """A persisted tool message carries call + output in one record; the
    model must see BOTH (its decision to call, and what came back) —
    same expansion `history_items` does for the live runner, in the
    chat-completions shape the probe speaks."""
    completion = _FakeCompletion([_chunk("ok")])
    service = ReplayService(completion=completion)
    config = AgentConfig(name="rca", system_prompt="sys", allowed_tools=[])
    messages = [
        Message(role="user", content="check the oven log"),
        Message(
            role="tool",
            content="zone3: 412C",
            tool_call_id="call_1",
            tool_name="read_file",
            tool_args={"path": "oven.log"},
        ),
        Message(role="assistant", content="Zone 3 ran hot."),
    ]

    service.replay_turn(messages=messages, index=2, config=config)

    sent = completion.kwargs["messages"]
    assert sent[1] == {"role": "user", "content": "check the oven log"}
    # The model's tool decision…
    assert sent[2]["role"] == "assistant"
    (call,) = sent[2]["tool_calls"]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "read_file"
    assert call["function"]["arguments"] == '{"path": "oven.log"}'
    # …and the persisted output it reacted to.
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": "zone3: 412C"}


def test_replay_turn_separates_reasoning_from_the_answer():
    """Qwen3-style `<think>…</think>` (split mid-stream) and provider
    `reasoning_content` deltas both land in `reasoning`, not `text` —
    the FE renders them collapsed, same as live turns."""
    chunks = [
        _chunk("<th"),
        _chunk("ink>weighing options</think>It was "),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None, tool_calls=None, reasoning_content="…more thought…"
                    )
                )
            ]
        ),
        _chunk("the fan."),
    ]
    service = ReplayService(completion=_FakeCompletion(chunks))
    config = AgentConfig(name="rca", allowed_tools=[])
    messages = [
        Message(role="user", content="why?"),
        Message(role="assistant", content="fan"),
    ]

    result = service.replay_turn(messages=messages, index=1, config=config)

    assert result.text == "It was the fan."
    assert result.reasoning == "weighing options…more thought…"


def _tool_chunk(index=0, call_id=None, name=None, args=None):
    tc = SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]))]
    )


def test_replay_turn_reports_tool_call_intent_without_executing():
    """A replayed tool call comes back as INTENT — name + parsed args
    assembled from the streamed fragments. Nothing runs."""
    chunks = [
        _tool_chunk(call_id="call_9", name="read_file", args='{"pa'),
        _tool_chunk(args='th": "oven.log"}'),
        _chunk(),
    ]
    service = ReplayService(completion=_FakeCompletion(chunks))
    config = AgentConfig(name="rca", allowed_tools=[])
    messages = [
        Message(role="user", content="check the log"),
        Message(
            role="tool",
            content="412C",
            tool_call_id="c1",
            tool_name="read_file",
            tool_args={"path": "oven.log"},
        ),
    ]

    result = service.replay_turn(messages=messages, index=1, config=config)

    assert result.tool_calls == [ReplayToolCall(name="read_file", arguments={"path": "oven.log"})]
    assert result.text == ""


def test_replay_turn_offers_the_agents_real_tool_schemas():
    """The probe must offer the same tools the live agent had —
    otherwise a tool-calling failure could just mean "no tools were on
    the menu". Default config (allowed_tools=None) → workspace toolset
    reaches both the system prompt and the `tools` parameter."""
    completion = _FakeCompletion([_chunk("ok")])
    service = ReplayService(completion=completion)
    config = AgentConfig(name="rca", system_prompt="sys")  # allowed_tools=None → defaults
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
    ]

    service.replay_turn(messages=messages, index=1, config=config)

    offered = completion.kwargs["tools"]
    assert offered, "default toolset must be offered to the model"
    names = {t["function"]["name"] for t in offered}
    assert "read_file" in names
    assert all(t["type"] == "function" for t in offered)
    # The runner folds a tool inventory into the system prompt for small
    # local models — the replay context must match.
    assert "## Tools available" in completion.kwargs["messages"][0]["content"]


def test_replay_turn_rejects_targets_that_are_not_model_output():
    service = ReplayService(completion=_FakeCompletion([]))
    config = AgentConfig(name="rca", allowed_tools=[])
    messages = [Message(role="user", content="hi")]
    with pytest.raises(ReplayInvalidTarget):
        service.replay_turn(messages=messages, index=0, config=config)  # a user msg
    with pytest.raises(ReplayInvalidTarget):
        service.replay_turn(messages=messages, index=5, config=config)  # out of range


# ── doc-level replay ─────────────────────────────────────────────────


class _RecordingLlm(ILlm):
    """ILlm fake that records the prompt and plays a scripted stream."""

    def __init__(self, *chunks: tuple[str, bool]) -> None:
        self._chunks = chunks
        self.prompt = ""

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompt = prompt
        yield from self._chunks


def test_replay_doc_chat_export_resends_the_extraction_prompt():
    """A chat-export doc replays the insight-extraction interaction: the
    KB LLM gets the SAME prompt ingestion builds (serialised transcript
    inside the template), and the raw response comes back verbatim plus
    a would-it-parse note — the qwen3 incident surfaced exactly here."""
    raw = '{"insights": [{"kind": "context", "title": "Fab", "markdown": "## Fab\\n\\nF14."}]}'
    llm = _RecordingLlm(("considering…", True), (raw, False))
    service = ReplayService(completion=_FakeCompletion([]), kb_llm=llm)
    blob = json.dumps(
        {
            "title": "Oven RCA",
            "messages": [
                {"role": "user", "content": "why did zone 3 overheat?"},
                {"role": "assistant", "content": "the fan controller was off"},
            ],
        }
    ).encode()

    result = service.replay_doc(path="exports/inv-1.chat.json", mime="application/json", blob=blob)

    assert result.text == raw
    assert result.reasoning == "considering…"
    # The ingest-path prompt: template resolved, transcript folded in.
    assert "User: why did zone 3 overheat?" in llm.prompt
    assert "Assistant: the fan controller was off" in llm.prompt
    assert "{conversation}" not in llm.prompt
    # The note tells the human what live ingestion would make of it.
    assert "1 insight" in result.note


def test_replay_doc_image_reruns_the_describer():
    """An image doc replays its VLM description through the real
    VlmDescriber (same prompt template / mime fallback as ingest)."""
    from workspace_app.kb.vlm import IVlm, VlmDescriber

    class _FakeVlm(IVlm):
        def __init__(self) -> None:
            self.images: list[tuple[bytes, str]] = []

        def stream(self, prompt, *, images):
            self.images.extend(images)
            yield ("## Visual description\n\nA red square.", False)

    vlm = _FakeVlm()
    service = ReplayService(completion=_FakeCompletion([]), describer=VlmDescriber(vlm))

    result = service.replay_doc(path="shots/die.png", mime="image/png", blob=b"\x89PNG-bytes")

    assert "red square" in result.text
    assert vlm.images == [(b"\x89PNG-bytes", "image/png")]


def test_replay_doc_rejects_docs_with_no_llm_step():
    """A plain text/markdown doc never touched an LLM during ingestion —
    there is nothing to replay, and the caller gets told so."""
    from workspace_app.health.replay import ReplayUnsupported

    service = ReplayService(completion=_FakeCompletion([]))
    with pytest.raises(ReplayUnsupported):
        service.replay_doc(path="notes/readme.md", mime="text/markdown", blob=b"# hi")
    # Configured-off components are equally not replayable.
    with pytest.raises(ReplayUnsupported):
        service.replay_doc(path="a.chat.json", mime="application/json", blob=b"{}")  # no kb_llm
    with pytest.raises(ReplayUnsupported):
        service.replay_doc(path="a.png", mime="image/png", blob=b"png")  # no describer


def test_replay_turn_shows_malformed_tool_args_verbatim():
    """Replay exists to SHOW a model emitting broken JSON — unparseable
    (or non-object) streamed arguments surface as `{"_raw": …}` instead
    of being silently dropped."""
    chunks = [
        _tool_chunk(call_id="c1", name="plot", args='{"x": 1}{"y"'),  # concat mess
        _tool_chunk(index=1, call_id="c2", name="lookup", args='"just-a-string"'),
        _chunk(),
    ]
    service = ReplayService(completion=_FakeCompletion(chunks))
    config = AgentConfig(name="rca", allowed_tools=[])
    messages = [
        Message(role="user", content="plot it"),
        Message(role="assistant", content="done"),
    ]

    result = service.replay_turn(messages=messages, index=1, config=config)

    assert result.tool_calls == [
        ReplayToolCall(name="plot", arguments={"_raw": '{"x": 1}{"y"'}),
        ReplayToolCall(name="lookup", arguments={"_raw": '"just-a-string"'}),
    ]
