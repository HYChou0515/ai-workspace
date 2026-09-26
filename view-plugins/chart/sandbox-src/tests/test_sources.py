"""`source:` table files, read from the workspace the command runs in."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chart_view.sources import SourceError, read_source, read_table


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


def test_an_entity_source_is_its_records(tmp_path: Path):
    # The platform's own reader (vendored); its parity with the store is pinned
    # in the app's tests/entity/test_local.py.
    (tmp_path / ".entity" / "issue").mkdir(parents=True)
    (tmp_path / ".entity" / "issue" / "schema.yaml").write_text(
        "path: issues\nfields:\n  title: { role: text }\n  points: { role: number }\n"
    )
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "1.md").write_text("---\ntitle: A\npoints: 3\n---\n")
    (tmp_path / "issues" / "2.md").write_text("---\ntitle: B\npoints: 5\n---\n")
    df = read_source(tmp_path, {"entity": "issue"})
    assert df.to_dict("records") == [
        {"number": 1, "title": "A", "points": 3},
        {"number": 2, "title": "B", "points": 5},
    ]


def test_an_unknown_entity_type_is_a_source_error(tmp_path: Path):
    with pytest.raises(SourceError, match="'issue'"):
        read_source(tmp_path, {"entity": "issue"})


def test_a_table_source_goes_through_read_table(tmp_path: Path, frame):
    frame.to_csv(tmp_path / "a.csv", index=False)
    assert read_source(tmp_path, "a.csv").equals(frame)


def test_a_missing_file_is_named(tmp_path: Path):
    with pytest.raises(SourceError, match="data/nope.csv"):
        read_table(tmp_path, "data/nope.csv")


def test_an_unreadable_file_is_named(tmp_path: Path):
    (tmp_path / "a.parquet").write_text("not parquet")
    with pytest.raises(SourceError, match="a.parquet"):
        read_table(tmp_path, "a.parquet")
