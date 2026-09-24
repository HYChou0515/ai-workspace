"""The pager (plan-view-plugins-pr4 P4): what the gallery asks the sandbox for,
answered from the cache in PR 2's wire shapes so one decoder and one raster
core serve both the thumbnail and the full view."""

import base64
import os
from pathlib import Path

import pytest

from chart_view.facet import (
    CacheKey,
    CacheUnusable,
    CategoryScale,
    ContinuousScale,
    Group,
    cache_file,
    write_cache,
)
from chart_view.facet.pager import StaleIndex, exact_payload, index_payload, page_payload

KEY = CacheKey(source_path="d.csv", size=1, mtime_ns=1, transform_hash="t")


def _build(root: Path, scale=None, values=None) -> str:
    scale = scale or ContinuousScale(0.0, 254.0)
    values = values or [[float(i), float(i) + 1] for i in range(5)]
    write_cache(
        cache_file(root, KEY),
        scale=scale,
        facet=("lot", "wafer"),
        cells=2,
        layout={"x": [0, 1], "y": [0, 0]},
        groups=[
            Group(key=("L1", f"W{i}"), sort={"rate": i / 10}, values=v)
            for i, v in enumerate(values)
        ],
    )
    return KEY.digest()


def test_the_index_payload_is_what_the_gallery_sorts_and_marks_from(tmp_path: Path) -> None:
    digest = _build(tmp_path)
    got = index_payload(tmp_path, digest)
    assert got["facet"] == ["lot", "wafer"]
    assert got["cells"] == 2
    assert got["layout"] == {"x": [0, 1], "y": [0, 0]}
    assert got["groups"][3] == {"key": ["L1", "W3"], "sort": {"rate": 0.3}}
    assert isinstance(got["build"], str) and len(got["build"]) == 32


def test_a_page_is_one_q8_column_per_group_in_the_order_asked(tmp_path: Path) -> None:
    digest = _build(tmp_path)
    build = index_payload(tmp_path, digest)["build"]
    got = page_payload(tmp_path, digest, build, [4, 0])
    assert [g["kind"] for g in got["groups"]] == ["q8", "q8"]
    assert got["groups"][0]["min"] == 0.0 and got["groups"][0]["max"] == 254.0
    assert [base64.b64decode(g["codes"]) for g in got["groups"]] == [bytes([4, 5]), bytes([0, 1])]


def test_a_category_page_is_one_byte_cat_column_per_group(tmp_path: Path) -> None:
    digest = _build(
        tmp_path,
        scale=CategoryScale(["pass", "fail"]),
        values=[
            ["pass", "fail"],
            ["fail", None],
            ["pass", "pass"],
            ["fail", "fail"],
            ["pass", None],
        ],
    )
    build = index_payload(tmp_path, digest)["build"]
    (group,) = page_payload(tmp_path, digest, build, [1])["groups"]
    assert group["kind"] == "cat" and group["levels"] == ["pass", "fail"] and group["width"] == 1
    assert base64.b64decode(group["codes"]) == bytes([1, 255])


def test_a_page_asked_with_an_older_build_is_stale_never_served(tmp_path: Path) -> None:
    """The browser sorted its positions from ITS index: after a rebuild those
    positions may name other groups, so the gallery must refetch the index."""
    digest = _build(tmp_path)
    old = index_payload(tmp_path, digest)["build"]
    _build(tmp_path)
    with pytest.raises(StaleIndex):
        page_payload(tmp_path, digest, old, [0])
    with pytest.raises(StaleIndex):
        exact_payload(tmp_path, digest, old, 0)


@pytest.mark.parametrize("read", ["page", "exact"])
def test_a_rebuild_between_the_build_check_and_the_read_is_stale_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read: str
) -> None:
    """The build check and the record read open the file separately: a rebuild
    landing between them must still tell the gallery to refetch, not the command
    to rebuild (which would mint yet another build and fail the page again)."""
    import chart_view.facet.pager as pager

    digest = _build(tmp_path)
    build = index_payload(tmp_path, digest)["build"]
    real = pager.read_index

    def index_then_rebuild(path: Path):  # type: ignore[no-untyped-def]
        index = real(path)
        _build(tmp_path)  # lands after the check, before the record read
        return index

    monkeypatch.setattr(pager, "read_index", index_then_rebuild)
    with pytest.raises(StaleIndex):
        if read == "page":
            page_payload(tmp_path, digest, build, [0])
        else:
            exact_payload(tmp_path, digest, build, 0)


