"""The facet build writes the bytes it wrote before P33 (#847/#848), and does
its per-build work once.

P33 moved work out of the build's per-column loop (the column list's "one
value per group" test) and stopped reading each facet column twice. The
golden digests below are of caches written by the code as it stood at
bec9ccae (random build id left out), so a change that moves any byte of the
header, the records or the exact values reddens here."""

from __future__ import annotations

import datetime as dt
import hashlib
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from chart_view.facet import build as B
from chart_view.facet.build import build_facet_cache


def _frame() -> pd.DataFrame:
    """36 groups on a 4 x 3 lattice: text / number / date / zoned / nullable /
    string / mixed-object columns, one value per group or several, missing
    values here and there, and rows with no x (left out of every tile)."""
    rng = np.random.default_rng(11)
    rows: list[dict[str, Any]] = []
    for g in range(36):
        group, item = f"G{g % 4}", g // 4 + 1
        label = f"t{rng.integers(0, 9):02d}"
        score = None if g % 7 == 3 else float(rng.integers(0, 6))
        when = pd.Timestamp("2026-01-01") + dt.timedelta(days=int(rng.integers(0, 30)))
        for x in range(4):
            for y in range(3):
                rows.append(
                    {
                        "group": group,
                        "item": item,
                        "x": x,
                        "y": y,
                        "v": None if rng.random() < 0.1 else float(rng.integers(0, 50)) / 4,
                        "n": int(rng.integers(-5, 5)),
                        "tag": None if rng.random() < 0.1 else str(rng.choice(["A", "B", "C"])),
                        "label": label,
                        "score": score,
                        "when": when,
                        "stamp": when + dt.timedelta(hours=int(rng.integers(0, 90))),
                        "same": -0.0 if (x + y) % 2 else 0.0,  # one text, two reprs
                    }
                )
        # a row off the map: counted in no tile, so "label" stays one per group
        rows.append({**rows[-1], "x": None, "v": 1e4, "tag": "Z", "label": "off"})
    frame = pd.DataFrame(rows)
    frame["zoned"] = frame["when"].dt.tz_localize("Asia/Taipei")
    frame["count"] = frame["n"].astype("Int64").mask(frame["n"] == 0)
    frame["text"] = frame["label"].astype("string")
    frame["mixed"] = pd.Series([1 if i % 3 else "1" for i in range(len(frame))], dtype=object)
    return frame


def _mixed_facet() -> pd.DataFrame:
    frame = _frame()
    frame["group"] = frame["group"].map({"G0": 1, "G1": "a", "G2": True, "G3": 2.5})
    return frame.astype({"group": object})


def _shuffled() -> pd.DataFrame:
    return _frame().sample(frac=1, random_state=3).reset_index(drop=True)


def _string_facet() -> pd.DataFrame:
    frame = _frame()
    return frame.astype({"group": "string"})


VARIANTS: dict[str, tuple[Any, dict[str, Any]]] = {
    "number": (_frame, {}),
    "number-quantitative-axes": (_frame, {"x_type": "quantitative", "y_type": "quantitative"}),
    "sort-one-per-group": (_frame, {"sort": ["label"]}),
    "sort-by-a-statistic": (_frame, {"sort": ["v"], "stat": "max"}),
    "sort-by-a-date-statistic": (_frame, {"sort": ["stamp"], "stat": "min"}),
    "category-value": (_frame, {"value": "tag"}),
    "zoned-facet": (_frame, {"facet": ["zoned", "group", "item"]}),
    "row-path-facet": (_mixed_facet, {"sort": ["v"], "stat": "mean"}),
    "string-facet": (_string_facet, {}),
    # "0" keys both zeros, whose reprs differ: that facet column is not single
    "float-facet-both-zeros": (_frame, {"facet": ["group", "item", "same"]}),
    # 1 and "1" key alike, and are two sort values: only the row path reads it
    "mixed-facet-1-and-text-1": (_frame, {"facet": ["group", "item", "mixed"]}),
    # a group's rows scattered through the file
    "rows-shuffled": (_shuffled, {"sort": ["v"], "stat": "median"}),
}

