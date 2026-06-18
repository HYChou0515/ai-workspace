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
