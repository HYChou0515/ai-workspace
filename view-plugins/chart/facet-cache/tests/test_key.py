from dataclasses import replace
from pathlib import Path

import pytest

from aiws_facet_cache import CacheKey, cache_file, transform_hash

BASE = CacheKey(
    source_path="data/wafers.csv",
    size=1024,
    mtime_ns=1_700_000_000_000_000_000,
    transform_hash=transform_hash({"groupby": ["wafer"], "aggregate": "mean"}),
)


@pytest.mark.parametrize(
    "changed",
    [
        replace(BASE, source_path="data/wafers2.csv"),
        replace(BASE, size=1025),
        replace(BASE, mtime_ns=BASE.mtime_ns + 1),
        replace(BASE, transform_hash=transform_hash({"groupby": ["lot"]})),
    ],
    ids=["path", "size", "mtime", "transform"],
)
def test_every_key_component_changes_the_cache_file(changed: CacheKey) -> None:
    root = Path("/cache")
    assert cache_file(root, changed) != cache_file(root, BASE)


def test_the_same_key_is_the_same_file_and_lives_under_the_root() -> None:
    root = Path("/cache")
    again = replace(BASE)
    assert cache_file(root, again) == cache_file(root, BASE)
    assert cache_file(root, BASE).parent == root


def test_transform_hash_ignores_key_order_but_not_values() -> None:
    a = transform_hash({"groupby": ["x", "y"], "aggregate": "mean"})
    b = transform_hash({"aggregate": "mean", "groupby": ["x", "y"]})
    c = transform_hash({"aggregate": "mean", "groupby": ["y", "x"]})
    assert a == b
    assert a != c
