"""Tests for output-path resolution (timestamped default, explicit override)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sci_plot.framework.output import default_filename, resolve_output


def test_default_filename_is_timestamped_not_hashed():
    name = default_filename("box_scatter", datetime(2026, 6, 27, 14, 35, 1, 204193))
    assert name == "box_scatter_20260627-143501-204193.png"


def test_resolve_output_default_under_charts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = resolve_output("wafermap", None, datetime(2026, 1, 1, 0, 0, 0, 1))
    assert out.parent.name == "charts"
    assert out.parent.is_dir()  # parent created
    assert out.name.startswith("wafermap_")


def test_resolve_output_explicit_creates_parent(tmp_path: Path):
    target = tmp_path / "sub" / "dir" / "p.png"
    out = resolve_output("box_scatter", str(target), datetime(2026, 1, 1))
    assert out == target
    assert out.parent.is_dir()
