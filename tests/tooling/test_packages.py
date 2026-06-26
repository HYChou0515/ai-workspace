"""`workspace_app.tooling.packages` is the deployment's PACKAGES dict — a
workspace-level tool registry (Apps use packages via allowed_tools). Pure data
module; this test exercises it so coverage sees the module-level definitions.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_python_stack_ships_office_libs():
    """The `python-stack` venv carrier bundles the office document stack
    (#252) alongside its data-science deps, so the agent's raw
    `exec(["python", ...])` calls can read/write Excel + PowerPoint files:

    - openpyxl: read/write .xlsx (also pandas' .xlsx engine)
    - XlsxWriter: write .xlsx with formatting / charts
    - python-pptx: read/write .pptx

    Mirrors `test_image.py`'s contract check on the data stack — guards the
    pinned dependency list rather than building the bundle.
    """
    pyproject = _REPO / "sample-tools" / "python-stack" / "pyproject.toml"
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    names = {d.split(">=")[0].split("==")[0].split("[")[0].strip().lower() for d in deps}
    for lib in ("python-pptx", "openpyxl", "xlsxwriter"):
        assert lib in names, f"{lib} not pinned in python-stack/pyproject.toml"


def test_packages_dict_exists_with_expected_entries():
    """The demo packages are registered. Sentinel: a real deploy swaps
    this dict for its own packages; here we lock the demo shape.

    - data-fetch / csv-column-summary: reusable CLI sample packages
    - python-stack: venv carrier (no CLI; provisions pandas/numpy/scipy/
      matplotlib into the sandbox via the jail's `python` shim)
    - rca-tools: in-house multi-command package (source gitignored —
      clones without it still build via the skip-missing branch in
      scripts/prebuild_tools.py)
    """
    from workspace_app.tooling.packages import PACKAGES, PREBUILT_DIR, SOURCE_DIR

    assert set(PACKAGES) == {
        "data-fetch",
        "csv-column-summary",
        "python-stack",
        "rca-tools",
    }
    # Each entry is a source directory path (host-side).
    for name, src in PACKAGES.items():
        assert src == SOURCE_DIR / name
    # PREBUILT_DIR is a Path; defaults to <repo>/.workspace-tools when env unset.
    assert isinstance(PREBUILT_DIR, Path)


def test_prebuilt_dir_honours_env_override(monkeypatch, tmp_path: Path):
    """`WORKSPACE_TOOLS_DIR` env var overrides the default location — the deploy
    knob for moving the bundle root."""
    import importlib

    monkeypatch.setenv("WORKSPACE_TOOLS_DIR", str(tmp_path / "custom"))
    import workspace_app.tooling.packages as tp

    importlib.reload(tp)
    try:
        assert tmp_path / "custom" == tp.PREBUILT_DIR
    finally:
        monkeypatch.delenv("WORKSPACE_TOOLS_DIR", raising=False)
        importlib.reload(tp)
