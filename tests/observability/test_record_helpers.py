"""Branch coverage for `observability.record` helpers — the defensive paths
that real provider variety hits (pydantic vs dict responses, already-prefixed
models, missing endpoints, embedding shapes) but the happy-path tests don't.
"""

from __future__ import annotations

from datetime import datetime

from workspace_app.observability.record import (
    build_call_record,
    build_failure_record,
    build_summary,
    classify_call,
)

_T = datetime(2026, 6, 9, 12, 0, 0)


def test_classify_rerank_and_other():
    assert classify_call("arerank") == "rerank"
    assert classify_call("rerank") == "rerank"
    assert classify_call("image_generation") == "other"
    assert classify_call(None) == "other"


def test_response_from_pydantic_like_model_dump():
    """A response object exposing model_dump() (the real litellm shape) is
    normalised via it."""

    class FakeResp:
        def model_dump(self):
            return {"choices": [{"message": {"content": "hi", "tool_calls": None}}], "usage": {}}

    rec = build_call_record({"call_type": "acompletion"}, FakeResp(), _T, _T)
    assert rec["response"]["choices"][0]["message"]["content"] == "hi"
    assert rec["meta"]["outcome"] == "text"


def test_response_from_mapping_without_model_dump():
    """An object that's dict()-able but has no model_dump still normalises."""

    class MappingLike:
        def keys(self):
            return ["usage"]

        def __getitem__(self, key):
            return {"total_tokens": 5}

    rec = build_call_record({"call_type": "acompletion"}, MappingLike(), _T, _T)
    assert rec["meta"]["usage"] == {"total_tokens": 5}


def test_response_unserialisable_is_kept_not_dropped():
    """An opaque response keeps its repr rather than raising or vanishing."""
    rec = build_call_record({"call_type": "acompletion"}, object(), _T, _T)
    assert "_unserializable" in rec["response"]
    assert rec["meta"]["outcome"] == "empty"


def test_response_model_dump_raising_falls_back_to_dict():
    """If model_dump() blows up, normalisation falls back to dict()."""

    class Weird:
        def model_dump(self):
            raise ValueError("nope")

        def keys(self):
            return ["usage"]

        def __getitem__(self, key):
            return {"total_tokens": 7}

    rec = build_call_record({"call_type": "acompletion"}, Weird(), _T, _T)
    assert rec["meta"]["usage"] == {"total_tokens": 7}


def test_first_message_handles_non_dict_choice():
    """A choice that isn't a dict doesn't crash outcome classification."""
    rec = build_call_record({"call_type": "acompletion"}, {"choices": ["weird"]}, _T, _T)
    assert rec["meta"]["outcome"] == "empty"


def test_first_message_handles_choice_without_message():
    """A dict choice that lacks a `message` falls back cleanly."""
    rec = build_call_record(
        {"call_type": "acompletion"}, {"choices": [{"finish_reason": "x"}]}, _T, _T
    )
    assert rec["meta"]["outcome"] == "empty"


def test_already_prefixed_model_is_left_as_is():
    """A model already carrying a provider prefix isn't double-prefixed."""
    rec = build_call_record({"model": "openai/gpt-4o", "messages": []}, {}, _T, _T)
    assert rec["request"]["model"] == "openai/gpt-4o"


def test_endpoint_falls_back_to_litellm_params_api_base_without_hidden():
    """No hidden_params → the endpoint comes from litellm_params.api_base."""
    kwargs = {"model": "x", "litellm_params": {"api_base": "http://h:9000/v1"}}
    rec = build_call_record(kwargs, {}, _T, _T)
    assert rec["meta"]["endpoint"] == "h:9000"


def test_endpoint_without_port_and_default():
    """host-only endpoint keeps the host; a missing endpoint reports default."""
    with_host = build_call_record(
        {"litellm_params": {"api_base": "https://api.example.com/v1"}}, {}, _T, _T
    )
    assert with_host["meta"]["endpoint"] == "api.example.com"
    no_base = build_call_record({}, {}, _T, _T)
    assert no_base["meta"]["endpoint"] == "default"


def test_latency_is_none_for_non_datetime_bounds():
    """Non-datetime start/end → latency simply unknown, not a crash."""
    rec = build_call_record({}, {}, "not-a-time", "also-not")
    assert rec["meta"]["latency_ms"] is None


def test_failure_record_with_plain_string_error():
    """A non-exception error value is recorded as its string form."""
    rec = build_failure_record({"model": "x", "messages": []}, "raw provider error", _T, _T)
    assert rec["meta"]["outcome"] == "error"
    assert rec["error"] == "raw provider error"


def test_summary_input_count_string_and_empty():
    """A string input counts as 1; no input + no data → n is None; a non-list
    embedding leaves dim unknown."""
    one = build_summary({"call_type": "aembedding", "input": "solo"}, {"data": []}, _T, _T)
    assert one["n"] == 1
    none = build_summary({"call_type": "aembedding"}, {"data": []}, _T, _T)
    assert none["n"] is None
    no_dim = build_summary(
        {"call_type": "aembedding", "input": ["a"]}, {"data": [{"embedding": "oops"}]}, _T, _T
    )
    assert no_dim["dim"] is None
