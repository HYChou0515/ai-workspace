"""LLM calls carry their person's tags to LiteLLM (docs/plan-capacity-usage.md §7):
`x-litellm-tags`, read from the usage scope at the moment of the call, at each
of the four adapters — each called the way production calls it, across its
thread hop, because a hop that does not copy the context loses the scope."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import litellm
import pytest
from agents.model_settings import ModelSettings

from workspace_app.capacity.llm_tags import TAG_HEADER, TaggedModel, tag_headers
from workspace_app.capacity.scope import UsageScope, scoped
from workspace_app.failover.core import Provider, failover_stream
from workspace_app.failover.registry import CooldownRegistry
from workspace_app.kb.embedder import LitellmEmbedder
from workspace_app.kb.llm import LitellmLlm
from workspace_app.kb.vlm.litellm import LitellmVlm

ALICE = UsageScope(user="alice", app="pm", item="i1", purpose="chat")


def _tags(headers: dict[str, str]) -> list[str]:
    return headers[TAG_HEADER].split(",")


def test_no_scope_no_tags():
    assert tag_headers("llm") == {}


def test_a_scope_that_opted_out_sends_no_tags():
    with scoped(UsageScope(user="alice", purpose="chat", tags=False)):
        assert tag_headers("llm") == {}


def test_a_scope_names_the_person_app_purpose_and_kind_of_call():
    with scoped(ALICE):
        assert _tags(tag_headers("agent")) == [
            "ws",
            "ws.user:alice",
            "ws.app:pm",
            "ws.purpose:chat",
            "ws.call:agent",
        ]


def test_a_value_cannot_break_the_comma_separated_list():
    with scoped(UsageScope(user="a,b%c 周", purpose="index")):
        tags = _tags(tag_headers("embed"))

    assert tags[1] == "ws.user:a%2Cb%25c%20%E5%91%A8"
    assert "ws.app:" not in ",".join(tags), "no App, no app tag"


def _fake_stream(seen: list[dict]):
    class _Delta:
        content = "ok"
        reasoning_content = None

    def completion(**kwargs):
        seen.append(kwargs)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=_Delta())])])

    return completion


async def test_the_kb_llm_tags_its_call_from_a_thread(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(litellm, "completion", _fake_stream(seen))

    with scoped(ALICE):
        await asyncio.to_thread(lambda: list(LitellmLlm("m").stream("hi")))

    assert "ws.call:llm" in seen[0]["extra_headers"][TAG_HEADER]
    assert "ws.user:alice" in seen[0]["extra_headers"][TAG_HEADER]


async def test_a_failover_chain_keeps_the_scope_across_its_producer_thread(monkeypatch):
    """`failover_stream` drives each provider on a thread of its own; a bare
    `threading.Thread` starts with an EMPTY context, which dropped the tags of
    every KB call behind a failover chain."""
    seen: list[dict] = []
    monkeypatch.setattr(litellm, "completion", _fake_stream(seen))
    provider = Provider(
        key=("m", None),
        label="m",
        start=lambda: LitellmLlm("m").stream("hi"),
        ttft_s=5.0,
        idle_s=5.0,
        cooldown_s=1.0,
    )

    with scoped(ALICE):
        await asyncio.to_thread(
            lambda: list(failover_stream([provider], CooldownRegistry(clock=time.monotonic)))
        )

    assert "ws.user:alice" in seen[0]["extra_headers"][TAG_HEADER]


def test_the_vlm_tags_its_call(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(litellm, "completion", _fake_stream(seen))

    with scoped(ALICE):
        list(LitellmVlm("m").stream("what is this", images=[(b"\x89PNG", "image/png")]))

    assert "ws.call:vlm" in seen[0]["extra_headers"][TAG_HEADER]


def test_the_embedder_tags_its_call(monkeypatch):
    seen: list[dict] = []

    def embedding(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(data=[{"embedding": [0.0, 1.0]} for _ in kwargs["input"]])

    monkeypatch.setattr(litellm, "embedding", embedding)

    with scoped(UsageScope(user="bob", purpose="index")):
        LitellmEmbedder("m", dim=2).embed_documents(["a"])

    assert "ws.call:embed" in seen[0]["extra_headers"][TAG_HEADER]
    assert "ws.purpose:index" in seen[0]["extra_headers"][TAG_HEADER]


def test_an_untagged_call_sends_no_header_at_all(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(litellm, "completion", _fake_stream(seen))

    list(LitellmLlm("m").stream("hi"))

    assert "extra_headers" not in seen[0]


class _Inner:
    def __init__(self) -> None:
        self.settings: list[ModelSettings] = []

    async def get_response(self, *args, **kwargs):
        self.settings.append(kwargs["model_settings"])

    async def stream_response(self, *args, **kwargs):
        self.settings.append(args[2])
        yield "chunk"


@pytest.mark.parametrize("path", ["get_response", "stream_response"])
async def test_the_agent_model_tags_every_call_the_sdk_makes(path):
    """The SDK passes `model_settings` by keyword to `get_response` and as the
    third positional argument to `stream_response` — both must carry the tags,
    and the turn's own headers must survive."""
    inner = _Inner()
    model = TaggedModel(inner)  # ty: ignore[invalid-argument-type]
    settings = ModelSettings(extra_headers={"x-lane": "interactive"})

    with scoped(ALICE):
        if path == "get_response":
            await model.get_response("sys", "hi", model_settings=settings)
        else:
            async for _ in model.stream_response("sys", "hi", settings):
                pass

    headers = inner.settings[0].extra_headers or {}
    assert headers["x-lane"] == "interactive"
    assert "ws.call:agent" in str(headers[TAG_HEADER])
    assert settings.extra_headers == {"x-lane": "interactive"}, (
        "the turn's settings are not mutated"
    )


