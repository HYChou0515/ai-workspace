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
