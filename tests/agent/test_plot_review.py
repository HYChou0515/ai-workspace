"""Unit tests for the #285 VLM chart self-review engine (no real VLM/render)."""

from __future__ import annotations

import asyncio

from workspace_app.agent import plot_review
from workspace_app.agent.plot_review import (
    ChartIssues,
    adjust_style,
    detect_issues,
    parse_issues,
    run_review,
)

CHECK_KEYS = ["blank", "overlap", "truncated", "tiny_text", "clipped", "missing_legend"]


def _answer(notes: str = "none", **flags: bool) -> str:
    lines = [f"{k}: {'yes' if flags.get(k) else 'no'}" for k in CHECK_KEYS]
    lines.append(f"notes: {notes}")
    return "\n".join(lines)


class _FakeDescriber:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.calls = 0

    def answer(self, png, mime, *, question, on_chunk=None):  # noqa: ANN001
        self.calls += 1
        if on_chunk is not None:
            on_chunk("reviewing…", False)
        return self._answers.pop(0)


class _FakeRender:
    def __init__(self) -> None:
        self.styles: list[dict] = []

    async def __call__(self, style: dict):
        self.styles.append(dict(style))
        n = len(self.styles)
        return (f"png{n}".encode(), f"charts/c{n}.png")


# ─── parse_issues ─────────────────────────────────────────────────────


def test_parse_yes_no_and_notes():
    issues = parse_issues(_answer(notes="labels overlap on the left", overlap=True, tiny_text=True))
    assert issues.overlap and issues.tiny_text and not issues.clipped
    assert issues.notes == "labels overlap on the left"


def test_parse_none_notes_is_empty():
    assert parse_issues(_answer(notes="none")).notes == ""


def test_parse_unparseable_defaults_all_false():
    issues = parse_issues("the chart looks fine to me, no structured answer")
    assert not issues.any_blocking and issues.notes == ""


# ─── adjust_style ─────────────────────────────────────────────────────


def test_adjust_none_when_no_issues():
    assert adjust_style({}, ChartIssues()) is None


def test_adjust_overlap_rotates_and_widens():
    out = adjust_style({}, ChartIssues(overlap=True))
    assert out is not None and out["x_tick_rotation"] == 35.0 and out["figsize"][0] > 9.0


def test_adjust_tiny_text_bumps_font_and_dpi():
    out = adjust_style({"font_size": 10.0, "dpi": 110}, ChartIssues(tiny_text=True))
    assert out is not None
    assert out["font_size"] == 12.0 and out["dpi"] == 140


def test_adjust_clipped_adds_pad_and_taller():
    out = adjust_style({}, ChartIssues(clipped=True))
    assert out is not None
    assert out["pad"] > 1.2 and out["figsize"][1] > 5.5


def test_adjust_soft_hint_rotate_from_notes():
    out = adjust_style({}, ChartIssues(missing_legend=True, notes="please rotate the x labels"))
    assert out is not None and out["x_tick_rotation"] == 45.0


def test_adjust_soft_hint_cutoff_widens():
    out = adjust_style({}, ChartIssues(missing_legend=True, notes="legend is cut off"))
    assert out is not None and out["figsize"][0] > 9.0


def test_adjust_returns_none_when_merged_equals_style():
    # soft-hint rotate, but style already at that rotation → no actual change.
    out = adjust_style({"x_tick_rotation": 45.0}, ChartIssues(notes="rotate please"))
    assert out is None


# ─── detect_issues ────────────────────────────────────────────────────


def test_detect_issues_calls_vlm_and_parses():
    d = _FakeDescriber([_answer(overlap=True)])
    issues = detect_issues(b"png", d)
    assert issues.overlap and d.calls == 1


# ─── run_review ───────────────────────────────────────────────────────


def test_review_no_issues_returns_initial_no_passes():
    d = _FakeDescriber([_answer()])
    out = asyncio.run(
        run_review(
            initial_png=b"p0", initial_path="charts/c0.png", render=_FakeRender(), describer=d
        )
    )
    assert out.passes == 0 and out.image_path == "charts/c0.png"
    assert "no layout issues" in out.summary()


def test_review_fixes_then_keeps_better():
    d = _FakeDescriber([_answer(overlap=True), _answer()])  # detect overlap, then clean
    render = _FakeRender()
    out = asyncio.run(
        run_review(initial_png=b"p0", initial_path="charts/c0.png", render=render, describer=d)
    )
    assert out.passes == 1 and out.image_path == "charts/c1.png"
    assert "overlap" in out.fixed and out.remaining is None
    assert render.styles[0]["x_tick_rotation"] == 35.0  # the adjuster's restyle was used


def test_review_no_improvement_keeps_initial_and_reports_remaining():
    d = _FakeDescriber([_answer(overlap=True), _answer(overlap=True)])
    out = asyncio.run(
        run_review(
            initial_png=b"p0", initial_path="charts/c0.png", render=_FakeRender(), describer=d
        )
    )
    assert out.passes == 1 and out.image_path == "charts/c0.png"  # reverted to best
    assert out.remaining is not None and out.remaining.overlap
    assert "still overlap" in out.summary()


def test_review_respects_max_passes():
    # improves every pass but max_passes caps the loop before it's clean.
    d = _FakeDescriber(
        [
            _answer(overlap=True, tiny_text=True, clipped=True),
            _answer(tiny_text=True, clipped=True),
            _answer(clipped=True),
        ]
    )
    out = asyncio.run(
        run_review(
            initial_png=b"p0",
            initial_path="charts/c0.png",
            render=_FakeRender(),
            describer=d,
            max_passes=2,
        )
    )
    assert out.passes == 2 and out.remaining is not None and out.remaining.clipped


def test_review_breaks_when_adjuster_finds_nothing(monkeypatch):
    monkeypatch.setattr(plot_review, "adjust_style", lambda style, issues: None)
    d = _FakeDescriber([_answer(overlap=True)])
    out = asyncio.run(
        run_review(
            initial_png=b"p0", initial_path="charts/c0.png", render=_FakeRender(), describer=d
        )
    )
    assert out.passes == 0 and out.remaining is not None  # couldn't act → reports the issue


def test_summary_no_improvement_line():
    d = _FakeDescriber([_answer(overlap=True), _answer(overlap=True)])
    out = asyncio.run(
        run_review(
            initial_png=b"p0", initial_path="charts/c0.png", render=_FakeRender(), describer=d
        )
    )
    s = out.summary()
    assert s.startswith("Visual check (1 pass")
