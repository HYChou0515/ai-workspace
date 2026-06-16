"""The model-sanity question registry + its mechanical graders."""

from __future__ import annotations

import pytest

from workspace_app.health.sanity import questions as Q
from workspace_app.health.sanity.questions import (
    ALL_LEVELS,
    QUESTIONS,
    SanityQuestion,
    auto_run_cells,
    comma_separated_no_numbering,
    contains,
    counts_to,
    find_question,
    is_valid_json,
    nonempty,
    one_word,
    question_key,
    user,
)


def test_post_init_rejects_inconsistent_auto_flags():
    with pytest.raises(ValueError):
        SanityQuestion("c", [user("x")], "e", auto_run=True)  # auto_run but no levels
    with pytest.raises(ValueError):
        SanityQuestion("c", [user("x")], "e", auto_levels=("none",))  # levels but not auto_run


def test_contains_is_case_insensitive_and_any_match():
    g = contains("台北", "Taipei")
    assert g("首都是台北市") is True
    assert g("The capital is TAIPEI") is True
    assert g("高雄") is False


def test_one_word_rejects_chatty_answers():
    g = one_word("藍")
    assert g("藍") is True
    assert g("藍。") is True  # trailing punctuation tolerated
    assert g("天空是藍色的") is False  # too long → fail


def test_is_valid_json_requires_bare_json():
    assert is_valid_json('{"name": "王小明", "age": 25}') is True
    assert is_valid_json('```json\n{"a":1}\n```') is False  # markdown fence → fail
    assert is_valid_json("姓名:王小明") is False


def test_comma_separated_no_numbering():
    assert comma_separated_no_numbering("蘋果, 香蕉, 葡萄") is True
    assert comma_separated_no_numbering("蘋果、香蕉、葡萄") is True
    assert comma_separated_no_numbering("1. 蘋果\n2. 香蕉") is False  # numbered
    assert comma_separated_no_numbering("蘋果 香蕉 葡萄") is False  # no separator


def test_counts_to_needs_every_integer():
    g = counts_to(20)
    assert g(" ".join(str(i) for i in range(1, 21))) is True
    assert g("1 2 3 ... 19") is False  # missing 20 etc.


def test_nonempty():
    assert nonempty("  hi ") is True
    assert nonempty("   ") is False


def test_registry_has_19_questions_each_well_formed():
    assert len(QUESTIONS) == 19
    for q in QUESTIONS:
        assert q.messages and all("role" in m and "content" in m for m in q.messages)
        assert q.expected
        # auto_run ⇔ auto_levels (enforced in __post_init__)
        assert bool(q.auto_run) == bool(q.auto_levels)


def test_locked_adjustments_are_present():
    # #4 reworded to the Saturday phrasing AND no longer auto-run
    weekday = next(q for q in QUESTIONS if "三天後" in q.messages[0]["content"])
    assert "星期六" in weekday.messages[0]["content"]
    assert weekday.auto_run is False
    # #6 JSON IS auto-run now
    js = next(q for q in QUESTIONS if q.grade is is_valid_json)
    assert js.auto_run is True
    # #10 essay carries an aux (字數) hint and no pass/fail grade
    essay = next(q for q in QUESTIONS if "300 字" in q.messages[0]["content"])
    assert essay.grade is None and essay.aux is not None
    assert essay.aux("一二三") == "3 字"


def test_multi_turn_question_is_a_message_list():
    cat = next(q for q in QUESTIONS if q.category == "多輪對話")
    assert len(cat.messages) == 2  # context turn + the actual question
    assert "咪咪" in cat.messages[0]["content"]


def test_question_key_is_stable_and_distinguishes_messages():
    q1, q2 = QUESTIONS[0], QUESTIONS[1]
    assert question_key(q1) == question_key(q1)  # stable
    assert question_key(q1) != question_key(q2)
    assert find_question(question_key(q1)) is q1
    assert find_question("deadbeef") is None


def test_auto_run_cells_cross_questions_with_their_levels():
    cells = auto_run_cells()
    # logic/date/essay run at all 4 levels; knowledge/format/etc at off+med only
    by_q: dict[str, set[str]] = {}
    for q, lvl in cells:
        by_q.setdefault(question_key(q), set()).add(lvl)
    essay = next(q for q in QUESTIONS if "300 字" in q.messages[0]["content"])
    assert by_q[question_key(essay)] == set(ALL_LEVELS)
    taipei = next(q for q in QUESTIONS if q.grade is not None and q.grade("台北"))
    # a 2-level question only appears at off+med
    two_level = next(q for q in QUESTIONS if q.auto_levels == (Q.OFF, Q.MED))
    assert by_q[question_key(two_level)] == {Q.OFF, Q.MED}
    assert taipei  # sanity: the contains-台北 question exists


def test_user_helper_shape():
    assert user("hi") == {"role": "user", "content": "hi"}
