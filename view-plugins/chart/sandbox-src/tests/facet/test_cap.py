"""The cache cap (plan-view-plugins-pr4 P3): the scratch disk is outside the
quota (Q12), so every build bounds `.home/.cache/views/` itself."""

import os
from pathlib import Path

from chart_view.facet import TMP_SUFFIX
from chart_view.facet.cap import enforce_cap

NOW = 1_000_000.0


def _file(root: Path, name: str, size: int, age_s: float) -> Path:
    path = root / name
    path.write_bytes(b"x" * size)
    t = NOW - age_s
    os.utime(path, (t, t))
    return path


def _names(root: Path) -> set[str]:
    return {p.name for p in root.iterdir()}


def test_under_the_cap_nothing_is_removed(tmp_path: Path) -> None:
    _file(tmp_path, "a.vcache", 40, 100)
    _file(tmp_path, "b.vcache", 40, 50)
    assert enforce_cap(tmp_path, cap_bytes=100, keep=None, now=NOW) == []
    assert _names(tmp_path) == {"a.vcache", "b.vcache"}


def test_over_the_cap_the_least_recently_used_go_first_until_it_fits(tmp_path: Path) -> None:
    _file(tmp_path, "old.vcache", 40, 300)
    _file(tmp_path, "mid.vcache", 40, 200)
    _file(tmp_path, "new.vcache", 40, 100)
    removed = enforce_cap(tmp_path, cap_bytes=90, keep=None, now=NOW)
    assert [p.name for p in removed] == ["old.vcache"]
    assert _names(tmp_path) == {"mid.vcache", "new.vcache"}


def test_the_cache_being_opened_is_kept_even_when_it_alone_is_over(tmp_path: Path) -> None:
    keep = _file(tmp_path, "open.vcache", 500, 999)  # oldest AND over the cap
    _file(tmp_path, "other.vcache", 40, 10)
    enforce_cap(tmp_path, cap_bytes=100, keep=keep, now=NOW)
    assert _names(tmp_path) == {"open.vcache"}


def test_a_dead_temp_file_is_removed_a_live_one_counted_but_left(tmp_path: Path) -> None:
    """A SIGKILLed build leaves its temp file; removing a LIVE build's temp file
    would fail that build's os.replace."""
    _file(tmp_path, f".a.vcache.dead{TMP_SUFFIX}", 10, 7200)
    _file(tmp_path, f".b.vcache.live{TMP_SUFFIX}", 60, 5)
    _file(tmp_path, "c.vcache", 50, 100)
    enforce_cap(tmp_path, cap_bytes=100, keep=None, now=NOW, tmp_grace_s=3600)
    # the dead one always goes; the live 60 bytes count, so c (50) must go too
    assert _names(tmp_path) == {f".b.vcache.live{TMP_SUFFIX}"}


def test_files_that_are_not_caches_are_never_touched_or_counted(tmp_path: Path) -> None:
    _file(tmp_path, "notes.txt", 1000, 99999)
    _file(tmp_path, "a.vcache", 40, 10)
    assert enforce_cap(tmp_path, cap_bytes=50, keep=None, now=NOW) == []
    assert _names(tmp_path) == {"notes.txt", "a.vcache"}


def test_a_missing_cache_dir_is_an_empty_one(tmp_path: Path) -> None:
    assert enforce_cap(tmp_path / "nope", cap_bytes=1, keep=None, now=NOW) == []


def test_a_file_removed_between_the_listing_and_its_stat_is_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    gone = _file(tmp_path, "gone.vcache", 60, 300)
    _file(tmp_path, "b.vcache", 60, 200)
    real_stat = Path.stat

    def racing_stat(self: Path, **kw):  # type: ignore[no-untyped-def]
        if self == gone:
            gone.unlink(missing_ok=True)
        return real_stat(self, **kw)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert enforce_cap(tmp_path, cap_bytes=100, keep=None, now=NOW) == []
    assert _names(tmp_path) == {"b.vcache"}


def test_a_file_another_process_removed_mid_sweep_is_skipped(tmp_path: Path, monkeypatch) -> None:
    _file(tmp_path, "a.vcache", 60, 300)
    _file(tmp_path, "b.vcache", 60, 200)
    real_unlink = Path.unlink

    def racing_unlink(self: Path, missing_ok: bool = False) -> None:
        real_unlink(self)  # someone else got there first ...
        real_unlink(self, missing_ok=missing_ok)  # ... then ours runs

    monkeypatch.setattr(Path, "unlink", racing_unlink)
    removed = enforce_cap(tmp_path, cap_bytes=100, keep=None, now=NOW)
    assert [p.name for p in removed] == ["a.vcache"]