# ── capacity.llm_tags: false reaches every scope a recorder opens ─────────


def _quiet_recorder():
    from workspace_app.capacity.recorder import UsageRecorder
    from workspace_app.capacity.sink import UsageSink

    class _Null(UsageSink):
        def write(self, documents):
            pass

    return UsageRecorder(_Null(), component="api", pod="p", queue_max=100, llm_tags=False)


async def test_a_turn_of_a_recorder_with_tags_off_opens_an_untagged_scope():
    from workspace_app.agent import AgentToolContext
    from workspace_app.api import RunDone
    from workspace_app.api.turns import ChatTurnEngine
    from workspace_app.capacity.scope import current

    seen = []

    class _Runner:
        async def run(self, content, ctx):  # noqa: ANN001, ANN201
            seen.append(current())
            yield RunDone()

    rec = _quiet_recorder()
    engine = ChatTurnEngine(_Runner(), usage_recorder=rec)  # ty: ignore[invalid-argument-type]
    ctx = AgentToolContext(investigation_id="i", acting_user="alice")
    await asyncio.wait_for(engine.enqueue("i", "hi", ctx, on_complete=lambda _m: None), 5)
    await engine.forget("i")
    rec.close()

    assert [s.tags for s in seen if s is not None] == [False]


def test_a_job_of_a_recorder_with_tags_off_opens_an_untagged_scope():
    import datetime as dt

    from workspace_app.capacity.jobs import recorded_job
    from workspace_app.capacity.scope import current

    seen = []
    rec = _quiet_recorder()
    job = SimpleNamespace(
        info=SimpleNamespace(created_by="alice", created_time=dt.datetime.now(dt.UTC)),
        data=SimpleNamespace(retries=0),
    )
    recorded_job("index", lambda _job: seen.append(current()), rec)(job)
    rec.close()

    assert [s.tags for s in seen if s is not None] == [False]


def test_a_request_of_a_recorder_with_tags_off_opens_an_untagged_scope():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from workspace_app.capacity.middleware import UsageMiddleware
    from workspace_app.capacity.scope import current

    seen = []
    app = FastAPI()

    @app.get("/api/x")
    def x():
        seen.append(current())
        return {}

    rec = _quiet_recorder()
    app.add_middleware(UsageMiddleware, recorder=rec, get_user_id=lambda: "alice")
    TestClient(app).get("/api/x")
    rec.close()

    assert [s.tags for s in seen if s is not None] == [False]


def test_the_composition_root_hands_the_setting_to_the_recorder(tmp_path, monkeypatch):
    from .test_wiring import _build

    app = _build(tmp_path, monkeypatch, "capacity:\n  llm_tags: false\n")

    assert app.state.usage_recorder.llm_tags is False
