"""`observability.record.build_call_record` — turn one litellm callback's
(kwargs, response) into a faithful, replayable record (observability feature B).

The record must let the operator answer "why did the LLM reply like that?":
the full sent messages + tools, every param, and the complete assembled
response. Its `request` block is exactly `litellm.completion(**request)`
kwargs so it can be copy-pasted to re-fire the call.

These tests build the record from a representative callback payload (shapes
taken from a live Ollama streaming+tools call) — no live LLM needed.
"""

from __future__ import annotations

from datetime import datetime

from workspace_app.observability.record import build_call_record, build_summary, classify_call

_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
}


def _kwargs():
    return {
        "model": "qwen3:8b",  # provider stripped by litellm
        "custom_llm_provider": "ollama_chat",
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Call get_weather for Tokyo."},
        ],
        "optional_params": {"stream": True, "tools": [_TOOL], "tool_choice": "auto"},
        "call_type": "acompletion",
        "litellm_params": {
            "api_base": "http://localhost:11434/api/chat",
            "api_key": None,
            "metadata": {
                "hidden_params": {
                    "api_base": "http://localhost:11434",
                    "custom_llm_provider": "ollama_chat",
                    "litellm_call_id": "call-abc123",
                }
            },
        },
    }


def _response():
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "Tokyo"}',
                            },
                        }
                    ],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 132, "completion_tokens": 112, "total_tokens": 244},
    }


def test_record_request_is_replayable_with_full_model_and_messages():
    """The `request` block reconstructs the provider-prefixed model and carries
    the full sent messages + tools — i.e. `litellm.completion(**request)`."""
    rec = build_call_record(
        _kwargs(), _response(), datetime(2026, 6, 9, 12, 0, 0), datetime(2026, 6, 9, 12, 0, 3)
    )
    req = rec["request"]
    assert req["model"] == "ollama_chat/qwen3:8b"  # provider prefix rebuilt
    assert req["messages"][0]["content"] == "You are terse."  # full system prompt
    assert req["tools"] == [_TOOL]  # tools schema verbatim
    assert req["tool_choice"] == "auto"


def test_record_response_keeps_assembled_tool_calls():
    """The response block is the full assembled reply — tool_calls intact."""
    rec = build_call_record(
        _kwargs(), _response(), datetime(2026, 6, 9, 12, 0, 0), datetime(2026, 6, 9, 12, 0, 3)
    )
    tcs = rec["response"]["choices"][0]["message"]["tool_calls"]
    assert tcs[0]["function"]["name"] == "get_weather"
    assert tcs[0]["function"]["arguments"] == '{"city": "Tokyo"}'


def test_record_meta_classifies_outcome_and_timing():
    """Meta carries the human-scannable summary: a tool_call outcome, latency,
    usage, and the correlation id."""
    rec = build_call_record(
        _kwargs(), _response(), datetime(2026, 6, 9, 12, 0, 0), datetime(2026, 6, 9, 12, 0, 3)
    )
    meta = rec["meta"]
    assert meta["outcome"] == "tool_call"
    assert meta["call_type"] == "acompletion"
    assert meta["latency_ms"] == 3000
    assert meta["usage"]["total_tokens"] == 244
    assert meta["litellm_call_id"] == "call-abc123"


def test_record_outcome_text_when_prose_reply():
    """A prose reply with no tool call classifies as `text`."""
    resp = {"choices": [{"message": {"content": "Tokyo is sunny.", "tool_calls": None}}]}
    rec = build_call_record(_kwargs(), resp, datetime(2026, 6, 9), datetime(2026, 6, 9))
    assert rec["meta"]["outcome"] == "text"


def test_record_outcome_empty_when_no_content_no_tools():
    """Nothing user-visible classifies as `empty`."""
    resp = {"choices": [{"message": {"content": "", "tool_calls": None}}]}
    rec = build_call_record(_kwargs(), resp, datetime(2026, 6, 9), datetime(2026, 6, 9))
    assert rec["meta"]["outcome"] == "empty"


def test_summary_for_embedding_records_count_and_dim_not_vectors():
    """An embedding call is summarised: model + input count + vector width +
    timing — never the vectors themselves."""
    kwargs = {
        "model": "bge-m3",
        "custom_llm_provider": "ollama",
        "call_type": "aembedding",
        "input": ["a", "b", "c"],
        "litellm_params": {"metadata": {"hidden_params": {"litellm_call_id": "emb-1"}}},
    }
    resp = {"data": [{"embedding": [0.0] * 1024} for _ in range(3)], "usage": {"prompt_tokens": 9}}
    s = build_summary(
        kwargs, resp, datetime(2026, 6, 9, 12, 0, 0), datetime(2026, 6, 9, 12, 0, 0, 12000)
    )
    assert classify_call("aembedding") == "embedding"
    assert s["kind"] == "embedding"
    assert s["n"] == 3
    assert s["dim"] == 1024
    assert "embedding" not in str(s.get("data", ""))  # no vectors leaked
