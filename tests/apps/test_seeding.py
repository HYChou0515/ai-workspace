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
