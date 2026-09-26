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


@pytest.fixture
def sided() -> pd.DataFrame:
    # `dies` in neutral words, for the tests P42 row 32 wrote
    return pd.DataFrame(
        {
            "group": ["A", "A", "A", "B", "B", "B"],
            "side": ["before", "after", "after", "before", "after", "before"],
            "x": [0, 0, 1, 0, 0, 1],
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


def test_diff_keeps_a_group_only_one_side_has_with_no_mean(sided):
    out = apply_transforms(
        sided,
        [
            {
                "diff": {"by": "side", "of": "after", "minus": "before"},
                "aggregate": [{"op": "mean", "field": "t", "as": "delta"}],
                "groupby": ["group", "x"],
            }
        ],
    )
    # (A,1) has only "after", (B,1) only "before": both are kept (P42 row 32),
    # and a mean of no row is missing, so their difference is too.
    rows = {(g, x): d for g, x, d in zip(out["group"], out["x"], out["delta"], strict=True)}
    assert rows.keys() == {("A", 0), ("A", 1), ("B", 0), ("B", 1)}
    assert (rows["A", 0], rows["B", 0]) == (1.0, 2.0)
    assert math.isnan(rows["A", 1]) and math.isnan(rows["B", 1])


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


# #847/#848 PR 5 P42 row 32: `diff` keeps a group either side has -- 0 on the
# missing side for count and sum, empty (missing) otherwise. The oracle is
# pandas' own `Series.sub`, with `fill_value=0` for count and sum.
_SIDES = {"group": ["A", "A", "B", "A", "B", "B"], "bin": ["x", "y", "x", "y", "x", "z"]}


def _sides(categorical: bool) -> pd.DataFrame:
    df = pd.DataFrame({**_SIDES, "v": [1.0, 2.0, 4.0, 8.0, None, 16.0]})
    if categorical:  # as a parquet file keeps them
        df = df.astype({"group": "category", "bin": "category"})
    return df


def _oracle(df: pd.DataFrame, op: str, field: str | None) -> dict[str, float]:
    def side(group: str) -> pd.Series:
        grouped = df[df["group"] == group].groupby("bin", observed=True)
        return grouped.size() if field is None else getattr(grouped[field], op)()

    fill = 0 if op in ("count", "sum") else None
    return side("A").sub(side("B"), fill_value=fill).to_dict()


@pytest.mark.parametrize("categorical", [False, True], ids=["text", "categorical"])
@pytest.mark.parametrize(
    ("op", "field"), [("count", None), ("count", "v"), ("sum", "v"), ("mean", "v"), ("max", "v")]
)
def test_diff_keeps_a_group_either_side_has(categorical, op, field):
    df = _sides(categorical)
    item = {"op": op, "as": "d", **({"field": field} if field else {})}
    out = apply_transforms(
        df,
        [
            {
                "diff": {"by": "group", "of": "A", "minus": "B"},
                "aggregate": [item],
                "groupby": ["bin"],
            }
        ],
    )
    got = dict(zip(out["bin"].astype(str), out["d"], strict=True))
    want = {str(k): v for k, v in _oracle(df, op, field).items()}
    assert sorted(got) == sorted(want) == ["x", "y", "z"]
    for k in want:
        assert got[k] == pytest.approx(want[k], nan_ok=True), k


def test_diff_of_a_count_keeps_whole_numbers():
    # a count over a side with no row there is 0, not a float the fill made
    out = apply_transforms(
        _sides(False),
        [
            {
                "diff": {"by": "group", "of": "A", "minus": "B"},
                "aggregate": [{"op": "count", "as": "n"}],
                "groupby": ["bin"],
            }
        ],
    )
    assert out.to_dict("records") == [
        {"bin": "x", "n": -1},
        {"bin": "y", "n": 2},
        {"bin": "z", "n": -1},
    ]
    assert out["n"].dtype.kind == "i"


def test_diff_of_a_sum_over_mixed_numbers_keeps_the_fraction():
    # one side sums whole numbers, the other 2.5: the 0 filled in is not a
    # reason to read the other side as whole numbers
    v = pd.Series([1, 2.5, 3], dtype=object)  # as an entity field holds them
    df = pd.DataFrame({"group": ["A", "B", "B"], "bin": ["x", "x", "y"], "v": v})
    assert pd.to_numeric(df["v"][:1]).dtype.kind == "i"  # side A alone is whole
    out = apply_transforms(
        df,
        [
            {
                "diff": {"by": "group", "of": "A", "minus": "B"},
                "aggregate": [{"op": "sum", "field": "v", "as": "d"}],
                "groupby": ["bin"],
            }
        ],
    )
    assert out.to_dict("records") == [{"bin": "x", "d": -1.5}, {"bin": "y", "d": -3.0}]


def test_diff_without_groupby_counts_a_side_with_no_row_as_zero(sided):
    t = {
        "diff": {"by": "side", "of": "after", "minus": "never"},
        "aggregate": [{"op": "count", "as": "n"}, {"op": "mean", "field": "t", "as": "m"}],
    }
    out = apply_transforms(sided, [t])
    assert out["n"].tolist() == [3]
    assert math.isnan(out["m"].iloc[0])


# #847/#848 PR 5 P44 row 38: a difference is taken in a signed type. Unsigned
# sums were subtracted in their own dtype and wrapped (uint32 0 - 7 =
# 4294967289); min / max alike. The oracle is the same aggregate in Python's
# own integers.
_UNSIGNED = {"side": ["after", "before", "before", "after"], "group": ["a", "a", "b", "c"]}
_VALUES = [1, 5, 7, 2]


def _unsigned_oracle(op: str) -> dict[str, float]:
    rows = list(zip(_UNSIGNED["side"], _UNSIGNED["group"], _VALUES, strict=True))

    def side(name: str) -> dict[str, int]:
        out: dict[str, list[int]] = {}
        for s, g, v in rows:
            if s == name:
                out.setdefault(g, []).append(v)
        return {g: {"sum": sum, "min": min, "max": max}[op](vs) for g, vs in out.items()}

    after, before = side("after"), side("before")
    fill = 0 if op == "sum" else math.nan
    return {g: after.get(g, fill) - before.get(g, fill) for g in sorted({*after, *before})}


@pytest.mark.parametrize("dtype", ["uint8", "uint32", "uint64", "UInt32", "UInt64"])
@pytest.mark.parametrize("op", ["sum", "min", "max"])
def test_diff_of_unsigned_numbers_is_signed(dtype, op):
    df = pd.DataFrame({**_UNSIGNED, "value": pd.array(_VALUES, dtype=dtype)})
    out = apply_transforms(
        df,
        [
            {
                "diff": {"by": "side", "of": "after", "minus": "before"},
                "aggregate": [{"op": op, "field": "value", "as": "d"}],
                "groupby": ["group"],
            }
        ],
    )
    got = dict(zip(out["group"], out["d"].astype("float64"), strict=True))
    want = _unsigned_oracle(op)
    assert sorted(got) == sorted(want)
    for g, v in want.items():
        assert got[g] == pytest.approx(v, nan_ok=True), g
    assert out["d"].dtype.kind != "u"
    # a nullable column stays nullable
    nullable = pd.api.extensions.ExtensionDtype
    assert isinstance(out["d"].dtype, nullable) == isinstance(df["value"].dtype, nullable)


@pytest.mark.parametrize("low", [5, 2**63 + 2], ids=["one-side-past", "both-past"])
@pytest.mark.parametrize("dtype", ["uint64", "UInt64"])
def test_diff_of_unsigned_numbers_past_a_signed_integer_is_a_float(dtype, low):
    # 2**63 + 10 has no int64: the difference is a float, not wrapped
    df = pd.DataFrame(
        {"side": ["after", "before"], "value": pd.array([2**63 + 10, low], dtype=dtype)}
    )
    for of, minus, want in [
        ("after", "before", float(2**63 + 10) - float(low)),
        ("before", "after", float(low) - float(2**63 + 10)),
    ]:
        out = apply_transforms(
            df,
            [
                {
                    "diff": {"by": "side", "of": of, "minus": minus},
                    "aggregate": [{"op": "min", "field": "value", "as": "d"}],
                }
            ],
        )
        assert out["d"].dtype == ("Float64" if dtype == "UInt64" else "float64")
        assert out["d"].tolist() == [want]


def test_diff_of_unsigned_numbers_int64_holds_stays_whole():
    # int64's largest value still fits: the difference stays an integer
    top = 2**63 - 1
    df = pd.DataFrame({"side": ["after", "before"], "value": pd.array([top, 5], dtype="uint64")})
    out = apply_transforms(
        df,
        [
            {
                "diff": {"by": "side", "of": "before", "minus": "after"},
                "aggregate": [{"op": "max", "field": "value", "as": "d"}],
            }
        ],
    )
    assert out["d"].dtype == "int64"
    assert out["d"].tolist() == [5 - top]
