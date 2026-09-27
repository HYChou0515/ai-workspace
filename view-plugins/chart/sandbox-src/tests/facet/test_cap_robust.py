"""What the cap meets on a real scratch disk: other spellings of the same
path, clocks that disagree, and entries that are not regular files."""

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


def test_keep_is_the_same_file_however_its_path_is_spelled(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    _file(real, "open.vcache", 500, 999)
    keep = tmp_path / "link" / "open.vcache"  # another spelling of the same file
    enforce_cap(real, cap_bytes=100, keep=keep, now=NOW)
    assert (real / "open.vcache").exists()


def test_the_default_clock_is_the_file_systems_not_the_pods(tmp_path: Path, monkeypatch) -> None:
    """On NFS the server stamps mtimes. Against a pod clock two hours off, a
    build's fresh temp file would look two hours old and be swept, failing that
    build's os.replace; read "now" from the same file system instead."""
    import time

    live = tmp_path / f".x.vcache.a{TMP_SUFFIX}"
    live.write_bytes(b"x" * 10)  # stamped by the file system, just now
    real = time.time()
    monkeypatch.setattr(time, "time", lambda: real + 7200)  # the pod's clock is off
    enforce_cap(tmp_path, cap_bytes=100, keep=None, tmp_grace_s=3600)
    assert live.exists()
    assert {p.name for p in tmp_path.iterdir()} == {live.name}  # and no stray probe


def test_a_temp_file_stamped_far_in_the_future_is_treated_as_dead(tmp_path: Path) -> None:
    """No live build stamps its temp file more than the grace period ahead of
    the file system's own clock; left counted, it would evict every other cache
    on every build until the clock caught up with it."""
    _file(tmp_path, f".x.vcache.a{TMP_SUFFIX}", 90, -7200)  # two hours ahead
    _file(tmp_path, "c.vcache", 50, 100)
    enforce_cap(tmp_path, cap_bytes=100, keep=None, now=NOW, tmp_grace_s=3600)
    assert {p.name for p in tmp_path.iterdir()} == {"c.vcache"}


def test_entries_that_are_not_regular_files_are_left_alone(tmp_path: Path) -> None:
    (tmp_path / "dir.vcache").mkdir()
    (tmp_path / "dangling.vcache").symlink_to(tmp_path / "nowhere")
    target = _file(tmp_path, "target.bin", 400, 10)
    (tmp_path / "link.vcache").symlink_to(target)
    _file(tmp_path, "a.vcache", 60, 300)
    removed = enforce_cap(tmp_path, cap_bytes=50, keep=None, now=NOW)
    assert [p.name for p in removed] == ["a.vcache"]
    assert {p.name for p in tmp_path.iterdir()} == {
        "dir.vcache",
        "dangling.vcache",
        "target.bin",
        "link.vcache",
    }


def test_a_cache_dir_that_is_a_file_is_an_empty_one(tmp_path: Path) -> None:
    root = tmp_path / "views"
    root.write_text("not a dir")
    assert enforce_cap(root, cap_bytes=1, keep=None, now=NOW) == []


def test_a_cache_the_cap_cannot_remove_is_skipped_and_the_sweep_goes_on(
    tmp_path: Path, monkeypatch
) -> None:
    _file(tmp_path, "stuck.vcache", 60, 300)
    _file(tmp_path, "b.vcache", 60, 200)
    _file(tmp_path, "c.vcache", 60, 100)
    real_unlink = Path.unlink

    def refuse_stuck(self: Path, missing_ok: bool = False) -> None:
        if self.name == "stuck.vcache":
            raise PermissionError(13, "Permission denied")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_stuck)
    removed = enforce_cap(tmp_path, cap_bytes=100, keep=None, now=NOW)
    assert [p.name for p in removed] == ["b.vcache", "c.vcache"]


def test_a_dead_temp_file_the_cap_cannot_remove_is_not_reported_removed(
    tmp_path: Path, monkeypatch
) -> None:
    stuck = _file(tmp_path, f".x.vcache.a{TMP_SUFFIX}", 10, 7200)
    real_unlink = Path.unlink

    def refuse(self: Path, missing_ok: bool = False) -> None:
        if self == stuck:
            raise PermissionError(13, "Permission denied")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    assert enforce_cap(tmp_path, cap_bytes=100, keep=None, now=NOW, tmp_grace_s=3600) == []
    assert stuck.exists()


def test_a_missing_cache_dir_needs_no_clock_of_its_own(tmp_path: Path) -> None:
    """With no dir to probe, "now" falls back to the pod's clock; there is
    nothing in it to age anyway."""
    assert enforce_cap(tmp_path / "nope", cap_bytes=1, keep=None) == []
