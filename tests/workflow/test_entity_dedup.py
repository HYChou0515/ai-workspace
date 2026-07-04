"""Pure helpers for #435 P3 cross-origin dedup: the fenced-block overwrite (idempotent
by construction) and the ``validate_choice`` + fail-open answer parse."""

from __future__ import annotations

from workspace_app.workflow.entity_dedup import (
    fence_markers,
    match_prompt,
    parse_match,
    render_contribution,
    replace_fenced_block,
)


def test_replace_fenced_block_appends_when_absent() -> None:
    out = replace_fenced_block("human notes\n", "bug", "robot said hi")
    begin, end = fence_markers("bug")
    assert "human notes" in out  # human content untouched
    assert begin in out and end in out
    assert "robot said hi" in out


def test_replace_fenced_block_overwrites_in_place_and_is_idempotent() -> None:
    first = replace_fenced_block("notes\n", "bug", "v1")
    second = replace_fenced_block(first, "bug", "v2")
    begin, _ = fence_markers("bug")
    assert second.count(begin) == 1  # replaced, not accumulated
    assert "v1" not in second and "v2" in second
    assert "notes" in second
    # re-running with identical content is a byte-stable no-op
    assert replace_fenced_block(second, "bug", "v2") == second


def test_replace_fenced_block_preserves_content_around_it() -> None:
    body = "before\n<!-- wf:bug begin -->\nold\n<!-- wf:bug end -->\nafter\n"
    out = replace_fenced_block(body, "bug", "new")
    assert out.startswith("before\n")
    assert out.endswith("after\n")
    assert "new" in out and "old" not in out


def test_render_contribution_is_sorted_and_labelled() -> None:
    out = render_contribution("bug", {"title": "X", "assignee": "a"})
    assert out.startswith("🤖 bug:")
    assert out.index("assignee") < out.index("title")  # stable sort → no churn


def test_match_prompt_lists_candidates_and_asks_for_number_or_new() -> None:
    p = match_prompt({"title": "Login broken"}, [{"number": 5, "title": "Login 500s"}])
    assert "#5: Login 500s" in p
    assert "Login broken" in p
    assert "NEW" in p


def test_parse_match_accepts_only_existing_candidate_ids() -> None:
    assert parse_match("5", [5, 7]) == 5
    assert parse_match("#7", [5, 7]) == 7  # leading marker tolerated
    assert parse_match("7.", [7]) == 7  # trailing punctuation tolerated
    assert parse_match("the answer is 5", [5]) is None  # not a clean leading token


def test_parse_match_fails_open_on_new_or_hallucination() -> None:
    assert parse_match("NEW", [5, 7]) is None  # explicit NEW
    assert parse_match("999", [5, 7]) is None  # hallucinated id → NEW, never merged
    assert parse_match("", [5]) is None  # empty
    assert parse_match("   ", [5]) is None  # whitespace only
