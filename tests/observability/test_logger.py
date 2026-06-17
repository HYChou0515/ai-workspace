"""`observability.logger.LlmCallLogger` — the litellm CustomLogger that routes
each call to the writer: generative → full record, embedding/rerank → summary.
It offloads writes off the event loop and is best-effort (a logging failure
must never break a turn).

The async hooks are driven via `asyncio.run` — no pytest-asyncio needed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from workspace_app.observability.logger import LlmCallLogger
from workspace_app.observability.writer import LlmLogWriter

_T0 = datetime(2026, 6, 9, 12, 0, 0)
_T1 = datetime(2026, 6, 9, 12, 0, 3)


def _gen_kwargs():
    return {
        "model": "qwen3:8b",
        "custom_llm_provider": "ollama_chat",
        "messages": [{"role": "user", "content": "hi"}],
        "optional_params": {"stream": True},
        "call_type": "acompletion",
        "litellm_params": {"metadata": {"hidden_params": {"litellm_call_id": "c1"}}},
    }


def _gen_resp():
    return {"choices": [{"message": {"content": "hello", "tool_calls": None}}], "usage": {}}


def _files(root: Path):
    return list(root.rglob("[0-9]*.json"))


def test_logger_writes_full_record_for_generative(tmp_path: Path):
    writer = LlmLogWriter(tmp_path / "llm")
    logger = LlmCallLogger(writer)
    asyncio.run(logger.async_log_success_event(_gen_kwargs(), _gen_resp(), _T0, _T1))
    assert len(_files(tmp_path / "llm")) == 1


def test_logger_summarises_embedding_no_full_file(tmp_path: Path):
    writer = LlmLogWriter(tmp_path / "llm")
    logger = LlmCallLogger(writer)
    kwargs = {
        "model": "bge-m3",
        "custom_llm_provider": "ollama",
        "call_type": "aembedding",
        "input": ["a", "b"],
        "litellm_params": {"metadata": {"hidden_params": {"litellm_call_id": "e1"}}},
    }
    resp = {"data": [{"embedding": [0.0] * 8}, {"embedding": [0.0] * 8}]}
    asyncio.run(logger.async_log_success_event(kwargs, resp, _T0, _T1))
    assert _files(tmp_path / "llm") == []
    index = next((tmp_path / "llm").rglob("index.jsonl"))
    assert json.loads(index.read_text(encoding="utf-8").strip())["kind"] == "embedding"


def test_logger_best_effort_swallows_writer_error(tmp_path: Path):
    class BoomWriter:
        def write_call(self, record):
            raise RuntimeError("disk full")

        def write_summary(self, summary):
            raise RuntimeError("disk full")

    logger = LlmCallLogger(BoomWriter())  # ty: ignore[invalid-argument-type]
    # Must not raise — a logging failure cannot be allowed to break a turn.
    asyncio.run(logger.async_log_success_event(_gen_kwargs(), _gen_resp(), _T0, _T1))


def test_logger_failure_event_is_best_effort(tmp_path: Path):
    class BoomWriter:
        def write_call(self, record):
            raise RuntimeError("disk full")

    logger = LlmCallLogger(BoomWriter())  # ty: ignore[invalid-argument-type]
    kwargs = {**_gen_kwargs(), "exception": ValueError("boom")}
    # A failing writer on the failure path must also be swallowed.
    asyncio.run(logger.async_log_failure_event(kwargs, None, _T0, _T1))


def test_logger_writes_failure_record_with_error(tmp_path: Path):
    writer = LlmLogWriter(tmp_path / "llm")
    logger = LlmCallLogger(writer)
    kwargs = {**_gen_kwargs(), "exception": ValueError("boom")}
    asyncio.run(logger.async_log_failure_event(kwargs, None, _T0, _T1))
    files = _files(tmp_path / "llm")
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["meta"]["outcome"] == "error"
    assert "boom" in rec["error"]
