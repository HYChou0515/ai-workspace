"""`source:` table files, read from the workspace the command runs in."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chart_view.sources import SourceError, read_table


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame({"lot": ["A", "B"], "v": [1.5, 2.0]})


def test_a_csv(tmp_path: Path, frame):
    (tmp_path / "data").mkdir()
    frame.to_csv(tmp_path / "data" / "a.csv", index=False)
    assert read_table(tmp_path, "data/a.csv").equals(frame)


def test_a_tsv(tmp_path: Path, frame):
    frame.to_csv(tmp_path / "a.tsv", index=False, sep="\t")
    assert read_table(tmp_path, "a.tsv").equals(frame)


def test_a_parquet(tmp_path: Path, frame):
    frame.to_parquet(tmp_path / "a.parquet", index=False)
    assert read_table(tmp_path, "a.parquet").equals(frame)


def test_a_leading_slash_is_the_workspace_root(tmp_path: Path, frame):
    # View files name sources the way the file tree shows them: `/data/a.csv`.
    (tmp_path / "data").mkdir()
    frame.to_csv(tmp_path / "data" / "a.csv", index=False)
    assert read_table(tmp_path, "/data/a.csv").equals(frame)


def test_a_missing_file_is_named(tmp_path: Path):
    with pytest.raises(SourceError, match="data/nope.csv"):
        read_table(tmp_path, "data/nope.csv")


def test_an_unreadable_file_is_named(tmp_path: Path):
    (tmp_path / "a.parquet").write_text("not parquet")
    with pytest.raises(SourceError, match="a.parquet"):
        read_table(tmp_path, "a.parquet")
