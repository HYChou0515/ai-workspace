"""`observability.writer.LlmLogWriter` — persists records to disk:
one JSON file per generative call + a per-day `index.jsonl` summary line,
date-partitioned so a day's logs delete with a single `rm -rf` (好管理/好刪除).

The writer is synchronous; the litellm logger offloads it off the event loop
(不卡). These tests drive it directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from workspace_app.observability.writer import LlmLogWriter


def _record(call_id: str = "abc") -> dict:
    return {
        "meta": {
            "ts": "2026-06-09T12:00:00",
            "call_type": "acompletion",
            "model": "ollama_chat/qwen3:8b",
            "outcome": "tool_call",
            "usage": {"total_tokens": 244},
            "latency_ms": 3000,
            "litellm_call_id": call_id,
        },
        "request": {
            "model": "ollama_chat/qwen3:8b",
            "messages": [{"role": "user", "content": "hi"}],
        },
        "response": {"choices": [{"message": {"content": "", "tool_calls": [{"id": "t"}]}}]},
    }


def test_writer_persists_full_record_under_date_dir(tmp_path: Path):
    """The per-call file holds the FULL record verbatim, under a date dir."""
    w = LlmLogWriter(tmp_path / "llm")
    path = w.write_call(_record())
    assert path.parent == tmp_path / "llm" / "2026-06-09"
    assert json.loads(path.read_text(encoding="utf-8")) == _record()


def test_writer_appends_index_line_pointing_at_file(tmp_path: Path):
    """Each call appends a scannable summary line to the day's index.jsonl,
    pointing back at its full-record file."""
    w = LlmLogWriter(tmp_path / "llm")
    path = w.write_call(_record())
    index = tmp_path / "llm" / "2026-06-09" / "index.jsonl"
    lines = index.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["call_type"] == "acompletion"
    assert entry["outcome"] == "tool_call"
    assert entry["file"] == path.name


def test_writer_summary_appends_index_only_no_file(tmp_path: Path):
    """A non-generative summary appends to the index but writes no per-call
    file (vectors don't get a full record)."""
    w = LlmLogWriter(tmp_path / "llm")
    w.write_summary(
        {
            "ts": "2026-06-09T12:00:00",
            "call_type": "aembedding",
            "kind": "embedding",
            "model": "ollama/bge-m3",
            "n": 64,
            "dim": 1024,
        }
    )
    day = tmp_path / "llm" / "2026-06-09"
    entry = json.loads((day / "index.jsonl").read_text(encoding="utf-8").strip())
    assert entry["kind"] == "embedding"
    assert list(day.glob("[0-9]*.json")) == []


def test_writer_sequence_numbers_increment(tmp_path: Path):
    """Files get an incrementing 4-digit sequence for stable ordering."""
    w = LlmLogWriter(tmp_path / "llm")
    p1 = w.write_call(_record("a"))
    p2 = w.write_call(_record("b"))
    assert p1.name.startswith("0001-")
    assert p2.name.startswith("0002-")


def test_writer_drops_replay_helper(tmp_path: Path):
    """A ready-to-run replay.py lands at the log root so a record can be
    re-fired with one command."""
    LlmLogWriter(tmp_path / "llm")
    assert (tmp_path / "llm" / "replay.py").is_file()


def test_writer_uses_unknown_date_dir_when_ts_missing(tmp_path: Path):
    """A record with no parseable ts still lands somewhere (unknown-date),
    never lost."""
    w = LlmLogWriter(tmp_path / "llm")
    rec = _record()
    rec["meta"]["ts"] = ""
    path = w.write_call(rec)
    assert path.parent == tmp_path / "llm" / "unknown-date"


def test_writer_record_with_unserialisable_value_still_writes(tmp_path: Path):
    """Robustness: a non-JSON value in the record must not break the write —
    it's coerced (default=str), never dropped silently to an exception."""
    w = LlmLogWriter(tmp_path / "llm")
    rec = _record()
    rec["response"]["weird"] = object()
    path = w.write_call(rec)
    assert path.is_file()
    assert "object at 0x" in path.read_text(encoding="utf-8")
