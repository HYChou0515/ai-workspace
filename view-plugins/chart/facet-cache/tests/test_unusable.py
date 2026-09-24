"""The pager rebuilds a cache it cannot use (plan-view-plugins-pr4 P4), so every
way a cache can be unusable must surface as the one error it catches."""

from pathlib import Path

import pytest

from aiws_facet_cache import (
    CacheUnusable,
    CategoryScale,
    ContinuousScale,
    Group,
    read_index,
    read_records,
    write_cache,
)


def _write(path: Path, n: int = 3, key: str = "g") -> None:
    groups = [Group(key=(f"{key}{i}",), sort={}, values=[1.0, 2.0]) for i in range(n)]
    write_cache(
        path, scale=ContinuousScale(0.0, 2.0), facet=("g",), cells=2, layout={}, groups=groups
    )


def test_a_missing_cache_is_unusable(tmp_path: Path) -> None:
    with pytest.raises(CacheUnusable):
        read_index(tmp_path / "gone.vcache")


def test_a_file_of_another_format_is_unusable(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    _write(path)
    path.write_bytes(b"AIWSVC00" + path.read_bytes()[8:])
    with pytest.raises(CacheUnusable):
        read_index(path)


@pytest.mark.parametrize("keep", [4, 10, 20, -1])
def test_a_truncated_cache_is_unusable(tmp_path: Path, keep: int) -> None:
    """Cut in the magic, in the length field, in the header, and in the last
    record — caught
    by the index read alone, since a gallery sorts from the index before it
    reads any page."""
    path = tmp_path / "c.vcache"
    _write(path)
    path.write_bytes(path.read_bytes()[:keep])
    with pytest.raises(CacheUnusable):
        read_index(path)


def test_a_cache_removed_after_its_index_was_read_is_unusable(tmp_path: Path) -> None:
    """A reap between the index read and the page read."""
    path = tmp_path / "c.vcache"
    _write(path)
    index = read_index(path)
    path.unlink()
    with pytest.raises(CacheUnusable):
        read_records(path, index, 0, 1)


@pytest.mark.parametrize("n", [1, 5, 9], ids=["smaller", "same-size", "bigger"])
def test_a_cache_rebuilt_after_its_index_was_read_is_unusable(tmp_path: Path, n: int) -> None:
    """A concurrent rebuild swapped the file. Whatever its size, the old index's
    offsets no longer describe it, so no page may be read through them."""
    path = tmp_path / "c.vcache"
    _write(path, n=5, key="old")
    index = read_index(path)
    _write(path, n=n, key="new")
    with pytest.raises(CacheUnusable):
        read_records(path, index, 0, 1)


def test_a_directory_at_the_cache_path_is_unusable(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    path.mkdir()
    with pytest.raises(CacheUnusable):
        read_index(path)


def test_a_header_with_the_wrong_types_is_unusable(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    _write(path)
    data = path.read_bytes()
    path.write_bytes(
        data.replace(b'"cells":2', b'"cells":"2"').replace(
            data[8:12], (int.from_bytes(data[8:12], "little") + 2).to_bytes(4, "little")
        )
    )
    with pytest.raises(CacheUnusable):
        read_index(path)


def test_a_category_code_outside_the_labels_is_unusable(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    write_cache(
        path,
        scale=CategoryScale(["x", "y"]),
        facet=("g",),
        cells=1,
        layout={},
        groups=[Group(key=("a",), sort={}, values=["y"])],
    )
    path.write_bytes(path.read_bytes()[:-1] + bytes([7]))
    index = read_index(path)
    with pytest.raises(CacheUnusable):
        index.scale.decode(read_records(path, index, 0, 1)[0])


def test_a_failed_write_leaves_no_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    groups = [
        Group(key=("ok",), sort={}, values=[1.0, 2.0]),
        Group(key=("short",), sort={}, values=[1.0]),
    ]
    with pytest.raises(ValueError, match="short"):
        write_cache(
            path, scale=ContinuousScale(0.0, 2.0), facet=("g",), cells=2, layout={}, groups=groups
        )
    assert list(tmp_path.iterdir()) == []


def test_a_rewrite_that_fails_while_writing_keeps_the_old_cache_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past validation, a write can still fail (disk full): a reader must never
    see the half-written file in the old one's place. (A SIGKILL skips the
    cleanup and leaves the temp file; the LRU cap removes those — see
    ``TMP_SUFFIX``.)"""
    path = tmp_path / "c.vcache"
    _write(path, n=5)

    def disk_full(*_: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("aiws_facet_cache.os.replace", disk_full)
    with pytest.raises(OSError):
        _write(path, n=2)
    assert [g.key for g in read_index(path).groups] == [(f"g{i}",) for i in range(5)]
    assert list(tmp_path.iterdir()) == [path]


def test_a_rewrite_replaces_the_old_cache_whole(tmp_path: Path) -> None:
    path = tmp_path / "c.vcache"
    _write(path, n=5)
    _write(path, n=2)
    assert [g.key for g in read_index(path).groups] == [("g0",), ("g1",)]
    assert list(tmp_path.iterdir()) == [path]
