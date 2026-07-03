"""Compute-on-read relational projection (#419 §A4). Backref collects the
records pointing here; rollup aggregates over that backref. Pure over a
`corpus` (type → number → entity), so no index and no I/O to test."""

from __future__ import annotations

from workspace_app.entity.parser import ParsedEntity
from workspace_app.entity.projection import compute_derived
from workspace_app.entity.schema import EntitySchema, FieldSpec, Role


def _issue(number: int, milestone: int, status: str, progress: int) -> ParsedEntity:
    fields = {"milestone": milestone, "status": status, "progress": progress}
    return ParsedEntity(number, "issue", fields, "", [])


_MILESTONE = EntitySchema(
    fields=[
        FieldSpec("issues", Role.BACKREF, from_="issue.milestone"),
        FieldSpec("avg_progress", Role.ROLLUP, over="issues", agg="avg", field="progress"),
        FieldSpec("done", Role.ROLLUP, over="issues", agg="count", where={"status": "done"}),
    ]
)


def _corpus() -> dict[str, dict[int, ParsedEntity]]:
    return {
        "issue": {
            10: _issue(10, 1, "open", 50),
            11: _issue(11, 1, "done", 100),
            12: _issue(12, 2, "open", 0),  # belongs to a different milestone
        }
    }


def test_backref_collects_only_records_pointing_here() -> None:
    milestone = ParsedEntity(1, "milestone", {"title": "M1"}, "", [])
    derived = compute_derived(milestone, _MILESTONE, _corpus())
    assert derived["issues"] == [10, 11]


def test_rollup_avg_over_the_backref() -> None:
    milestone = ParsedEntity(1, "milestone", {}, "", [])
    derived = compute_derived(milestone, _MILESTONE, _corpus())
    assert derived["avg_progress"] == 75


def test_rollup_count_with_single_equality_where() -> None:
    milestone = ParsedEntity(1, "milestone", {}, "", [])
    derived = compute_derived(milestone, _MILESTONE, _corpus())
    assert derived["done"] == 1


def test_rollup_sum_min_max_and_empty() -> None:
    schema = EntitySchema(
        fields=[
            FieldSpec("kids", Role.BACKREF, from_="task.parent"),
            FieldSpec("total", Role.ROLLUP, over="kids", agg="sum", field="pts"),
            FieldSpec("lo", Role.ROLLUP, over="kids", agg="min", field="pts"),
            FieldSpec("hi", Role.ROLLUP, over="kids", agg="max", field="pts"),
            FieldSpec("none_field", Role.ROLLUP, over="kids", agg="avg", field="missing"),
            FieldSpec("bad_agg", Role.ROLLUP, over="kids", agg="median", field="pts"),
        ]
    )
    parent = ParsedEntity(1, "epic", {}, "", [])
    corpus = {
        "task": {
            5: ParsedEntity(5, "task", {"parent": 1, "pts": 3}, "", []),
            6: ParsedEntity(6, "task", {"parent": 1, "pts": 7}, "", []),
        }
    }
    derived = compute_derived(parent, schema, corpus)
    assert derived["total"] == 10
    assert derived["lo"] == 3
    assert derived["hi"] == 7
    assert derived["none_field"] is None  # no numeric values → None (non-sum agg)
    assert derived["bad_agg"] is None  # unknown agg


def test_rollup_sum_over_empty_backref_is_zero() -> None:
    schema = EntitySchema(
        fields=[
            FieldSpec("kids", Role.BACKREF, from_="task.parent"),
            FieldSpec("total", Role.ROLLUP, over="kids", agg="sum", field="pts"),
        ]
    )
    derived = compute_derived(ParsedEntity(9, "epic", {}, "", []), schema, {})
    assert derived["total"] == 0


def test_malformed_relational_config_degrades_quietly() -> None:
    schema = EntitySchema(
        fields=[
            FieldSpec("bad_backref", Role.BACKREF, from_="noseparator"),
            FieldSpec("dangling_rollup", Role.ROLLUP, over="missing", agg="count"),
        ]
    )
    derived = compute_derived(ParsedEntity(1, "x", {}, "", []), schema, {})
    assert derived["bad_backref"] == []
    assert derived["dangling_rollup"] is None


def test_non_numeric_ref_and_bool_values_are_ignored() -> None:
    schema = EntitySchema(
        fields=[
            FieldSpec("kids", Role.BACKREF, from_="task.parent"),
            FieldSpec("total", Role.ROLLUP, over="kids", agg="sum", field="pts"),
        ]
    )
    corpus = {
        "task": {
            5: ParsedEntity(5, "task", {"parent": "notanumber", "pts": 3}, "", []),
            6: ParsedEntity(6, "task", {"parent": 1, "pts": True}, "", []),
        }
    }
    derived = compute_derived(ParsedEntity(1, "epic", {}, "", []), schema, corpus)
    assert derived["kids"] == [6]
    assert derived["total"] == 0
