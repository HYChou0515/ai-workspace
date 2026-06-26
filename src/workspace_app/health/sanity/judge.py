"""LLM-as-judge for the sanity matrix (#231).

Built-in questions carry a mechanical Python grader; many do not (eyeball-only),
and user-authored questions never do. An optional judge ``ILlm`` scores **every**
cell pass/fail with a one-line rationale — filling the eyeball blanks and
cross-checking the naive mechanical graders. Small local judges reply messily, so
the parse is deliberately lenient and never raises: a judge hiccup must leave the
cell intact (empty AI columns), not wreck the run.
"""

from __future__ import annotations

import json
import logging
import re

from ...kb.llm import ILlm

_LOGGER = logging.getLogger(__name__)

# Judge prompt. Kept simple + explicit for small local models (one-line JSON out).
_PROMPT = """你是嚴謹但公平的評審。依「題目」與「期望/參考答案」判斷「受測回答」是否合格。
寬鬆原則:核心正確、符合期望即 pass;答錯、答非所問、未遵守要求的格式才 fail。

題目:
{prompt}

期望/參考答案:
{expected}

受測回答:
{output}

只輸出一行 JSON,不要其他文字:{{"grade": "pass" 或 "fail", "note": "20 字內理由"}}"""


def _parse(text: str) -> tuple[str, str]:
    """Extract ``(grade, note)`` from a judge reply. Prefer an embedded JSON
    object; fall back to scanning prose for a pass/fail keyword. Returns
    ``("", note)`` when no verdict is recoverable."""
    grade, note = "", ""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m is not None:
        try:
            obj = json.loads(m.group(0))
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict):
            g = str(obj.get("grade", "")).strip().lower()
            if g in ("pass", "fail"):
                grade = g
            note = str(obj.get("note", "")).strip()
    if not grade:  # no / unusable JSON → scan the prose
        low = text.lower()
        if "fail" in low or "不合格" in text:
            grade = "fail"
        elif "pass" in low or "合格" in text:
            grade = "pass"
    if not note:
        note = text.strip()[:120]
    return grade, note


def judge_cell(judge: ILlm, *, prompt: str, expected: str, output: str) -> tuple[str, str]:
    """Ask ``judge`` to grade one answer → ``(ai_grade, ai_note)``. ``ai_grade`` is
    ``"pass"`` | ``"fail"`` | ``""`` (unrecoverable). Streams through the ILlm seam
    and never raises — any failure returns ``("", "")``."""
    filled = _PROMPT.format(prompt=prompt, expected=expected, output=output)
    try:
        reply = judge.collect(filled)
    except Exception:  # noqa: BLE001 — a judge crash must not fail the cell
        _LOGGER.warning("sanity judge raised", exc_info=True)
        return "", ""
    return _parse(reply)


# Verdict prompt: the judge reads a digest of ALL a model's cells → fitness call.
_VERDICT_PROMPT = """你是模型評估專家。以下是模型「{model}」在一系列能力測試的結果摘要,
每列為「題組 | effort | 機械評分 | AI評分 | 回答節錄」。

請綜合給出整體適配度評估:
- score:0–100 的整體分數(越高越適合擔任系統角色)。
- summary:簡短 markdown(用 - 列點),說明這個模型適合/不適合哪些角色(例如 KB 問答、
  JSON 格式輸出、推理、中文處理、安全),弱點在哪。

只輸出一行 JSON,不要其他文字:{{"score": <0-100 整數>, "summary": "<markdown>"}}

結果摘要:
{digest}"""


def _parse_verdict(text: str) -> tuple[int, str]:
    """Extract ``(score, summary)`` from a verdict reply. Score clamps to 0–100;
    summary falls back to the raw reply. ``("", 0)``-equivalent is never returned —
    an empty summary signals 'nothing usable' to the caller."""
    score, summary = 0, ""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m is not None:
        try:
            obj = json.loads(m.group(0))
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict):
            try:
                score = int(obj.get("score", 0))
            except (ValueError, TypeError):
                score = 0
            summary = str(obj.get("summary", "")).strip()
    score = max(0, min(100, score))
    if not summary:
        summary = text.strip()[:500]
    return score, summary


def judge_verdict(judge: ILlm, *, model: str, digest: str) -> tuple[int, str]:
    """Ask ``judge`` for a model's overall fitness verdict → ``(score, summary)``.
    Streams; never raises — a failure returns ``(0, "")`` (empty summary = skip)."""
    try:
        reply = judge.collect(_VERDICT_PROMPT.format(model=model, digest=digest))
    except Exception:  # noqa: BLE001 — a judge crash must not fail the run
        _LOGGER.warning("sanity verdict judge raised", exc_info=True)
        return 0, ""
    return _parse_verdict(reply)