GOLDEN = {
    "number": "01069fa4974e595425fa0fa393f37a45c8e4c275e8a1e35e43bab3f4c2711de9",
    "number-quantitative-axes": "93cc1ea67e6aa418b7183dac649a4cd0fe3a907d4cf4efdff57ab6ffd35769c4",
    "sort-one-per-group": "a064cef7d00870fb41c5a7d38e325d86b20a7dff9abca0b761cfea4c118db2f7",
    "sort-by-a-statistic": "b33c0960dce82cacc2103ebb4c3af388cfd259cd027c105446423cdce8dc3ac6",
    "sort-by-a-date-statistic": "390660910b6e6615ec29fba5b84d251cab6fd8f291f282421334468982693f28",
    "category-value": "b7a796f145e01434dc79a7dc87f50abcdb7d6e02574439bb8f967da1bc348912",
    "zoned-facet": "bf4e0e5b03e0bbd2d0b96ed486eda2cb4d35dae1c7bd5655fdc351b65afa0c5b",
    "row-path-facet": "d42ffca22c1b0e9b9e38079d058907c9de0521049f2244f4bed827eee13ce258",
    "string-facet": "01069fa4974e595425fa0fa393f37a45c8e4c275e8a1e35e43bab3f4c2711de9",
    "float-facet-both-zeros": "0e944be0b2f9d414eee7bbe4bfa470c34d22378b781096a26533179f46e54f66",
    "mixed-facet-1-and-text-1": "e6dbfab5fefb161d2d27a2e4a70a5090ccab8ec1aa203a5cbff788a953ec6834",
    "rows-shuffled": "a4ec9d96b866faeabd6a110126bf9819c857e7e1af7e63bd2b4969da92f758e5",
}


def _write(tmp_path: Path, name: str) -> bytes:
    make, kwargs = VARIANTS[name]
    path = tmp_path / f"{name}.vcache"
    args: dict[str, Any] = {"facet": ["group", "item"], "value": "v", **kwargs}
    build_facet_cache(make(), x="x", y="y", path=path, **args)
    data = path.read_bytes()
    return data[:8] + data[40:]  # the build id (32 hex chars after the magic) is random


@pytest.mark.parametrize("name", list(VARIANTS))
def test_the_cache_is_the_one_written_before(tmp_path: Path, name: str) -> None:
    assert hashlib.sha256(_write(tmp_path, name)).hexdigest() == GOLDEN[name]


def _count_calls(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, where: str) -> list[int]:
    """A counter of calls to ``owner.name`` made from the module file ``where``."""
    calls: list[int] = []
    real = getattr(owner, name)

    def spy(*args: Any, **kwargs: Any) -> Any:
        if sys._getframe(1).f_code.co_filename.endswith(where):
            calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, spy)
    return calls


def test_each_facet_column_is_read_once_per_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A facet column's labels were worked out by the whole-column path and
    again to place each row in its group: ~0.3 s per 10M text rows, twice."""
    calls = _count_calls(monkeypatch, B, "_labels", "facet/build.py")
    _write(tmp_path, "sort-by-a-statistic")  # a number colour: only the two facet columns
    assert len(calls) == 2


def test_the_rows_are_put_in_tile_order_once_per_build_not_once_per_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The column list asks of every column whether each tile holds one value
    of it; the tiles' row order is the same for all of them (a stable argsort
    of 10M rows per column was ~0.1 s each)."""
    calls = _count_calls(monkeypatch, np, "argsort", "facet/build.py")
    frame = _frame()
    build_facet_cache(
        frame, facet=["group", "item"], x="x", y="y", value="v", path=tmp_path / "a.vcache"
    )
    few = len(calls)
    calls.clear()
    wide = frame.assign(**{f"extra{i}": frame["n"] + i for i in range(5)})
    build_facet_cache(
        wide, facet=["group", "item"], x="x", y="y", value="v", path=tmp_path / "b.vcache"
    )
    assert few > 0 and len(calls) == few


@pytest.mark.parametrize("name", ["number", "string-facet"])
def test_a_text_facet_column_is_one_per_group_without_being_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A group IS one text of each facet column, and a text column's text is
    its value: asking cost a factorize of the column (~0.4 s per 10M rows), or,
    for a string dtype, a Python call per row. A number facet column is still
    asked: "0" keys 0.0 and -0.0, which are two sort values."""
    asked: list[str] = []
    real = B._single

    def spy(column: pd.Series, *args: Any) -> bool:
        asked.append(str(column.name))
        return real(column, *args)

    monkeypatch.setattr(B, "_single", spy)
    _write(tmp_path, name)
    assert "group" not in asked and "item" in asked


if __name__ == "__main__":  # print the digests (run once, at the code the goldens pin)
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        for name in VARIANTS:
            print(f'    "{name}": "{hashlib.sha256(_write(Path(d), name)).hexdigest()}",')
    sys.exit(0)
