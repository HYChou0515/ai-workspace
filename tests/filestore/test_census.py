"""#407: the durable-store census — a cheap group-by over the whole
WorkspaceFile table (total rows, distinct workspaces, largest single workspace)
that feeds the ws_census telemetry trend. Both workspace-backing stores expose
it, so the census reads the same on either backend."""

from __future__ import annotations

from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec


async def test_specstar_census_counts_rows_workspaces_and_max():
    fs = SpecstarFileStore(make_spec(default_user="u"))
    await fs.write("wsA", "/a.txt", b"A")
    await fs.write("wsA", "/b.txt", b"BB")
    await fs.write("wsB", "/c.txt", b"C")
    assert await fs.census() == {
        "total_workspacefile_rows": 3,
        "n_workspaces": 2,
        "max_files_per_ws": 2,
    }


async def test_specstar_census_empty_is_all_zero():
    fs = SpecstarFileStore(make_spec(default_user="u"))
    assert await fs.census() == {
        "total_workspacefile_rows": 0,
        "n_workspaces": 0,
        "max_files_per_ws": 0,
    }


async def test_memory_census_matches_specstar_shape():
    fs = MemoryFileStore()
    await fs.write("wsA", "/a", b"A")
    await fs.write("wsA", "/b", b"B")
    await fs.write("wsB", "/c", b"C")
    assert await fs.census() == {
        "total_workspacefile_rows": 3,
        "n_workspaces": 2,
        "max_files_per_ws": 2,
    }


async def test_memory_census_ignores_emptied_workspaces():
    fs = MemoryFileStore()
    await fs.write("wsA", "/a", b"A")
    await fs.delete("wsA", "/a")  # the workspace key lingers with {} — not a live row
    assert await fs.census() == {
        "total_workspacefile_rows": 0,
        "n_workspaces": 0,
        "max_files_per_ws": 0,
    }
