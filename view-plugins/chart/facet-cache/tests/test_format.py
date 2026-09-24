from pathlib import Path

from aiws_facet_cache import (
    CategoryScale,
    ContinuousScale,
    Group,
    read_index,
    read_records,
    write_cache,
)


def test_continuous_groups_round_trip_within_half_a_level(tmp_path: Path) -> None:
    scale = ContinuousScale(lo=0.0, hi=10.0)
    groups = [
        Group(key="g1", sort={"rate": 0.5}, values=[0.0, 5.0, 10.0, None]),
        Group(key="g2", sort={"rate": 0.1}, values=[1.0, 2.0, 3.0, 4.0]),
    ]
    path = tmp_path / "c.vcache"
    write_cache(path, scale=scale, cells=4, layout={"x": [0, 1, 0, 1]}, groups=groups)

    index = read_index(path)
    assert [g.key for g in index.groups] == ["g1", "g2"]
    assert [g.sort for g in index.groups] == [{"rate": 0.5}, {"rate": 0.1}]
    assert index.layout == {"x": [0, 1, 0, 1]}

    records = read_records(path, index, 0, 2)
    assert len(records) == 2
    step = (scale.hi - scale.lo) / 254
    for got, group in zip(records, groups):
        for d, v in zip(index.scale.decode(got), group.values):
            if v is None:
                assert d is None
            else:
                assert d is not None and abs(d - v) <= step / 2


def test_category_groups_round_trip_exactly_one_byte_per_cell(tmp_path: Path) -> None:
    scale = CategoryScale(labels=["pass", "fail", "edge"])
    groups = [
        Group(key="w1", sort={}, values=["pass", "fail", None]),
        Group(key="w2", sort={}, values=["edge", "edge", "pass"]),
    ]
    path = tmp_path / "c.vcache"
    write_cache(path, scale=scale, cells=3, layout={}, groups=groups)

    index = read_index(path)
    records = read_records(path, index, 0, 2)
    assert [len(r) for r in records] == [3, 3]
    assert [index.scale.decode(r) for r in records] == [list(g.values) for g in groups]


def test_a_page_is_a_slice_of_the_order_the_groups_were_written_in(tmp_path: Path) -> None:
    scale = ContinuousScale(lo=0.0, hi=254.0)
    groups = [Group(key=f"g{i}", sort={"i": i}, values=[float(i)]) for i in range(100)]
    path = tmp_path / "c.vcache"
    write_cache(path, scale=scale, cells=1, layout={}, groups=groups)

    index = read_index(path)
    page = read_records(path, index, 40, 43)
    assert [index.scale.decode(r) for r in page] == [[40.0], [41.0], [42.0]]
    # a range running off the end is clamped, never padded
    assert len(read_records(path, index, 98, 120)) == 2
