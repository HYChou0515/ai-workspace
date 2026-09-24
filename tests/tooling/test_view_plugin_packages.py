"""A view plugin's sandbox package carries its own tests under its own rootdir
(like `sample-tools/`), which this repo's `testpaths` never collects. Run them
here so CI does — a suite nobody runs guards nothing."""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PACKAGES = sorted(p.parent for p in (_REPO / "view-plugins").glob("*/*/pyproject.toml"))


def test_the_facet_cache_package_is_found() -> None:
    assert _REPO / "view-plugins" / "chart" / "facet-cache" in _PACKAGES


@pytest.mark.parametrize("package", _PACKAGES, ids=lambda p: p.name)
def test_view_plugin_package_suite_passes(package: Path) -> None:
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(package / "tests")],
        cwd=package,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
