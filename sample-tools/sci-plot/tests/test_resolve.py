"""Tests for role resolution: explicit / infer / ask + liberal coercion."""

from __future__ import annotations

import pandas as pd
import pytest

from sci_plot.framework.resolve import AskNeeded, Resolved, resolve
from sci_plot.framework.roles import Role, RoleKind

GROUP = Role("group", RoleKind.CATEGORY, required=True)
Y = Role("y", RoleKind.NUMBER, required=True)


def test_explicit_roles_resolve_and_coerce_strings():
    df = pd.DataFrame({"g": ["a", "b"], "v": ["1.5", "2.5"]})
    res = resolve(df, (GROUP, Y), {"group": "g", "y": "v"})
    assert isinstance(res, Resolved)
    assert res.roles == {"group": "g", "y": "v"}
    assert res.df["v"].tolist() == [1.5, 2.5]  # numeric-ish strings coerced


def test_infer_unambiguous_single_candidate_each():
    df = pd.DataFrame({"g": ["a", "b"], "v": [1, 2]})
    res = resolve(df, (GROUP, Y), {})
    assert isinstance(res, Resolved)
    assert res.roles == {"group": "g", "y": "v"}


def test_ambiguous_numeric_asks():
    df = pd.DataFrame({"n1": [1, 2], "n2": [3, 4]})
    res = resolve(df, (GROUP, Y), {})
    assert isinstance(res, AskNeeded)
    by_role = {it.role: it for it in res.items}
    assert by_role["y"].reason == "ambiguous"
    assert set(by_role["y"].candidates) == {"n1", "n2"}
    assert by_role["group"].reason == "missing"  # no non-numeric column
    assert {c.name for c in res.available} == {"n1", "n2"}


def test_optional_role_omitted_is_left_unset():
    hue = Role("hue", RoleKind.CATEGORY, required=False)
    df = pd.DataFrame({"g": ["a", "b"], "v": [1, 2]})
    res = resolve(df, (GROUP, Y, hue), {})
    assert isinstance(res, Resolved)
    assert "hue" not in res.roles  # optional + no candidate → silently unset


def test_explicit_missing_column_raises():
    df = pd.DataFrame({"g": ["a"], "v": [1]})
    with pytest.raises(ValueError, match="column 'nope' not found"):
        resolve(df, (GROUP, Y), {"group": "nope", "y": "v"})


def test_used_columns_not_double_picked():
    # Two numeric roles, one numeric column → first takes it, second asks.
    y2 = Role("y2", RoleKind.NUMBER, required=True)
    df = pd.DataFrame({"g": ["a", "b"], "v": [1, 2]})
    res = resolve(df, (Y, y2, GROUP), {})
    assert isinstance(res, AskNeeded)
    asked = {it.role for it in res.items}
    assert "y2" in asked  # the single numeric col was used by `y`


def test_int_coercion():
    role = Role("die", RoleKind.INT, required=True)
    df = pd.DataFrame({"die": ["1", "2", "3"]})
    res = resolve(df, (role,), {"die": "die"})
    assert isinstance(res, Resolved)
    assert res.df["die"].tolist() == [1, 2, 3]
    assert str(res.df["die"].dtype) == "Int64"


def test_datetime_coercion_and_inference():
    role = Role("t", RoleKind.DATETIME, required=True)
    df = pd.DataFrame({"t": ["2026-01-01", "2026-01-02"], "n": [1, 2]})
    res = resolve(df, (role,), {})
    assert isinstance(res, Resolved)
    assert res.roles == {"t": "t"}  # the date column inferred over the numeric one
    assert pd.api.types.is_datetime64_any_dtype(res.df["t"])


def test_multi_role_explicit_list():
    levels = Role("levels", RoleKind.ANY, required=True, multi=True)
    df = pd.DataFrame({"a": [1], "b": [2], "v": [3]})
    res = resolve(df, (levels,), {"levels": ["a", "b"]})
    assert isinstance(res, Resolved)
    assert res.roles == {"levels": ["a", "b"]}


def test_multi_role_single_string_is_wrapped():
    levels = Role("levels", RoleKind.ANY, required=True, multi=True)
    df = pd.DataFrame({"a": [1], "v": [3]})
    res = resolve(df, (levels,), {"levels": "a"})
    assert isinstance(res, Resolved)
    assert res.roles == {"levels": ["a"]}


def test_multi_role_omitted_asks():
    levels = Role("levels", RoleKind.ANY, required=True, multi=True)
    df = pd.DataFrame({"a": [1], "b": [2]})
    res = resolve(df, (levels,), {})
    assert isinstance(res, AskNeeded)
    assert res.items[0].role == "levels"
    assert res.items[0].reason == "missing"


def test_any_role_single_column_inferred():
    role = Role("label", RoleKind.ANY, required=True)
    df = pd.DataFrame({"only": ["a", "b"]})
    res = resolve(df, (role,), {})
    assert isinstance(res, Resolved)
    assert res.roles == {"label": "only"}


def test_datetime_already_datetime_dtype_inferred():
    role = Role("t", RoleKind.DATETIME, required=True)
    df = pd.DataFrame({"t": pd.to_datetime(["2026-01-01", "2026-01-02"]), "n": [1, 2]})
    res = resolve(df, (role,), {})
    assert isinstance(res, Resolved)
    assert res.roles == {"t": "t"}
