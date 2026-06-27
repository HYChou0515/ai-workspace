"""Tests for the input normalizer (file path | inline records | inline columns)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sci_plot.framework.normalize import normalize


def test_records_list_to_dataframe():
    df = normalize([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
    assert list(df.columns) == ["x", "y"]
    assert df.shape == (2, 2)


def test_column_dict_to_dataframe():
    df = normalize({"a": [1, 2, 3], "b": [4, 5, 6]})
    assert list(df.columns) == ["a", "b"]
    assert df["a"].tolist() == [1, 2, 3]


def test_read_csv(tmp_path: Path):
    p = tmp_path / "d.csv"
    p.write_text("x,y\n1,2\n3,4\n")
    df = normalize(str(p))
    assert df["y"].tolist() == [2, 4]


def test_read_tsv(tmp_path: Path):
    p = tmp_path / "d.tsv"
    p.write_text("x\ty\n1\t2\n")
    df = normalize(str(p))
    assert list(df.columns) == ["x", "y"]


def test_read_json(tmp_path: Path):
    p = tmp_path / "d.json"
    p.write_text('[{"x": 1, "y": 2}, {"x": 3, "y": 4}]')
    df = normalize(str(p))
    assert df.shape == (2, 2)


def test_read_excel(tmp_path: Path):
    p = tmp_path / "d.xlsx"
    pd.DataFrame({"x": [1, 2], "y": [3, 4]}).to_excel(p, index=False)
    df = normalize(str(p))
    assert df["x"].tolist() == [1, 2]


def test_read_parquet(tmp_path: Path):
    p = tmp_path / "d.parquet"
    pd.DataFrame({"x": [1, 2]}).to_parquet(p)
    df = normalize(str(p))
    assert df["x"].tolist() == [1, 2]


def test_file_not_found():
    with pytest.raises(ValueError, match="file not found"):
        normalize("/no/such/file.csv")


def test_unsupported_extension(tmp_path: Path):
    p = tmp_path / "d.xyz"
    p.write_text("whatever")
    with pytest.raises(ValueError, match="unsupported file type"):
        normalize(str(p))


def test_corrupt_file_friendly_error(tmp_path: Path):
    p = tmp_path / "d.parquet"
    p.write_text("not a parquet file")
    with pytest.raises(ValueError, match="could not read"):
        normalize(str(p))


def test_empty_records_list():
    with pytest.raises(ValueError, match="empty list"):
        normalize([])


def test_non_dict_records():
    with pytest.raises(ValueError, match="list of row records"):
        normalize([1, 2, 3])  # type: ignore[list-item]


def test_empty_column_dict():
    with pytest.raises(ValueError, match="empty object"):
        normalize({})


def test_ragged_columns():
    with pytest.raises(ValueError, match="could not build a table"):
        normalize({"a": [1, 2], "b": [3]})


def test_wrong_type():
    with pytest.raises(ValueError, match="must be a file path"):
        normalize(42)  # type: ignore[arg-type]
