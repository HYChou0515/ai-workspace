"""Coverage fill for prebuild.build_package_uvrun: rebuilding over an existing
bundle unlinks the stale `project` symlink before re-creating it (prebuild.py
line 204).
"""

from __future__ import annotations

from pathlib import Path


def test_uvrun_bundle_replaces_an_existing_project_symlink(tmp_path: Path, monkeypatch):
    """A second build into the same dst must unlink the old `project` link
    (prebuild.py line 204) and re-point it at the (possibly new) source."""
    from workspace_app.tooling import prebuild

    monkeypatch.setattr(prebuild, "_dump_schemas", lambda launch, dst: None)

    old_src = tmp_path / "old" / "data-fetch"
    old_src.mkdir(parents=True)
    (old_src / "pyproject.toml").write_text(
        '[project]\nname = "data-fetch"\nversion = "0"\n[project.scripts]\ndata-fetch = "x:y"\n'
    )
    dst = tmp_path / "dst"

    # First build creates `dst/project` → old_src.
    prebuild.build_package_uvrun(name="data-fetch", source=old_src, dst=dst)
    link = dst / "project"
    assert link.is_symlink()
    assert link.resolve() == old_src.resolve()

    # Second build into the SAME dst, pointing at a new source — the existing
    # symlink must be unlinked first, not raise FileExistsError.
    new_src = tmp_path / "new" / "data-fetch"
    new_src.mkdir(parents=True)
    (new_src / "pyproject.toml").write_text(
        '[project]\nname = "data-fetch"\nversion = "1"\n[project.scripts]\ndata-fetch = "x:y"\n'
    )
    prebuild.build_package_uvrun(name="data-fetch", source=new_src, dst=dst)

    assert link.is_symlink()
    assert link.resolve() == new_src.resolve()  # re-pointed at the new source
