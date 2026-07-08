"""ILlm.collect: drains the stream, forwards every chunk (live thinking), and
returns only the non-reasoning content."""

from collections.abc import Iterator
from types import SimpleNamespace

import litellm
import pytest

from workspace_app.kb.llm import ILlm, LitellmLlm


class _FakeLlm(ILlm):
    def __init__(self, chunks: list[tuple[str, bool]]) -> None:
        self._chunks = chunks

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        yield from self._chunks


def test_collect_returns_content_and_forwards_all_chunks():
    llm = _FakeLlm([("<think>", True), ("hmm", True), ("hello ", False), ("world", False)])
    seen: list[tuple[str, bool]] = []
    text = llm.collect("p", on_chunk=lambda t, r: seen.append((t, r)))
    assert text == "hello world"  # reasoning chunks excluded from the result
    assert seen == [("<think>", True), ("hmm", True), ("hello ", False), ("world", False)]


def test_collect_without_callback_still_works():
    assert _FakeLlm([("a", False), ("b", False)]).collect("p") == "ab"


def test_collect_recovers_the_answer_from_reasoning_when_opted_in(caplog):
    # #494 S2: a vLLM reasoning model can route its whole answer (JSON included)
    # into the reasoning channel — e.g. it hits max_tokens before the closing
    # </think>, so every content delta is empty. Dropping reasoning then returned
    # "", which silently zeroed the card-gen digest. A structured caller opts into
    # recovery so collect() hands the reasoning text to the parser, and logs it.
    llm = _FakeLlm([("<think>the answer is ", True), ('{"cards": []}', True)])
    with caplog.at_level("WARNING"):
        text = llm.collect("p", recover_reasoning=True)
    assert text == '<think>the answer is {"cards": []}'  # recovered, not ""
    assert any("reasoning" in r.message.lower() for r in caplog.records)


def test_collect_does_not_recover_reasoning_by_default():
    # Recovery is OPT-IN: without the flag a reasoning-only reply stays "" so a
    # caller whose blank reply is a legitimate outcome (an LLM judge's "no
    # verdict") keeps the strict content-only contract.
    llm = _FakeLlm([("<think>weighing</think>", True)])
    assert llm.collect("p") == ""


def test_collect_prefers_content_and_does_not_append_reasoning_when_content_present():
    # The recovery is a fallback ONLY: when the model emits real content, the
    # reasoning stays out of the result even when opted in (no double-count).
    llm = _FakeLlm([("<think>scratch", True), ("real answer", False)])
    assert llm.collect("p", recover_reasoning=True) == "real answer"


def test_collect_returns_empty_when_the_model_emits_nothing_even_opted_in():
    # Genuinely empty (no content, no reasoning) still returns "" — the recovery
    # only fires when there IS reasoning to fall back to.
    assert _FakeLlm([]).collect("p", recover_reasoning=True) == ""
    assert _FakeLlm([("", False), ("", True)]).collect("p", recover_reasoning=True) == ""


def _capture_completion_kwargs(monkeypatch) -> dict:
    """Patch litellm.completion to record kwargs and yield nothing."""
    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return iter(())  # an empty stream — stream() just won't yield

    monkeypatch.setattr(litellm, "completion", fake_completion)
    return captured


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # Ollama → litellm think=False, no reasoning_effort.
        ("ollama_chat/qwen3:14b", {"think": False}),
        ("ollama/qwen3:14b", {"think": False}),
        # everything else → vLLM enable_thinking=False via extra_body.
        (
            "hosted_vllm/qwen3-32b",
            {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    ],
)
def test_reasoning_none_sends_provider_disable_param_not_reasoning_effort(
    monkeypatch, model, expected
):
    captured = _capture_completion_kwargs(monkeypatch)
    list(LitellmLlm(model, reasoning_effort="none").stream("hi"))
    assert "reasoning_effort" not in captured  # the no-op param is dropped
    for k, v in expected.items():
        assert captured[k] == v


def test_reasoning_low_sends_reasoning_effort_unchanged(monkeypatch):
    captured = _capture_completion_kwargs(monkeypatch)
    list(LitellmLlm("ollama_chat/qwen3:14b", reasoning_effort="low").stream("hi"))
    assert captured["reasoning_effort"] == "low"
    assert "think" not in captured and "extra_body" not in captured


def test_reasoning_unset_omits_all_reasoning_params(monkeypatch):
    captured = _capture_completion_kwargs(monkeypatch)
    list(LitellmLlm("ollama_chat/qwen3:14b").stream("hi"))
    assert "reasoning_effort" not in captured
    assert "think" not in captured and "extra_body" not in captured


def test_stream_yields_content_and_reasoning(monkeypatch):
    """stream() splits reasoning_content from content deltas (covers the loop body)."""

    def fake_completion(**kwargs):
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="th", content=None))]
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content=None, content="hi"))]
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    assert list(LitellmLlm("ollama_chat/qwen3:14b").stream("p")) == [("th", True), ("hi", False)]