@pytest.mark.parametrize("read", ["page", "exact"])
def test_a_cache_cut_short_in_the_same_build_is_rebuilt_not_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read: str
) -> None:
    """Refetching the index would find the same broken build: this one must go
    to the command's rebuild path (CacheUnusable), never StaleIndex. Cut after
    the build check, so it is the record read that finds it."""
    import chart_view.facet.pager as pager

    digest = _build(tmp_path)
    build = index_payload(tmp_path, digest)["build"]
    path = cache_file(tmp_path, KEY)
    real = pager.read_index

    def index_then_cut(p: Path):  # type: ignore[no-untyped-def]
        index = real(p)
        path.write_bytes(path.read_bytes()[:-3])
        return index

    monkeypatch.setattr(pager, "read_index", index_then_cut)
    with pytest.raises(CacheUnusable) as caught:
        if read == "page":
            page_payload(tmp_path, digest, build, [0])
        else:
            exact_payload(tmp_path, digest, build, 0)
    assert type(caught.value) is CacheUnusable  # not its CacheRebuilt subclass


def test_a_corrupt_build_id_is_unusable_not_a_decode_error(tmp_path: Path) -> None:
    digest = _build(tmp_path)
    path = cache_file(tmp_path, KEY)
    data = bytearray(path.read_bytes())
    data[8] = 0xFF  # first byte of the build id
    path.write_bytes(bytes(data))
    with pytest.raises(CacheUnusable):
        index_payload(tmp_path, digest)


def test_an_infinite_exact_value_travels_as_is(tmp_path: Path) -> None:
    import math
    import struct

    digest = _build(tmp_path, values=[[math.inf, -math.inf]] + [[1.0, 2.0]] * 4)
    build = index_payload(tmp_path, digest)["build"]
    got = exact_payload(tmp_path, digest, build, 0)
    assert struct.unpack("<2d", base64.b64decode(got["data"])) == (math.inf, -math.inf)


def test_the_exact_values_travel_as_f64_with_missing_as_nan(tmp_path: Path) -> None:
    import struct

    digest = _build(tmp_path, values=[[0.125, None]] + [[1.0, 2.0]] * 4)
    build = index_payload(tmp_path, digest)["build"]
    got = exact_payload(tmp_path, digest, build, 0)
    assert got["kind"] == "f64"
    a, b = struct.unpack("<2d", base64.b64decode(got["data"]))
    assert a == 0.125 and b != b  # NaN


@pytest.mark.parametrize("digest", ["../etc/passwd", "a" * 63, "A" * 64, "g" * 64, ""])
def test_a_digest_that_is_not_64_lowercase_hex_is_refused_before_any_path(
    tmp_path: Path, digest: str
) -> None:
    """The digest arrives from the browser in argv: it names a file, so it is
    checked to be exactly what cache_file produces."""
    with pytest.raises(ValueError, match="digest"):
        index_payload(tmp_path, digest)


def test_a_missing_cache_is_unusable_for_the_command_to_rebuild(tmp_path: Path) -> None:
    with pytest.raises(CacheUnusable):
        index_payload(tmp_path, KEY.digest())


def test_every_read_marks_the_cache_recently_used(tmp_path: Path) -> None:
    """The cap evicts the least recently used cache; atime is unreliable on NFS,
    so a read sets the mtime."""
    digest = _build(tmp_path)
    path = cache_file(tmp_path, KEY)
    os.utime(path, ns=(0, 0))
    build = index_payload(tmp_path, digest)["build"]
    assert path.stat().st_mtime_ns > 0
    for read in (
        lambda: page_payload(tmp_path, digest, build, [0]),
        lambda: exact_payload(tmp_path, digest, build, 0),
    ):
        os.utime(path, ns=(0, 0))
        read()
        assert path.stat().st_mtime_ns > 0
