"""§B.T6 — `workspace_app.rca.tool_packages` is the demo deployment's
PACKAGES dict. Pure data module; this test just exercises it so coverage
sees the module-level definitions (it has no callable surface beyond
import).
"""

from __future__ import annotations

from pathlib import Path


def test_packages_dict_exists_with_expected_entries():
    """The two sample packages are registered. Sentinel: a real deploy
    swaps this dict for its own packages; here we lock the demo shape."""
    from workspace_app.rca.tool_packages import PACKAGES, PREBUILT_DIR, SOURCE_DIR

    assert set(PACKAGES) == {"data-fetch", "csv-column-summary"}
    # Each entry is a source directory path (host-side).
    for name, src in PACKAGES.items():
        assert src == SOURCE_DIR / name
    # PREBUILT_DIR is a Path; defaults to <repo>/.rca-tools when env unset.
    assert isinstance(PREBUILT_DIR, Path)


def test_prebuilt_dir_honours_env_override(monkeypatch, tmp_path: Path):
    """`RCA_TOOLS_DIR` env var overrides the default location — the deploy
    knob for moving the bundle root."""
    import importlib

    monkeypatch.setenv("RCA_TOOLS_DIR", str(tmp_path / "custom"))
    import workspace_app.rca.tool_packages as tp

    importlib.reload(tp)
    try:
        assert tmp_path / "custom" == tp.PREBUILT_DIR
    finally:
        monkeypatch.delenv("RCA_TOOLS_DIR", raising=False)
        importlib.reload(tp)
