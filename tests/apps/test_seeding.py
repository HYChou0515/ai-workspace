from workspace_app.apps.seeding import seed_item
from workspace_app.filestore.memory import MemoryFileStore


async def test_seed_item_writes_substituted_profile_files():
    """Seeding a profile copies its files into the item's FileStore: `.tpl`
    files are `$var`-substituted with the item's case (and lose the suffix);
    `_profile.json` / `_prompt.md` are profile metadata and are NOT seeded."""
    fs = MemoryFileStore()
    case = {
        "title": "Oven drift",
        "owner": "alice",
        "severity": "P1",
        "status": "triaging",
        "product": "MX-7",
        "topics": "reflow",
        "description": "voids on lot 25-W14",
    }
    written = await seed_item(fs, "rca/abc", "rca", "default", case)

    assert "/SOP.md" in written  # SOP.md.tpl → /SOP.md
    assert "/_prompt.md" not in written
    assert "/_profile.json" not in written
    # `.agent/` (like `.skill/`) is read straight from the package. A copy in the
    # workspace would be a SECOND definition of the same sub-agent, free to drift
    # from the shipped one — and rca/default ships `log-digger`, so without the
    # skip this profile really would seed one.
    assert not [p for p in written if p.startswith("/.agent/")], written

    sop = (await fs.read("rca/abc", "/SOP.md")).decode()
    assert "Oven drift" in sop  # $title substituted
    assert "P1" in sop  # $severity substituted


async def test_seed_smt_profile_seeds_notebooks_and_omits_canvas_and_5why():
    """#89 P8 closeout T1: the ported smt-reflow-example seeds its notebooks +
    sample data but NOT the dropped 5-Why / fishbone.canvas files."""
    fs = MemoryFileStore()
    case = {
        "title": "Reflow voids",
        "owner": "alice",
        "severity": "P1",
        "status": "triaging",
        "product": "MX-7",
        "topics": "reflow",
        "description": "voids",
    }
    written = await seed_item(fs, "rca/smt", "rca", "smt-reflow-example", case)

    assert any(w.endswith("/drift.ipynb") for w in written)
    assert any(w.endswith("/pareto.ipynb") for w in written)
    assert any("/data/" in w for w in written)
    assert not any("canvas" in w.lower() or "5-why" in w.lower() for w in written)


async def test_a_declaring_profile_seeds_the_files_the_sync_reads():
    """#775 — the declaration only works if it reaches the WORKSPACE.

    `ensure_project_env` looks for `pyproject.toml` in the sandbox, which is
    restored from what seeding wrote. A profile whose declaration stayed in the
    package would sync nothing and report nothing: the feature would be off,
    and off in the way that leaves no trace.

    The lock ships too. `uv sync --frozen` has nothing to install from without
    it, so a pyproject on its own is a failed cold start rather than a
    half-configured one.
    """
    fs = MemoryFileStore()

    written = await seed_item(fs, "playground/x", "playground", "pydeps", {})

    assert "/pyproject.toml" in written
    assert "/uv.lock" in written
    assert "/_prompt.md" not in written, "the prompt is read from the package, never copied"

    declared = (await fs.read("playground/x", "/pyproject.toml")).decode()
    # The carrier is REPLACED, not layered under, so a profile that declares
    # anything must carry the stack the carrier would have given it — or an
    # author adding one package silently loses pandas.
    # The reference set is `sample-tools/python-stack/pyproject.toml` — the
    # CARRIER. The first version of this list was taken from
    # `docker/Dockerfile.workspace` instead, an image nothing starts, so the
    # office half was missing from both the example and this assertion and
    # `import openpyxl` broke in the one profile shipped to demonstrate that it
    # would not.
    for pkg in (
        "ipykernel",
        "numpy",
        "pandas",
        "matplotlib",
        "scipy",
        "openpyxl",
        "XlsxWriter",
        "python-pptx",
    ):
        assert pkg in declared, f"{pkg} must survive the carrier being replaced"
