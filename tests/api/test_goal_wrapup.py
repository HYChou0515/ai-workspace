"""#615 P5: the morning hand-over.

An overnight run that ends silently is the same as one that never happened —
you arrive to a thread you did not read and have to reconstruct it. Every
unattended ending writes one summary into the chat and rings the bell.
"""

from __future__ import annotations

from workspace_app.api.goal_wrapup import ENDINGS, headline, marker_text, write_summary
from workspace_app.kb.llm import ILlm


class _Summariser(ILlm):
    def __init__(self, text: str = "改好了 report.md,測試全過。") -> None:
        self.text = text
        self.prompts: list[str] = []

    def stream(self, prompt: str):
        self.prompts.append(prompt)
        yield (self.text, False)


class _BrokenSummariser(ILlm):
    def stream(self, prompt: str):
        raise RuntimeError("the cheap model is down")
        yield ("", False)  # pragma: no cover - unreachable, keeps this a generator


def test_every_ending_says_something_different_about_what_to_do_next():
    # "Stuck" wants a person to look; "budget spent" wants a decision; "window
    # closed" wants nothing at all. One generic "run finished" would make the
    # reader open the thread every time to find out which it was.
    lines = {e: marker_text(e, "the report exists", "") for e in ENDINGS}
    assert len(set(lines.values())) == len(ENDINGS)
    assert "卡住" in lines["stalled"]
    assert "額度" in lines["exhausted"]
    assert "晚上" in lines["window"]


def test_the_summary_is_carried_into_the_hand_over():
    llm = _Summariser()
    body = write_summary(
        llm, condition="the report exists", ending="met", transcript="assistant: wrote it"
    )
    assert body == "改好了 report.md,測試全過。"
    assert "the report exists" in llm.prompts[0]
    assert "assistant: wrote it" in llm.prompts[0]
    assert body in marker_text("met", "the report exists", body)


def test_a_dead_summariser_costs_detail_not_the_hand_over():
    # The ending is the load-bearing part. If the cheap model is down we still
    # say what happened and still ring the bell; only the story is missing.
    body = write_summary(_BrokenSummariser(), condition="ship it", ending="stalled", transcript="…")
    assert body == ""
    assert headline("stalled", "ship it") in marker_text("stalled", "ship it", body)
