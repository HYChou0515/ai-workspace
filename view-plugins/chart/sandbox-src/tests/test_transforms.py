"""`transform:` over a frame — what a chart's rows are before any mark sees them."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from chart_view.transforms import TransformError, apply_transforms


@pytest.fixture
def dies() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "lot": ["A", "A", "A", "B", "B", "B"],
            "phase": ["before", "after", "after", "before", "after", "before"],
            "x": [0, 0, 1, 0, 0, 1],
            "fail": [0, 1, 1, 0, 0, 1],
            "t": [1.0, 2.0, 4.0, 3.0, 5.0, 7.0],
        }
    )


def test_no_transforms_is_the_frame_itself(dies):
    assert apply_transforms(dies, []).equals(dies)


def test_a_string_filter_is_a_pandas_query(dies):
    out = apply_transforms(dies, [{"filter": "lot == 'B' and t > 3"}])
    assert out["t"].tolist() == [5.0, 7.0]


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ({"equal": "A"}, [1.0, 2.0, 4.0]),
        ({"oneOf": ["B"]}, [3.0, 5.0, 7.0]),
    ],
)
def test_a_field_predicate_on_a_category(dies, predicate, expected):
    out = apply_transforms(dies, [{"filter": {"field": "lot", **predicate}}])
    assert out["t"].tolist() == expected


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ({"range": [2, 5]}, [2.0, 4.0, 3.0, 5.0]),
        ({"lt": 3}, [1.0, 2.0]),
        ({"lte": 3}, [1.0, 2.0, 3.0]),
        ({"gt": 5}, [7.0]),
        ({"gte": 5}, [5.0, 7.0]),
        ({"gt": 1, "lt": 4}, [2.0, 3.0]),
    ],
)
def test_a_field_predicate_on_a_number(dies, predicate, expected):
    out = apply_transforms(dies, [{"filter": {"field": "t", **predicate}}])
    assert out["t"].tolist() == expected


def test_valid_keeps_rows_with_a_value_and_its_negation_the_rest():
    df = pd.DataFrame({"note": ["x", None, "y", float("nan")]})
    kept = apply_transforms(df, [{"filter": {"field": "note", "valid": True}}])
    dropped = apply_transforms(df, [{"filter": {"field": "note", "valid": False}}])
    assert kept["note"].tolist() == ["x", "y"]
    assert len(dropped) == 2


def test_filters_apply_in_order(dies):
    out = apply_transforms(dies, [{"filter": {"field": "lot", "equal": "A"}}, {"filter": "t > 1"}])
    assert out["t"].tolist() == [2.0, 4.0]


def test_aggregate_ops_over_groupby(dies):
    out = apply_transforms(
        dies,
        [
            {
                "aggregate": [
                    {"op": "mean", "field": "t", "as": "mean_t"},
                    {"op": "sum", "field": "t", "as": "sum_t"},
                    {"op": "min", "field": "t", "as": "min_t"},
                    {"op": "max", "field": "t", "as": "max_t"},
                    {"op": "count", "as": "n"},
                    {"op": "rate", "field": "fail", "as": "fail_rate"},
                ],
                "groupby": ["lot"],
            }
        ],
    )
    rows = out.set_index("lot").to_dict("index")
    assert rows["A"] == {
        "mean_t": pytest.approx(7 / 3),
        "sum_t": 7.0,
        "min_t": 1.0,
        "max_t": 4.0,
        "n": 3,
        "fail_rate": pytest.approx(2 / 3),
    }
    assert rows["B"]["fail_rate"] == pytest.approx(1 / 3)
    assert list(out.columns) == ["lot", "mean_t", "sum_t", "min_t", "max_t", "n", "fail_rate"]


def test_rate_is_the_share_of_truthy_rows_over_every_row_in_the_group():
    df = pd.DataFrame({"g": ["a"] * 4, "ok": [True, False, True, None]})
    out = apply_transforms(
        df, [{"aggregate": [{"op": "rate", "field": "ok", "as": "r"}], "groupby": ["g"]}]
    )
    assert out["r"].tolist() == [0.5]


def test_count_with_a_field_counts_its_values_not_the_rows():
    df = pd.DataFrame({"g": ["a", "a", "a"], "v": [1.0, None, 3.0]})
    out = apply_transforms(
        df, [{"aggregate": [{"op": "count", "field": "v", "as": "n"}], "groupby": ["g"]}]
    )
    assert out["n"].tolist() == [2]


def test_aggregate_without_groupby_is_one_row(dies):
    out = apply_transforms(dies, [{"aggregate": [{"op": "count", "as": "n"}]}])
    assert out.to_dict("records") == [{"n": 6}]


def test_a_group_key_that_is_missing_is_kept_as_its_own_group():
    df = pd.DataFrame({"g": ["a", None, None], "v": [1, 2, 3]})
    out = apply_transforms(
        df, [{"aggregate": [{"op": "sum", "field": "v", "as": "s"}], "groupby": ["g"]}]
    )
    assert sorted(out["s"].tolist()) == [1, 5]


def test_diff_subtracts_the_same_aggregate_over_two_groups(dies):
    out = apply_transforms(
        dies,
        [
            {
                "diff": {"by": "phase", "of": "after", "minus": "before"},
                "aggregate": [{"op": "mean", "field": "t", "as": "delta"}],
                "groupby": ["lot"],
            }
        ],
    )
    rows = dict(zip(out["lot"], out["delta"], strict=True))
    # A: after mean(2,4)=3, before 1 → 2.   B: after 5, before mean(3,7)=5 → 0.
    assert rows == {"A": 2.0, "B": 0.0}


def test_diff_drops_a_group_that_only_one_side_has(dies):
    out = apply_transforms(
        dies,
        [
            {
                "diff": {"by": "phase", "of": "after", "minus": "before"},
                "aggregate": [{"op": "mean", "field": "t", "as": "delta"}],
                "groupby": ["lot", "x"],
            }
        ],
    )
    # (A,1) has only "after", (B,1) only "before": neither has a difference.
    assert sorted(zip(out["lot"], out["x"], out["delta"], strict=True)) == [
        ("A", 0, 1.0),
        ("B", 0, 2.0),
    ]


def test_diff_matches_numbers_written_as_numbers():
    df = pd.DataFrame({"run": [1, 2, 1, 2], "v": [1.0, 5.0, 2.0, 8.0], "g": ["a", "a", "b", "b"]})
    out = apply_transforms(
        df,
        [
            {
                "diff": {"by": "run", "of": 2, "minus": 1},
                "aggregate": [{"op": "sum", "field": "v", "as": "d"}],
                "groupby": ["g"],
            }
        ],
    )
    assert dict(zip(out["g"], out["d"], strict=True)) == {"a": 4.0, "b": 6.0}


def test_an_unknown_column_is_named(dies):
    with pytest.raises(TransformError, match="'thickness'"):
        apply_transforms(dies, [{"filter": {"field": "thickness", "gt": 1}}])
    with pytest.raises(TransformError, match="'wafer'"):
        apply_transforms(dies, [{"aggregate": [{"op": "count", "as": "n"}], "groupby": ["wafer"]}])
    with pytest.raises(TransformError, match="'yield'"):
        apply_transforms(
            dies, [{"aggregate": [{"op": "mean", "field": "yield", "as": "m"}], "groupby": []}]
        )
    with pytest.raises(TransformError, match="'step'"):
        apply_transforms(
            dies,
            [
                {
                    "diff": {"by": "step", "of": 1, "minus": 2},
                    "aggregate": [{"op": "count", "as": "n"}],
                }
            ],
        )


def test_a_query_that_does_not_evaluate_says_so(dies):
    with pytest.raises(TransformError, match="filter"):
        apply_transforms(dies, [{"filter": "no_such_column > 1"}])
    with pytest.raises(TransformError, match="filter"):
        apply_transforms(dies, [{"filter": "t >"}])


def test_a_query_must_select_rows_not_compute_values(dies):
    with pytest.raises(TransformError, match="true/false"):
        apply_transforms(dies, [{"filter": "t + 1"}])


def test_diff_without_groupby_is_one_row(dies):
    out = apply_transforms(
        dies,
        [
            {
                "diff": {"by": "phase", "of": "after", "minus": "before"},
                "aggregate": [{"op": "sum", "field": "fail", "as": "d"}],
            }
        ],
    )
    # after: 1 + 1 + 0 = 2; before: 0 + 0 + 1 = 1.
    assert out.to_dict("records") == [{"d": 1}]


def test_aggregate_without_groupby_over_no_rows_is_no_row():
    out = apply_transforms(pd.DataFrame({"v": []}), [{"aggregate": [{"op": "count", "as": "n"}]}])
    assert out.empty and list(out.columns) == ["n"]


def test_mean_of_an_all_missing_group_is_missing_not_an_error():
    df = pd.DataFrame({"g": ["a"], "v": [None]}, dtype=object)
    out = apply_transforms(
        df, [{"aggregate": [{"op": "mean", "field": "v", "as": "m"}], "groupby": ["g"]}]
    )
    assert math.isnan(out["m"].iloc[0])
