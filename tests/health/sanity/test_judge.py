"""LLM-as-judge parse robustness (#231): small local judges reply messily, so
``judge_cell`` must recover a verdict from JSON or prose and never raise."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from workspace_app.health.sanity.judge import judge_cell, judge_verdict
from workspace_app.kb.llm import ILlm


class _StubJudge(ILlm):
    def __init__(self, reply: str, *, boom: bool = False) -> None:
        self._reply = reply
        self._boom = boom
        self.prompts: list[str] = []

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        self.prompts.append(prompt)
        if self._boom:
            raise RuntimeError("judge down")
        yield "<think>weighing</think>", True  # reasoning is dropped by collect()
        yield self._reply, False


def _judge(reply: str, **kw) -> tuple[str, str]:
    return judge_cell(_StubJudge(reply, **kw), prompt="Q", expected="E", output="O")


def test_clean_json_verdict():
    assert _judge('{"grade": "pass", "note": "正確"}') == ("pass", "正確")


def test_json_fail_verdict():
    assert _judge('結論: {"grade":"fail","note":"答非所問"}') == ("fail", "答非所問")


def test_invalid_json_braces_fall_back_to_prose_scan():
    # braces present but not valid JSON → except path, then prose scan finds fail
    g, note = _judge("{大概 fail 吧}")
    assert g == "fail" and note == "{大概 fail 吧}"


def test_json_with_unknown_grade_falls_back_to_scan():
    # valid JSON but grade isn't pass/fail → keep scanning; prose says 合格 → pass
    g, _ = _judge('{"grade": "maybe", "note": "勉強合格"}')
    assert g == "pass"


def test_prose_only_pass_and_fail_keywords():
    assert _judge("整體 PASS,沒問題")[0] == "pass"
    assert _judge("這題不合格")[0] == "fail"
    assert _judge("合格,符合期望")[0] == "pass"


def test_no_verdict_recoverable_returns_empty_grade():
    g, note = _judge("我不確定欸")
    assert g == "" and note == "我不確定欸"


def test_note_falls_back_to_truncated_reply_when_json_note_empty():
    long = "x" * 200
    g, note = _judge('{"grade": "pass", "note": ""}' + long)
    assert g == "pass" and len(note) == 120


def test_judge_crash_is_swallowed():
    assert _judge("anything", boom=True) == ("", "")


@pytest.mark.parametrize("reply", ["", "   "])
def test_blank_reply_is_empty_verdict(reply: str):
    assert _judge(reply) == ("", "")


# ── verdict parse ──────────────────────────────────────────────────────────
def _verdict(reply: str, **kw) -> tuple[int, str]:
    return judge_verdict(_StubJudge(reply, **kw), model="m", digest="d")


def test_verdict_clean_json():
    assert _verdict('{"score": 82, "summary": "- KB OK"}') == (82, "- KB OK")


def test_verdict_score_clamped_to_0_100():
    assert _verdict('{"score": 250, "summary": "s"}')[0] == 100
    assert _verdict('{"score": -7, "summary": "s"}')[0] == 0


def test_verdict_non_int_score_defaults_zero():
    assert _verdict('{"score": "高", "summary": "s"}') == (0, "s")


def test_verdict_invalid_json_falls_back_to_raw_summary():
    score, summary = _verdict("just prose, no json")
    assert score == 0 and summary == "just prose, no json"


def test_verdict_braces_but_invalid_json():
    score, summary = _verdict("{not json}")
    assert score == 0 and summary == "{not json}"


def test_verdict_empty_summary_falls_back_to_truncated_reply():
    long = "y" * 600
    score, summary = _verdict('{"score": 40, "summary": ""}' + long)
    assert score == 40 and len(summary) == 500


def test_verdict_judge_crash_is_swallowed():
    assert _verdict("anything", boom=True) == (0, "")
